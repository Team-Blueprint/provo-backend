from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.messaging.schemas import MessageLogResponse, SendSMSRequest
from app.messaging.service import MessagingService

router = APIRouter(prefix="/messaging", tags=["Messaging"])
messaging_service = MessagingService()


@router.post("/send-sms", response_model=MessageLogResponse)
async def send_sms(
    payload: SendSMSRequest,
    db: AsyncSession = Depends(get_db),
):
    try:
        log_entry = await messaging_service.send_sms(
            db=db,
            to_phone=payload.to_phone,
            content=payload.content,
        )
        return log_entry
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/webhooks/httpsms", status_code=status.HTTP_200_OK)
async def httpsms_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    try:
        body = await request.json()
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON") from e

    log_entry = await messaging_service.handle_httpsms_webhook(db=db, payload=body)
    return {"status": "success", "processed": log_entry is not None}


@router.get("/webhooks/whatsapp")
async def whatsapp_webhook_verification(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
):
    """
    Verification handshake for Meta WhatsApp Cloud API.
    Meta sends a GET request with hub.challenge and hub.verify_token.
    """
    if hub_mode == "subscribe" and hub_verify_token == (settings.WHATSAPP_VERIFY_TOKEN or "proofwork_verify_token"):
        return Response(content=hub_challenge, media_type="text/plain")
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Verification failed")


@router.post("/webhooks/whatsapp", status_code=status.HTTP_200_OK)
async def whatsapp_webhook_post(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_hub_signature_256: str | None = Header(default=None, alias="X-Hub-Signature-256"),
):
    raw_body = await request.body()
    try:
        payload = await request.json()
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON") from e

    try:
        await messaging_service.handle_whatsapp_webhook(
            db=db,
            payload=payload,
            raw_body=raw_body,
            signature=x_hub_signature_256,
        )
        return {"status": "success"}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))
