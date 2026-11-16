import json
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.services.funding_service import FundingService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])
funding_service = FundingService()


@router.post("/paga")
async def paga_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_paga_signature: str | None = Header(None, alias="X-Paga-Signature"),
    hash_header: str | None = Header(None, alias="hash"),
):
    """
    Ingests Paga Collect payment callbacks:
    - Verifies raw-body SHA-512 HMAC signature
    - Deduplicates on paga_event_id
    - Matches job by funding reference and verifies exact payment amount
    - Moves job to FUNDED and writes FUNDED ledger entry
    """
    raw_body = await request.body()
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload",
        )

    signature = x_paga_signature or hash_header
    try:
        job, payment_in = await funding_service.process_paga_webhook(
            db=db,
            payload=payload,
            raw_body=raw_body,
            signature=signature,
        )
        return {
            "status": "success",
            "job_id": str(job.id),
            "job_status": job.status,
            "paga_event_id": payment_in.paga_event_id,
        }
    except ValueError as e:
        logger.warning("Paga webhook rejection: %s", e)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
