import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.schemas import (
    DisputeRequest,
    JobCreateRequest,
    JobDayResponse,
    JobResponse,
    ReceiptResponse,
)
from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.core.enums import JobStatus, TradeType
from app.db.models import Receipt, User
from app.services.funding_service import FundingService
from app.services.job_service import JobService
from app.services.release_service import ReleaseService

router = APIRouter(prefix="/jobs", tags=["Jobs & Escrow"])
job_service = JobService()
funding_service = FundingService()
release_service = ReleaseService()


@router.post("", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def create_job(
    payload: JobCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Creates an escrow job draft with daily pay schedule."""
    try:
        job = await job_service.create_job(
            db=db,
            client_id=current_user.id,
            trade=payload.trade.value,
            description=payload.description,
            total_kobo=payload.total_kobo,
            days=payload.days,
            worker_id=payload.worker_id,
            supervisor_id=payload.supervisor_id,
        )
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
