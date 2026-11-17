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
    x_paga_hash: str | None = Header(None, alias="x-paga-hash"),
):
    """
    Ingests Paga Collect payment callbacks:
    - Verifies raw-body SHA-512 HMAC signature
    - Deduplicates on paga_event_id
    - Matches job by funding reference and verifies exact payment amount
    - Moves job to FUNDED and writes FUNDED ledger entry
    """
    raw_body = await request.body()
    logger.info("Received Paga Webhook: %s", raw_body.decode("utf-8", errors="replace"))
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload",
        )

    signature = x_paga_signature or hash_header or x_paga_hash
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


@router.post("/telegram")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(None, alias="X-Telegram-Bot-Api-Secret-Token"),
):
    """
    Ingests live updates from Telegram Bot API:
    - Validates X-Telegram-Bot-Api-Secret-Token header
    - Feeds update to aiogram dispatcher
    """
    from app.core.config import settings
    from app.integrations.telegram.bot import process_telegram_webhook_update

    if settings.TELEGRAM_WEBHOOK_SECRET and x_telegram_bot_api_secret_token != settings.TELEGRAM_WEBHOOK_SECRET:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid Telegram webhook secret")

    try:
        raw_body = await request.body()
        payload = json.loads(raw_body.decode("utf-8"))
        await process_telegram_webhook_update(payload)
        return {"ok": True}
    except Exception as e:
        logger.error("Failed to process Telegram webhook update: %s", e)
        # Always return 200/ok so Telegram doesn't retry infinitely on non-critical update errors
        return {"ok": True, "error": str(e)}
