import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.enums import DayStatus, JobStatus
from app.core.utils import kobo_to_naira_str, normalize_phone_number
from app.db.models import Job, User
from app.services.release_service import ReleaseService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ussd", tags=["USSD Simulator"])
release_service = ReleaseService()


# --- Schemas ---

class USSDJobItem(BaseModel):
    job_id: str
    trade: str
    description: str
    total_naira: str
    current_day: int
    total_days: int
    day_status: str


class USSDBalanceResponse(BaseModel):
    phone: str
    worker_name: str
    trade: str
    total_earned_kobo: int
    total_earned_naira: str
    paga_account_number: str
    bank_name: str = "Paga"
    display_text: str


class USSDPendingJobsResponse(BaseModel):
    phone: str
    total_pending_jobs: int
    jobs: list[USSDJobItem]
    display_text: str


class USSDReportDoneRequest(BaseModel):
    phone: str = Field(..., description="Worker's phone number")
    job_id: uuid.UUID = Field(..., description="Job UUID to report day done")


class USSDReportDoneResponse(BaseModel):
    status: str
    message: str
    job_id: str
    day_number: int
    display_text: str


class USSDWithdrawRequest(BaseModel):
    phone: str = Field(..., description="Worker's phone number")


class USSDWithdrawResponse(BaseModel):
    status: str
    worker_name: str
    account_number: str
    bank_name: str
    message: str
    display_text: str


class USSDGatewayRequest(BaseModel):
    session_id: str = Field("SIM-SESSION-001", alias="sessionId")
    phone_number: str = Field(..., alias="phoneNumber")
    service_code: str = Field("*347*900#", alias="serviceCode")
    text: str = Field("", description="Raw USSD dial string (e.g. '', '1', '2', '3')")


class USSDGatewayResponse(BaseModel):
    action: str  # CON (continue) or END
    message: str


# --- Helper ---

async def _get_worker_by_phone(db: AsyncSession, raw_phone: str) -> User:
    clean_phone = normalize_phone_number(raw_phone)
    stmt = (
        select(User)
        .where(User.phone == clean_phone)
        .options(
            selectinload(User.worker_profile),
            selectinload(User.jobs_as_worker).selectinload(Job.days_schedule),
        )
    )
    user = (await db.execute(stmt)).scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Phone {raw_phone} is not registered on PROVO.",
        )
    if not user.is_worker:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This USSD service is reserved for registered PROVO artisans/workers.",
        )
    return user


# --- Endpoints ---

@router.get(
    "/balance",
    response_model=USSDBalanceResponse,
    summary="Key 1: Check Worker Balance & Paga Account",
)
async def get_ussd_balance(
    phone: str = Query(..., description="Worker phone number"),
    db: AsyncSession = Depends(get_db),
) -> Any:
    worker = await _get_worker_by_phone(db, phone)

    # Calculate total settled earnings from paid days
    total_earned_kobo = sum(
        d.amount_kobo
        for job in worker.jobs_as_worker
        for d in job.days_schedule
        if d.status == DayStatus.PAID.value
    )
    total_naira = kobo_to_naira_str(total_earned_kobo)
    account_num = (
        worker.worker_profile.paga_account_number
        if worker.worker_profile and worker.worker_profile.paga_account_number
        else "Not provisioned"
    )
    trade = worker.worker_profile.trade if worker.worker_profile else "Artisan"

    display_text = (
        f"PROVO Balance:\n"
        f"Total Paid: {total_naira}\n"
        f"Paga Account: {account_num}"
    )

    return USSDBalanceResponse(
        phone=worker.phone,
        worker_name=worker.name,
        trade=trade,
        total_earned_kobo=total_earned_kobo,
        total_earned_naira=total_naira,
        paga_account_number=account_num,
        bank_name="Paga",
        display_text=display_text,
    )


@router.get(
    "/pending-jobs",
    response_model=USSDPendingJobsResponse,
    summary="Key 2: View Worker Pending Active Jobs",
)
async def get_ussd_pending_jobs(
    phone: str = Query(..., description="Worker phone number"),
    db: AsyncSession = Depends(get_db),
) -> Any:
    worker = await _get_worker_by_phone(db, phone)

    active_jobs: list[USSDJobItem] = []
    display_lines = ["Pending Jobs:"]

    for idx, job in enumerate(worker.jobs_as_worker, start=1):
        if job.status in (JobStatus.FUNDED.value, JobStatus.IN_PROGRESS.value):
            # Find next pending day
            next_day = next((d for d in job.days_schedule if d.status == DayStatus.PENDING.value), None)
            day_num = next_day.day_number if next_day else job.days
            day_stat = next_day.status if next_day else "ALL_REPORTED"

            active_jobs.append(
                USSDJobItem(
                    job_id=str(job.id),
                    trade=job.trade,
                    description=job.description,
                    total_naira=kobo_to_naira_str(job.total_kobo),
                    current_day=day_num,
                    total_days=job.days,
                    day_status=day_stat,
                )
            )
            display_lines.append(f"{idx}. {job.trade} - {job.description} (Day {day_num}/{job.days})")

    if not active_jobs:
        display_lines.append("No active escrow jobs right now.")

    return USSDPendingJobsResponse(
        phone=worker.phone,
        total_pending_jobs=len(active_jobs),
        jobs=active_jobs,
        display_text="\n".join(display_lines),
    )


@router.post(
    "/report-done",
    response_model=USSDReportDoneResponse,
    summary="Report Day Done from USSD Simulator",
)
async def ussd_report_done(
    payload: USSDReportDoneRequest,
    db: AsyncSession = Depends(get_db),
) -> Any:
    worker = await _get_worker_by_phone(db, payload.phone)

    # Find the job
    stmt = (
        select(Job)
        .where(Job.id == payload.job_id)
        .options(selectinload(Job.days_schedule))
    )
    job = (await db.execute(stmt)).scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")

    # Find the next pending day
    next_day = next((d for d in job.days_schedule if d.status == DayStatus.PENDING.value), None)
    if not next_day:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="All days for this job have already been reported done.",
        )

    try:
        updated_day = await release_service.report_day_done(
            db=db,
            job_id=job.id,
            day_number=next_day.day_number,
            worker_user_id=worker.id,
        )
        await db.commit()
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    display_text = (
        f"Day {updated_day.day_number} reported done!\n"
        f"Client has 24hrs to confirm before release."
    )

    return USSDReportDoneResponse(
        status="success",
        message=f"Day {updated_day.day_number} reported done successfully.",
        job_id=str(job.id),
        day_number=updated_day.day_number,
        display_text=display_text,
    )


@router.post(
    "/withdraw",
    response_model=USSDWithdrawResponse,
    summary="Key 3: Check Wage Disbursement Account",
)
async def ussd_withdraw(
    payload: USSDWithdrawRequest,
    db: AsyncSession = Depends(get_db),
) -> Any:
    worker = await _get_worker_by_phone(db, payload.phone)

    account_num = (
        worker.worker_profile.paga_account_number
        if worker.worker_profile and worker.worker_profile.paga_account_number
        else worker.payout_account_number or "9900000001"
    )
    bank_name = "Paga"

    msg = f"Available wages disburse automatically to Paga NUBAN {account_num} upon daily client confirmation."

    return USSDWithdrawResponse(
        status="success",
        worker_name=worker.name,
        account_number=account_num,
        bank_name=bank_name,
        message=msg,
        display_text=msg,
    )


@router.post(
    "",
    response_model=USSDGatewayResponse,
    summary="Interactive USSD Session Gateway (*347*900#)",
    description="Standard USSD gateway endpoint compatible with telco session requests (CON/END)",
)
async def ussd_gateway(
    payload: USSDGatewayRequest,
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Standard USSD Gateway handling dial strings:
    - Root ('*347*900#' or '') -> Menu
    - '1' -> Balance
    - '2' -> Pending Jobs
    - '3' -> Withdraw
    """
    clean_text = payload.text.strip()

    # Initial Menu
    if not clean_text:
        menu = (
            "CON Welcome to PROVO\n"
            "1. Balance\n"
            "2. Pending Jobs\n"
            "3. Withdraw"
        )
        return USSDGatewayResponse(action="CON", message=menu)

    # Route by first digit
    choice = clean_text.split("*")[0]

    try:
        worker = await _get_worker_by_phone(db, payload.phone_number)
    except HTTPException as e:
        return USSDGatewayResponse(action="END", message=f"END {e.detail}")

    if choice == "1":
        total_earned_kobo = sum(
            d.amount_kobo
            for job in worker.jobs_as_worker
            for d in job.days_schedule
            if d.status == DayStatus.PAID.value
        )
        account_num = (
            worker.worker_profile.paga_account_number
            if worker.worker_profile and worker.worker_profile.paga_account_number
            else "Not set"
        )
        msg = f"END PROVO Balance:\nTotal Paid: {kobo_to_naira_str(total_earned_kobo)}\nPaga Account: {account_num}"
        return USSDGatewayResponse(action="END", message=msg)

    elif choice == "2":
        active_jobs = [
            j for j in worker.jobs_as_worker
            if j.status in (JobStatus.FUNDED.value, JobStatus.IN_PROGRESS.value)
        ]
        if not active_jobs:
            return USSDGatewayResponse(action="END", message="END You have no pending escrow jobs.")

        lines = ["END Pending Jobs:"]
        for idx, j in enumerate(active_jobs, start=1):
            next_day = next((d for d in j.days_schedule if d.status == DayStatus.PENDING.value), None)
            day_num = next_day.day_number if next_day else j.days
            lines.append(f"{idx}. {j.trade} (Day {day_num}/{j.days})")
        return USSDGatewayResponse(action="END", message="\n".join(lines))

    elif choice == "3":
        account_num = (
            worker.worker_profile.paga_account_number
            if worker.worker_profile and worker.worker_profile.paga_account_number
            else "Paga NUBAN"
        )
        msg = f"END Wages are paid directly to your Paga Account ({account_num}) automatically upon daily confirmation."
        return USSDGatewayResponse(action="END", message=msg)

    else:
        return USSDGatewayResponse(action="END", message="END Invalid option selected.")
