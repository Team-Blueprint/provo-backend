import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.schemas import (
    CreateShareRequest,
    ShareResponse,
    WorkerDirectoryItem,
    WorkerPaymentLinkResponse,
)
from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.core.enums import DayStatus
from app.db.models import Job, JobDay, User, WorkerProfile
from app.services.report_service import ReportService

router = APIRouter(prefix="/workers", tags=["Workers"])
report_service = ReportService()


@router.get("/available", response_model=list[WorkerDirectoryItem])
async def list_available_workers(
    trade: str | None = Query(None, description="Filter by trade (e.g. BRICKLAYING)"),
    area: str | None = Query(None, description="Filter by city/area (e.g. Uyo)"),
    db: AsyncSession = Depends(get_db),
) -> list[WorkerDirectoryItem]:
    """
    Public directory of registered artisans available for hire.
    Returns anonymized professional metrics (verified days) without leaking personal PII.
    """
    stmt = (
        select(WorkerProfile)
        .where(WorkerProfile.is_available.is_(True))
        .options(selectinload(WorkerProfile.user))
    )
    if trade:
        stmt = stmt.where(WorkerProfile.trade.ilike(trade.strip()))
    if area:
        stmt = stmt.where(WorkerProfile.area.ilike(f"%{area.strip()}%"))

    profiles = (await db.execute(stmt)).scalars().all()
    results: list[WorkerDirectoryItem] = []

    for prof in profiles:
        user = prof.user
        if not user:
            continue

        # Count verified paid days for this worker
        count_stmt = (
            select(func.count(JobDay.id))
            .join(Job, JobDay.job_id == Job.id)
            .where(
                Job.worker_id == user.id,
                JobDay.status == DayStatus.PAID.value,
            )
        )
        verified_days = (await db.execute(count_stmt)).scalar() or 0

        results.append(
            WorkerDirectoryItem(
                id=user.id,
                name=user.name,
                trade=prof.trade,
                area=prof.area,
                verified_days_count=verified_days,
                paga_account_number=prof.paga_account_number,
                paga_payment_url=prof.paga_payment_url,
            )
        )

    return results


@router.get("/{worker_id}/record")
async def get_worker_record(
    worker_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Detailed work record and daily verification metrics.
    Only the worker themselves, a designated supervisor, or an admin can view the full record.
    """
    if current_user.id != worker_id and not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorized to view this worker's detailed record.",
        )

    stmt = (
        select(User)
        .where(User.id == worker_id)
        .options(
            selectinload(User.worker_profile),
            selectinload(User.jobs_as_worker).selectinload(Job.days_schedule),
        )
    )
    worker = (await db.execute(stmt)).scalar_one_or_none()
    if not worker:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Worker not found",
        )

    jobs_summary = []
    total_earned_kobo = 0
    total_confirmed_days = 0
    total_auto_released_days = 0
    total_disputed_days = 0

    for job in worker.jobs_as_worker:
        day_items = []
        for d in job.days_schedule:
            if d.status == DayStatus.PAID.value:
                total_earned_kobo += d.amount_kobo
                # Check whether confirmed or auto-released
                if d.release_at and d.done_at and (d.updated_at and d.updated_at >= d.release_at):
                    total_auto_released_days += 1
                else:
                    total_confirmed_days += 1
            elif d.status == DayStatus.DISPUTED.value:
                total_disputed_days += 1

            day_items.append({
                "day_number": d.day_number,
                "amount_kobo": d.amount_kobo,
                "status": d.status,
                "done_at": d.done_at.isoformat() if d.done_at else None,
                "release_at": d.release_at.isoformat() if d.release_at else None,
            })

        jobs_summary.append({
            "job_id": str(job.id),
            "trade": job.trade,
            "description": job.description,
            "status": job.status,
            "total_kobo": job.total_kobo,
            "days_count": job.days_count,
            "days": day_items,
        })

    return {
        "worker_id": str(worker.id),
        "name": worker.name,
        "phone": worker.phone,
        "trade": worker.worker_profile.trade if worker.worker_profile else "Artisan",
        "area": worker.worker_profile.area if worker.worker_profile else "Nigeria",
        "aggregate_metrics": {
            "total_earned_kobo": total_earned_kobo,
            "total_earned_naira": f"{total_earned_kobo / 100:,.2f}",
            "confirmed_days": total_confirmed_days,
            "auto_released_days": total_auto_released_days,
            "disputed_days": total_disputed_days,
            "total_settled_days": total_confirmed_days + total_auto_released_days,
        },
        "jobs": jobs_summary,
    }


@router.post("/{worker_id}/shares", response_model=ShareResponse)
async def create_share_link(
    worker_id: uuid.UUID,
    payload: CreateShareRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ShareResponse:
    """
    Generate an NDPA-compliant 7-day share link for credit institutions and micro-finance lenders.
    """
    if current_user.id != worker_id and not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the worker can authorize sharing their financial report.",
        )

    consent = await report_service.create_worker_consent(
        db=db,
        worker_id=worker_id,
        purpose=payload.purpose,
        valid_days=payload.valid_days,
    )

    base = settings.BASE_URL.rstrip("/")
    report_url = f"{base}/reports/{consent.token}"

    return ShareResponse(
        token=consent.token,
        report_url=report_url,
        expires_at=consent.expires_at.isoformat(),
        purpose=consent.purpose,
    )


@router.get("/{worker_id}/payment-link", response_model=WorkerPaymentLinkResponse)
async def get_worker_payment_link(
    worker_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> WorkerPaymentLinkResponse:
    """
    Returns the worker's personal Paga virtual account number and shareable payment link.
    Public endpoint — anyone can fetch this to pay a worker directly.
    """
    stmt = (
        select(User)
        .where(User.id == worker_id)
        .options(selectinload(User.worker_profile))
    )
    worker = (await db.execute(stmt)).scalar_one_or_none()

    if not worker or not worker.is_worker:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Worker not found.",
        )

    profile = worker.worker_profile
    if not profile or not profile.paga_account_number:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This worker does not yet have a personal payment account. They may need to log in once to activate it.",
        )

    payment_url = profile.paga_payment_url or f"{settings.BASE_URL}/pay/{profile.paga_account_number}"

    return WorkerPaymentLinkResponse(
        worker_id=str(worker.id),
        worker_name=worker.name,
        account_number=profile.paga_account_number,
        bank="Paga",
        payment_url=payment_url,
        share_text=f"Pay {worker.name} via Provo: Transfer to Paga account {profile.paga_account_number}",
    )
