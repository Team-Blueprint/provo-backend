import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.database import get_db
from app.core.enums import DayStatus, JobStatus, TradeType
from app.core.security import hash_pin, verify_pin
from app.core.utils import kobo_to_naira_str, normalize_phone_number
from app.db.models import AuditEvent, Job, User, Withdrawal, WorkerProfile
from app.services.funding_service import FundingService
from app.services.release_service import ReleaseService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ussd", tags=["USSD Simulator"])
release_service = ReleaseService()
funding_service = FundingService()


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
    total_withdrawn_kobo: int = 0
    total_withdrawn_naira: str = "₦0.00"
    available_balance_kobo: int = 0
    available_balance_naira: str = "₦0.00"
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
    amount_naira: float | int | None = Field(None, description="Withdrawal amount in Naira. If omitted, returns balance/account inquiry.")
    bank_code: str | None = Field(None, description="Destination bank 3-digit CBN code (e.g. '058')")
    account_number: str | None = Field(None, description="10-digit NUBAN destination account number")
    pin: str | None = Field(None, description="6-digit worker security PIN")


class USSDWithdrawResponse(BaseModel):
    status: str
    worker_name: str
    account_number: str
    bank_name: str
    amount_withdrawn_naira: str | None = None
    remaining_balance_naira: str | None = None
    reference: str | None = None
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


class USSDRegisterRequest(BaseModel):
    phone: str = Field(..., description="Worker's phone number")
    name: str = Field(..., min_length=2, max_length=150, description="Worker's full name")
    pin: str = Field(..., min_length=6, max_length=6, pattern=r"^[0-9]{6}$", description="6-digit security PIN")
    trade: str = Field(default="OTHER", description="Worker's trade/skill")


class USSDRegisterResponse(BaseModel):
    status: str = "success"
    user_id: str
    name: str
    phone: str
    role: str = "worker"
    trade: str
    paga_account_number: str | None = None
    message: str
    display_text: str


# --- Helper ---

def _normalize_trade(raw_trade: str | None) -> str:
    if not raw_trade:
        return TradeType.OTHER.value
    clean = raw_trade.strip().upper()
    try:
        return TradeType[clean].value
    except KeyError:
        return TradeType.OTHER.value

async def _get_worker_by_phone(db: AsyncSession, raw_phone: str) -> User:
    try:
        clean_phone = normalize_phone_number(raw_phone)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Phone {raw_phone} is not registered on PROVO.",
        )
    stmt = (
        select(User)
        .where(User.phone == clean_phone)
        .options(
            selectinload(User.worker_profile),
            selectinload(User.jobs_as_worker).selectinload(Job.days_schedule),
            selectinload(User.withdrawals),
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

@router.post(
    "/register",
    response_model=USSDRegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register New Worker via USSD",
    description="Allows informal workers to create an artisan account via USSD. Defaults to worker role and automatically provisions a personal Paga NUBAN.",
)
async def ussd_register(
    payload: USSDRegisterRequest,
    db: AsyncSession = Depends(get_db),
) -> Any:
    clean_phone = normalize_phone_number(payload.phone)
    clean_name = payload.name.strip()
    clean_trade = _normalize_trade(payload.trade)
    hashed_pin = hash_pin(payload.pin)

    # Check if user already exists
    stmt = (
        select(User)
        .where(User.phone == clean_phone)
        .options(selectinload(User.worker_profile))
    )
    user = (await db.execute(stmt)).scalar_one_or_none()

    if user and user.is_phone_verified:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An account with phone {clean_phone} already exists. Please dial *347*900# to access your account.",
        )

    if not user:
        user = User(
            name=clean_name,
            phone=clean_phone,
            pin_hash=hashed_pin,
            is_phone_verified=True,
            is_worker=True,
            is_client=False,
        )
        db.add(user)
        await db.flush()

        profile = WorkerProfile(
            user_id=user.id,
            trade=clean_trade,
            area="Uyo",
            is_available=True,
        )
        db.add(profile)
        await db.flush()
    else:
        # Existing unverified user (e.g. from an incomplete web signup)
        user.name = clean_name
        user.pin_hash = hashed_pin
        user.is_phone_verified = True
        user.is_worker = True
        user.is_client = False
        if user.worker_profile:
            user.worker_profile.trade = clean_trade
        else:
            profile = WorkerProfile(
                user_id=user.id,
                trade=clean_trade,
                area="Uyo",
                is_available=True,
            )
            db.add(profile)
        await db.flush()

    # Provision personal Paga virtual account for wage disbursements
    paga_account_num = None
    try:
        updated_profile = await funding_service.provision_worker_paga_account(db=db, user_id=user.id)
        paga_account_num = updated_profile.paga_account_number
    except Exception as e:
        logger.warning("Could not provision Paga account for worker %s via USSD: %s", user.id, e)

    # Audit trail
    audit = AuditEvent(
        entity_name="users",
        entity_id=str(user.id),
        action="USSD_WORKER_REGISTERED",
        actor="USSD",
        payload={
            "phone": clean_phone,
            "trade": clean_trade,
            "paga_account": paga_account_num,
        },
    )
    db.add(audit)
    await db.commit()

    msg = f"Artisan account created successfully for {clean_name} ({clean_trade})."
    paga_disp = paga_account_num or "Pending"
    display_text = (
        f"CON Welcome to PROVO, {clean_name}!\n"
        f"Your artisan account ({clean_trade}) is active.\n"
        f"Paga Account: {paga_disp}\n"
        f"Dial *347*900# anytime."
    )

    return USSDRegisterResponse(
        status="success",
        user_id=str(user.id),
        name=user.name,
        phone=user.phone,
        role="worker",
        trade=clean_trade,
        paga_account_number=paga_account_num,
        message=msg,
        display_text=display_text,
    )


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
    total_withdrawn_kobo = sum(
        w.amount_kobo for w in (worker.withdrawals or []) if w.status == "SUCCESS"
    )
    available_balance_kobo = max(0, total_earned_kobo - total_withdrawn_kobo)

    total_naira = kobo_to_naira_str(total_earned_kobo)
    total_withdrawn_naira = kobo_to_naira_str(total_withdrawn_kobo)
    available_naira = kobo_to_naira_str(available_balance_kobo)

    account_num = (
        worker.worker_profile.paga_account_number
        if worker.worker_profile and worker.worker_profile.paga_account_number
        else "Not provisioned"
    )
    trade = worker.worker_profile.trade if worker.worker_profile else "Artisan"

    display_text = (
        f"PROVO Balance:\n"
        f"Total Paid: {total_naira}\n"
        f"Available: {available_naira}\n"
        f"Paga Account: {account_num}"
    )

    return USSDBalanceResponse(
        phone=worker.phone,
        worker_name=worker.name,
        trade=trade,
        total_earned_kobo=total_earned_kobo,
        total_earned_naira=total_naira,
        total_withdrawn_kobo=total_withdrawn_kobo,
        total_withdrawn_naira=total_withdrawn_naira,
        available_balance_kobo=available_balance_kobo,
        available_balance_naira=available_naira,
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
    summary="Key 3: Request Withdrawal or Check Wage Disbursement Account",
)
async def ussd_withdraw(
    payload: USSDWithdrawRequest,
    db: AsyncSession = Depends(get_db),
) -> Any:
    worker = await _get_worker_by_phone(db, payload.phone)

    account_num = (
        payload.account_number
        or (
            worker.worker_profile.paga_account_number
            if worker.worker_profile and worker.worker_profile.paga_account_number
            else worker.payout_account_number or "9900000001"
        )
    )
    bank_name = payload.bank_code or "Paga"

    # Calculate current balance
    total_earned_kobo = sum(
        d.amount_kobo
        for job in worker.jobs_as_worker
        for d in job.days_schedule
        if d.status == DayStatus.PAID.value
    )
    total_withdrawn_kobo = sum(
        w.amount_kobo for w in (worker.withdrawals or []) if w.status == "SUCCESS"
    )
    available_balance_kobo = max(0, total_earned_kobo - total_withdrawn_kobo)

    # If amount_naira is not specified or None, behave as inquiry (backward compatible)
    if payload.amount_naira is None:
        msg = f"Available wages ({kobo_to_naira_str(available_balance_kobo)}) disburse automatically to Paga NUBAN {account_num} upon daily client confirmation."
        return USSDWithdrawResponse(
            status="success",
            worker_name=worker.name,
            account_number=account_num,
            bank_name=bank_name,
            remaining_balance_naira=kobo_to_naira_str(available_balance_kobo),
            message=msg,
            display_text=msg,
        )

    # If amount_naira is provided, perform withdrawal
    if payload.amount_naira <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Withdrawal amount must be greater than zero.",
        )

    requested_kobo = int(round(float(payload.amount_naira) * 100))

    # Verify PIN
    pin = (payload.pin or "").strip()
    is_demo = settings.DEMO_MODE and pin == "123456"
    pin_valid = False
    if is_demo:
        pin_valid = True
    elif worker.pin_hash and pin:
        pin_valid = verify_pin(pin, worker.pin_hash)

    if not pin_valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid transaction PIN.",
        )

    # Check sufficient available balance
    if requested_kobo > available_balance_kobo:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Insufficient available balance. "
                f"Available: {kobo_to_naira_str(available_balance_kobo)}, "
                f"requested: {kobo_to_naira_str(requested_kobo)}."
            ),
        )

    # Persist withdrawal
    ref = f"WD-{uuid.uuid4().hex[:10].upper()}"
    withdrawal = Withdrawal(
        worker_id=worker.id,
        amount_kobo=requested_kobo,
        destination_account=account_num,
        destination_bank=bank_name,
        reference=ref,
        status="SUCCESS",
    )
    db.add(withdrawal)

    # Audit event
    new_available_kobo = available_balance_kobo - requested_kobo
    audit = AuditEvent(
        entity_name="users",
        entity_id=str(worker.id),
        action="WORKER_WITHDRAWAL",
        actor="USSD",
        payload={
            "reference": ref,
            "amount_kobo": requested_kobo,
            "destination_account": account_num,
            "destination_bank": bank_name,
            "remaining_balance_kobo": new_available_kobo,
        },
    )
    db.add(audit)
    await db.commit()

    withdrawn_naira = kobo_to_naira_str(requested_kobo)
    remaining_naira = kobo_to_naira_str(new_available_kobo)
    msg = f"Withdrawal of {withdrawn_naira} to {bank_name} ({account_num}) successful. New balance: {remaining_naira}."

    return USSDWithdrawResponse(
        status="success",
        worker_name=worker.name,
        account_number=account_num,
        bank_name=bank_name,
        amount_withdrawn_naira=withdrawn_naira,
        remaining_balance_naira=remaining_naira,
        reference=ref,
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
        if e.status_code == status.HTTP_404_NOT_FOUND:
            return USSDGatewayResponse(
                action="END",
                message="END Welcome to PROVO!\nYour phone is not registered yet.\nPlease register to access artisan jobs and escrow payments.",
            )
        return USSDGatewayResponse(action="END", message=f"END {e.detail}")

    if choice == "1":
        total_earned_kobo = sum(
            d.amount_kobo
            for job in worker.jobs_as_worker
            for d in job.days_schedule
            if d.status == DayStatus.PAID.value
        )
        total_withdrawn_kobo = sum(
            w.amount_kobo for w in (worker.withdrawals or []) if w.status == "SUCCESS"
        )
        available_kobo = max(0, total_earned_kobo - total_withdrawn_kobo)
        account_num = (
            worker.worker_profile.paga_account_number
            if worker.worker_profile and worker.worker_profile.paga_account_number
            else "Not set"
        )
        msg = (
            f"END PROVO Balance:\n"
            f"Total Paid: {kobo_to_naira_str(total_earned_kobo)}\n"
            f"Available: {kobo_to_naira_str(available_kobo)}\n"
            f"Paga Account: {account_num}"
        )
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
        total_earned_kobo = sum(
            d.amount_kobo
            for job in worker.jobs_as_worker
            for d in job.days_schedule
            if d.status == DayStatus.PAID.value
        )
        total_withdrawn_kobo = sum(
            w.amount_kobo for w in (worker.withdrawals or []) if w.status == "SUCCESS"
        )
        available_kobo = max(0, total_earned_kobo - total_withdrawn_kobo)
        account_num = (
            worker.worker_profile.paga_account_number
            if worker.worker_profile and worker.worker_profile.paga_account_number
            else "Paga NUBAN"
        )
        msg = f"END Wages are paid directly to your Paga Account ({account_num}) automatically upon daily confirmation. Available: {kobo_to_naira_str(available_kobo)}"
        return USSDGatewayResponse(action="END", message=msg)

    else:
        return USSDGatewayResponse(action="END", message="END Invalid option selected.")
