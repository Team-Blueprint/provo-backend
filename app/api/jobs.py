import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.schemas import (
    AssignSupervisorRequest,
    AssignWorkerRequest,
    DisputeRequest,
    JobCreateRequest,
    JobDayResponse,
    JobResponse,
    ReceiptResponse,
    SupervisorConfirmDayRequest,
    SupervisorDayScheduleItem,
    SupervisorDisputeDayRequest,
    SupervisorJobViewResponse,
)
from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.core.enums import JobStatus, TradeType
from app.db.models import Receipt, User
from app.services.funding_service import FundingService
from app.services.job_service import JobService
from app.services.release_service import ReleaseService

router = APIRouter(prefix="/jobs", tags=["Jobs & Escrow"])
logger = logging.getLogger(__name__)
job_service = JobService()
funding_service = FundingService()
release_service = ReleaseService()


@router.post("", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def create_job(
    payload: JobCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Creates an escrow job draft with daily pay schedule.
    Supports single artisan or multi-artisan daily breakdowns via days_breakdown.
    """
    try:
        breakdown_dicts = (
            [item.model_dump() for item in payload.days_breakdown]
            if payload.days_breakdown
            else None
        )
        job = await job_service.create_job(
            db=db,
            client_id=current_user.id,
            trade=payload.trade.value,
            description=payload.description,
            total_kobo=payload.total_kobo,
            days=payload.days,
            worker_id=payload.worker_id,
            supervisor_id=payload.supervisor_id,
            days_breakdown=breakdown_dicts,
        )

        # If auto_fund_request is requested (default True), immediately request Paga NUBAN
        if payload.auto_fund_request:
            try:
                await funding_service.initiate_job_escrow(db=db, job_id=job.id)
            except Exception as exc:
                logger.warning("Auto fund request on create failed: %s", exc)

        return await job_service.get_job_by_id(db, job.id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("", response_model=list[JobResponse])
async def list_jobs(
    status: JobStatus | None = None,
    trade: TradeType | None = None,
    worker_id: uuid.UUID | None = None,
    client_id: uuid.UUID | None = None,
    supervisor_id: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Lists jobs with filters (status, trade, worker_id, client_id, supervisor_id) and pagination.
    Admins can see all jobs; other users see jobs they are associated with or unassigned marketplace jobs.
    """
    return await job_service.list_jobs(
        db=db,
        user_id=current_user.id,
        is_admin=current_user.is_admin,
        status=status,
        trade=trade,
        worker_id=worker_id,
        client_id=client_id,
        supervisor_id=supervisor_id,
        limit=min(limit, 100),
        offset=offset,
    )


@router.post("/{job_id}/assign-worker", response_model=JobResponse)
async def assign_worker(
    job_id: uuid.UUID,
    payload: AssignWorkerRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Assign an artisan to an existing unassigned job.
    Only the job's employer can call this. Job must be in DRAFT, AWAITING_FUNDING, or FUNDED status.
    """
    try:
        job = await job_service.assign_worker_to_job(
            db=db,
            job_id=job_id,
            actor_id=current_user.id,
            worker_phone=payload.worker_phone,
            worker_id=payload.worker_id,
        )
        await db.commit()
        return await job_service.get_job_by_id(db, job.id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/{job_id}/supervisor", response_model=JobResponse)
async def assign_supervisor(
    job_id: uuid.UUID,
    payload: AssignSupervisorRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Assign an invited site supervisor by name and phone number.
    Generates a unique supervisor_token if not already set.
    Only the job's employer or an admin can perform this.
    """
    try:
        job = await job_service.assign_supervisor(
            db=db,
            job_id=job_id,
            actor_id=current_user.id,
            supervisor_name=payload.supervisor_name,
            supervisor_phone=payload.supervisor_phone,
        )
        await db.commit()
        return await job_service.get_job_by_id(db, job.id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/{job_id}/supervisor-view", response_model=SupervisorJobViewResponse)
async def get_supervisor_view(
    job_id: uuid.UUID,
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Public token-gated endpoint for site supervisors.
    Provides milestone breakdown and artisan info with zero client PII.
    """
    from app.db.models import Job
    stmt = (
        select(Job)
        .where(Job.id == job_id)
        .options(
            selectinload(Job.days_schedule),
            selectinload(Job.worker),
        )
    )
    job = (await db.execute(stmt)).scalar_one_or_none()
    if not job or not token or not job.supervisor_token or token.strip() != job.supervisor_token:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found or invalid token")

    worker_name = job.worker.name if job.worker else None
    schedule_items = [
        SupervisorDayScheduleItem(
            id=d.id,
            day_number=d.day_number,
            amount_kobo=d.amount_kobo,
            status=d.status,
            done_at=d.done_at,
            release_at=d.release_at,
            proof_photo=d.proof_photo,
        )
        for d in job.days_schedule
    ]

    return SupervisorJobViewResponse(
        id=job.id,
        trade=job.trade,
        description=job.description,
        total_kobo=job.total_kobo,
        days_count=job.days,
        status=job.status,
        worker_name=worker_name,
        supervisor_name=job.supervisor_name,
        supervisor_phone=job.supervisor_phone,
        days_schedule=schedule_items,
    )


@router.post("/{job_id}/days/{day_number}/supervisor-confirm", response_model=JobDayResponse)
async def supervisor_confirm_day(
    job_id: uuid.UUID,
    day_number: int,
    token: str,
    payload: SupervisorConfirmDayRequest = SupervisorConfirmDayRequest(),
    db: AsyncSession = Depends(get_db),
):
    """
    Supervisor confirms a day's work using their unique token.
    Optionally attaches a proof photograph and triggers daily payout release.
    """
    try:
        day = await release_service.supervisor_confirm_day(
            db=db,
            job_id=job_id,
            day_number=day_number,
            token=token,
            phone=payload.phone,
            proof_photo=payload.proof_photo,
        )
        await db.commit()
        return day
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except LookupError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/{job_id}/days/{day_number}/supervisor-dispute", response_model=JobDayResponse)
async def supervisor_dispute_day(
    job_id: uuid.UUID,
    day_number: int,
    token: str,
    payload: SupervisorDisputeDayRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Supervisor disputes a day's work using their unique token.
    Freezes the 24-hour auto-release countdown and records the dispute.
    """
    try:
        day = await release_service.supervisor_dispute_day(
            db=db,
            job_id=job_id,
            day_number=day_number,
            token=token,
            reason=payload.reason,
        )
        await db.commit()
        return day
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except LookupError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retrieves job details, daily breakdown, and escrow status."""
    job = await job_service.get_job_by_id(db, job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    is_party = (
        job.client_id == current_user.id
        or job.worker_id == current_user.id
        or job.supervisor_id == current_user.id
        or current_user.is_admin
    )
    if not is_party:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied to this job")
    return job


@router.post("/{job_id}/fund-request", response_model=JobResponse)
async def initiate_fund_request(
    job_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Generates one-time Paga payment link and dedicated NUBAN account number for client funding.
    Transitions job status to AWAITING_FUNDING.
    """
    job = await job_service.get_job_by_id(db, job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if job.client_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the job client can request funding")

    try:
        await funding_service.initiate_job_escrow(db=db, job_id=job_id)
        return await job_service.get_job_by_id(db, job_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/{job_id}/days/{day_number}/done", response_model=JobDayResponse)
async def report_day_done(
    job_id: uuid.UUID,
    day_number: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Artisan reports a day's work as done.
    Starts the 24-hour review window.
    """
    try:
        day = await release_service.report_day_done(
            db=db,
            job_id=job_id,
            day_number=day_number,
            worker_user_id=current_user.id,
        )
        return day
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/{job_id}/days/{day_number}/confirm", response_model=JobDayResponse)
async def confirm_day(
    job_id: uuid.UUID,
    day_number: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Client or designated supervisor confirms daily work.
    Releases daily pay and mints receipt if final day.
    """
    try:
        day, _payout = await release_service.confirm_day(
            db=db,
            job_id=job_id,
            day_number=day_number,
            actor_user_id=current_user.id,
        )
        return day
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/{job_id}/days/{day_number}/dispute", response_model=JobDayResponse)
async def dispute_day(
    job_id: uuid.UUID,
    day_number: int,
    payload: DisputeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Client or supervisor disputes a day's work.
    Freezes the 24-hour countdown.
    """
    try:
        day, _dispute = await release_service.dispute_day(
            db=db,
            job_id=job_id,
            day_number=day_number,
            actor_user_id=current_user.id,
            reason=payload.reason,
        )
        return day
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/verify/{receipt_number}", response_model=ReceiptResponse)
async def verify_receipt(
    receipt_number: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Public Proofwork Receipt verification endpoint.
    Lenders or clients can verify receipt authenticity by ID.
    """
    clean_num = receipt_number.strip().upper()
    stmt = (
        select(Receipt)
        .where(Receipt.receipt_number == clean_num)
        .options(selectinload(Receipt.job))
    )
    receipt = (await db.execute(stmt)).scalar_one_or_none()
    if not receipt:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Receipt not found")
    return receipt
