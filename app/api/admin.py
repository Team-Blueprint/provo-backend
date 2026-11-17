import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.schemas import DisputeResolveRequest
from app.core.database import get_db
from app.core.dependencies import require_admin
from app.core.enums import DayStatus, DisputeStatus, LedgerEntryType, PayoutStatus
from app.db.models import Dispute, Job, JobDay, LedgerEntry, Payout, User
from app.services.release_service import ReleaseService

router = APIRouter(prefix="/admin", tags=["Admin Operations"])
release_service = ReleaseService()


@router.post("/disputes/{dispute_id}/resolve")
async def resolve_dispute(
    dispute_id: uuid.UUID,
    payload: DisputeResolveRequest,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Admin manual resolution of a frozen dispute.
    Supports releasing to worker, refunding to client, or splitting funds.
    Enforces ledger balance invariant.
    """
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only administrators can resolve disputes.",
        )

    stmt = (
        select(Dispute)
        .where(Dispute.id == dispute_id)
        .options(
            selectinload(Dispute.day).selectinload(JobDay.job),
        )
    )
    dispute = (await db.execute(stmt)).scalar_one_or_none()
    if not dispute:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dispute not found",
        )

    if dispute.status != DisputeStatus.OPEN.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Dispute is already {dispute.status}",
        )

    day: JobDay = dispute.day
    job: Job = day.job
    action = payload.action.upper()

    if action == "RELEASE_TO_WORKER":
        # Full day release to worker
        day.status = DayStatus.PAID.value
        ledger = LedgerEntry(
            job_id=job.id,
            day_id=day.id,
            kind=LedgerEntryType.RELEASED.value,
            amount_kobo=day.amount_kobo,
        )
        db.add(ledger)

        # Enqueue outbox payout if not already queued
        payout_stmt = select(Payout).where(Payout.day_id == day.id)
        existing_payout = (await db.execute(payout_stmt)).scalar_one_or_none()
        if not existing_payout:
            payout = Payout(
                day_id=day.id,
                amount_kobo=day.amount_kobo,
                idempotency_ref=f"PAYOUT-DISPUTE-{day.id}-{uuid.uuid4().hex[:8]}",
                status=PayoutStatus.PENDING.value,
            )
            db.add(payout)

    elif action == "REFUND_TO_CLIENT":
        # Refund day funds back to client
        day.status = DayStatus.CANCELLED.value
        ledger = LedgerEntry(
            job_id=job.id,
            day_id=day.id,
            kind=LedgerEntryType.REFUNDED.value,
            amount_kobo=day.amount_kobo,
        )
        db.add(ledger)

    elif action == "SPLIT":
        worker_kobo = payload.worker_amount_kobo or 0
        client_kobo = payload.client_amount_kobo or 0
        if worker_kobo + client_kobo != day.amount_kobo:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Split amounts ({worker_kobo} + {client_kobo}) must exactly equal day total ({day.amount_kobo})",
            )

        day.status = DayStatus.PAID.value
        if worker_kobo > 0:
            db.add(
                LedgerEntry(
                    job_id=job.id,
                    day_id=day.id,
                    kind=LedgerEntryType.RELEASED.value,
                    amount_kobo=worker_kobo,
                )
            )
            payout = Payout(
                day_id=day.id,
                amount_kobo=worker_kobo,
                idempotency_ref=f"PAYOUT-SPLIT-W-{day.id}-{uuid.uuid4().hex[:8]}",
                status=PayoutStatus.PENDING.value,
            )
            db.add(payout)

        if client_kobo > 0:
            db.add(
                LedgerEntry(
                    job_id=job.id,
                    day_id=day.id,
                    kind=LedgerEntryType.REFUNDED.value,
                    amount_kobo=client_kobo,
                )
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid action: {action}. Must be RELEASE_TO_WORKER, REFUND_TO_CLIENT, or SPLIT.",
        )

    dispute.status = DisputeStatus.RESOLVED.value
    dispute.resolution = f"[{action}] {payload.resolution_note}"
    dispute.resolved_by = current_user.id

    await db.commit()

    # Check if this settles the overall job
    await release_service.check_and_settle_job(db, job.id)

    return {
        "dispute_id": str(dispute.id),
        "status": dispute.status,
        "action": action,
        "resolution": dispute.resolution,
        "day_id": str(day.id),
        "day_status": day.status,
    }


@router.get("/outbox")
async def inspect_outbox(
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Admin inspects pending, processing, or failed payout outbox records."""

    stmt = select(Payout).order_by(Payout.created_at.desc()).limit(50)
    payouts = (await db.execute(stmt)).scalars().all()

    return [
        {
            "id": str(p.id),
            "day_id": str(p.day_id),
            "amount_kobo": p.amount_kobo,
            "idempotency_ref": p.idempotency_ref,
            "status": p.status,
            "attempts": p.attempts,
            "created_at": p.created_at.isoformat(),
        }
        for p in payouts
    ]
