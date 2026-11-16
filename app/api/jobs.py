import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.api.schemas import JobCreateRequest, JobResponse
from app.core.database import get_db
from app.db.models import User
from app.services.funding_service import FundingService
from app.services.job_service import JobService

router = APIRouter(prefix="/jobs", tags=["Jobs & Escrow"])
job_service = JobService()
funding_service = FundingService()


@router.post("", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def create_job(
    payload: JobCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Creates an escrow job draft with daily schedule.
    Authenticated client creates the job.
    """
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


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Retrieves job details, daily breakdown, and escrow status."""
    job = await job_service.get_job_by_id(db, job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job


@router.post("/{job_id}/fund-request", response_model=JobResponse)
async def initiate_fund_request(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """
    Generates one-time Paga payment link and dedicated NUBAN account number for client funding.
    Transitions job status to AWAITING_FUNDING.
    """
    try:
        await funding_service.initiate_job_escrow(db=db, job_id=job_id)
        return await job_service.get_job_by_id(db, job_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
