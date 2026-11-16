import json

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.enums import JobType
from app.payments.schemas import (
    CreatePaymentRequestSchema,
    PaymentEventResponse,
    PaymentLinkResponse,
)
from app.payments.service import PaymentService

router = APIRouter(prefix="/payments", tags=["Payments"])
payment_service = PaymentService()


@router.post("/request", response_model=PaymentLinkResponse)
async def create_payment_request(
    payload: CreatePaymentRequestSchema,
    db: AsyncSession = Depends(get_db),
):
    try:
        job_type = JobType(payload.job_type.upper())
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid job_type. Allowed: {[t.value for t in JobType]}",
        )

    try:
        job, payment_url = await payment_service.create_job_payment_link(
            db=db,
            worker_id=payload.worker_id,
            amount_kobo=payload.amount_kobo,
            payer_phone=payload.payer_phone,
            job_type=job_type,
            payer_name=payload.payer_name,
            description=payload.description,
        )
        return PaymentLinkResponse(
            reference=str(job.id),
            payment_url=payment_url,
            amount_kobo=payload.amount_kobo,
            worker_id=payload.worker_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/paga/callback", response_model=PaymentEventResponse)
async def paga_webhook_callback(
    request: Request,
    db: AsyncSession = Depends(get_db),
    hash_signature: str | None = Header(default=None, alias="hash"),
):
    raw_body = await request.body()
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON body") from e

    # Fallback: check if hash is inside payload
    signature = hash_signature or payload.get("hash") or ""

    try:
        event = await payment_service.handle_paga_callback(
            db=db,
            raw_body=raw_body,
            signature=signature,
            payload=payload,
        )
        return event
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
