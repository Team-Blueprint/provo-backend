import logging
import uuid
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.db.models import Job
from app.services.funding_service import FundingService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sandbox", tags=["Sandbox Simulator"])
funding_service = FundingService()


class SimulateDepositRequest(BaseModel):
    account_number: str = Field(..., description="Paga virtual account number assigned to the job")
    amount: Decimal = Field(..., gt=0, description="Amount in Naira deposited (e.g. 10000 or 5000.50)")
    payer_name: str | None = Field(default="Sandbox Test Client", description="Sender's name")
    payer_phone: str | None = Field(default="+2348000000000", description="Sender's phone")
    payer_bank: str | None = Field(default="Access Bank", description="Sender's bank")


class SimulateDepositResponse(BaseModel):
    status: str
    message: str
    job_id: str
    trade: str
    previous_status: str
    job_status: str
    amount_deposited_naira: float
    total_job_amount_naira: float
    total_funded_naira: float
    account_number: str
    reference_number: str
    artisan_notified: bool


@router.post(
    "/simulate-deposit",
    response_model=SimulateDepositResponse,
    summary="Simulate Inbound Bank Deposit (Frontend Sandbox Testing)",
    description=(
        "Simulates a customer making a real bank transfer into the job's virtual Paga NUBAN. "
        "Applies escrow funding logic, updates ledger, transitions job status, and dispatches SMS/Telegram notifications."
    ),
)
async def simulate_bank_deposit(
    request: SimulateDepositRequest,
    db: AsyncSession = Depends(get_db),
) -> Any:
    # Look up the job by paga_account_number
    stmt = (
        select(Job)
        .where(Job.paga_account_number == request.account_number.strip())
        .options(selectinload(Job.worker), selectinload(Job.client), selectinload(Job.days_schedule))
    )
    job = (await db.execute(stmt)).scalar_one_or_none()

    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No job found associated with account number '{request.account_number}'. Make sure the job has an active payment request.",
        )

    previous_status = job.status
    amount_naira_float = float(request.amount)
    simulated_tx_id = f"SIM-{uuid.uuid4().hex[:12].upper()}"

    # Build Paga Collect deposit callback payload
    payload = {
        "transactionId": simulated_tx_id,
        "referenceNumber": job.funding_reference,
        "accountNumber": request.account_number.strip(),
        "amount": amount_naira_float,
        "currency": "NGN",
        "payerName": request.payer_name,
        "payerPhoneNumber": request.payer_phone,
        "payerBank": request.payer_bank,
        "status": "SUCCESSFUL",
        "channel": "BANK_TRANSFER",
    }

    try:
        updated_job, _payment_in = await funding_service.process_paga_webhook(
            db=db,
            payload=payload,
            raw_body=b"",
            signature=None,  # Sandbox simulated call bypasses external signature check
        )
    except ValueError as e:
        logger.error("Simulation failed: %s", e)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # Calculate total funded kobo from DB
    from app.db.models import PaymentIn

    all_payments_stmt = select(PaymentIn).where(PaymentIn.job_id == updated_job.id)
    all_payments = list((await db.execute(all_payments_stmt)).scalars().all())
    total_funded_kobo = sum(p.amount_kobo for p in all_payments)

    artisan_notified = updated_job.status in ("FUNDED", "OVERFUNDED")

    return SimulateDepositResponse(
        status="success",
        message=f"Simulated deposit of ₦{amount_naira_float:,.2f} applied to {request.account_number}.",
        job_id=str(updated_job.id),
        trade=updated_job.trade,
        previous_status=previous_status,
        job_status=updated_job.status,
        amount_deposited_naira=amount_naira_float,
        total_job_amount_naira=updated_job.total_kobo / 100.0,
        total_funded_naira=total_funded_kobo / 100.0,
        account_number=request.account_number.strip(),
        reference_number=updated_job.funding_reference,
        artisan_notified=artisan_notified,
    )
