import hashlib
import hmac
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.enums import JobStatus, MessageChannel, MessageDirection, MessageStatus
from app.core.utils import kobo_to_naira_str, normalize_phone_number
from app.jobs.models import Confirmation, Job
from app.jobs.service import JobService
from app.messaging.models import MessageLog
from app.workers.models import Worker

logger = logging.getLogger(__name__)


class MessagingService:
    def __init__(self, job_service: JobService | None = None):
        self.job_service = job_service or JobService()

    async def send_sms(
        self,
        db: AsyncSession,
        to_phone: str,
        content: str,
    ) -> MessageLog:
        """
        Sends an SMS via httpSMS REST API.
        Falls back to local simulated delivery if HTTPSMS_API_KEY is not configured.
        """
        clean_to_phone = normalize_phone_number(to_phone)
        message_id = f"SMS-{uuid.uuid4().hex[:16]}"
        status = MessageStatus.SENT

        if settings.HTTPSMS_API_KEY and settings.HTTPSMS_SENDER_PHONE:
            url = "https://api.httpsms.com/v1/messages/send"
            headers = {
                "x-api-key": settings.HTTPSMS_API_KEY,
                "Content-Type": "application/json",
            }
            body = {
                "from": settings.HTTPSMS_SENDER_PHONE,
                "to": clean_to_phone,
                "content": content,
            }
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.post(url, json=body, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()
                    message_id = str(data.get("data", {}).get("id") or message_id)
                    logger.info("Sent real SMS via httpSMS to %s (id=%s)", clean_to_phone, message_id)
            except Exception as e:
                logger.error("Failed to send real SMS via httpSMS: %s", e)
                status = MessageStatus.FAILED
        else:
            logger.info("[SIMULATED httpSMS] Sent SMS to %s: '%s'", clean_to_phone, content)

        log_entry = MessageLog(
            channel=MessageChannel.HTTPSMS,
            message_id=message_id,
            direction=MessageDirection.OUTBOUND,
            to_phone=clean_to_phone,
            from_phone=settings.HTTPSMS_SENDER_PHONE or "+2348000000000",
            content=content,
            status=status,
        )
        db.add(log_entry)
        await db.flush()
        return log_entry

    async def send_whatsapp_message(
        self,
        db: AsyncSession,
        to_phone: str,
        content: str,
        interactive: dict[str, Any] | None = None,
    ) -> MessageLog:
        """
        Sends a WhatsApp message via Meta Cloud API.
        Falls back to simulated delivery if credentials are not configured.
        """
        clean_to_phone = normalize_phone_number(to_phone).lstrip("+")
        message_id = f"WA-{uuid.uuid4().hex[:16]}"
        status = MessageStatus.SENT

        if settings.WHATSAPP_TOKEN and settings.WHATSAPP_PHONE_NUMBER_ID:
            url = f"https://graph.facebook.com/v19.0/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
            headers = {
                "Authorization": f"Bearer {settings.WHATSAPP_TOKEN}",
                "Content-Type": "application/json",
            }
            body: dict[str, Any] = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": clean_to_phone,
            }
            if interactive:
                body["type"] = "interactive"
                body["interactive"] = interactive
            else:
                body["type"] = "text"
                body["text"] = {"body": content}

            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.post(url, json=body, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()
                    message_id = str(data.get("messages", [{}])[0].get("id") or message_id)
                    logger.info("Sent WhatsApp message to %s (id=%s)", clean_to_phone, message_id)
            except Exception as e:
                logger.error("Failed to send WhatsApp message: %s", e)
                status = MessageStatus.FAILED
        else:
            logger.info("[SIMULATED WhatsApp] Sent message to %s: '%s'", clean_to_phone, content)

        log_entry = MessageLog(
            channel=MessageChannel.WHATSAPP,
            message_id=message_id,
            direction=MessageDirection.OUTBOUND,
            to_phone=f"+{clean_to_phone}",
            from_phone="WhatsApp-Business",
            content=content,
            status=status,
        )
        db.add(log_entry)
        await db.flush()
        return log_entry

    async def send_confirmation_prompt(
        self,
        db: AsyncSession,
        worker_name: str,
        job_amount_kobo: int,
        client_phone: str,
        confirmation_token: str,
        job_type_name: str,
    ) -> MessageLog:
        """
        Unified Router with Automated Fallback:
        1. Tries WhatsApp with interactive buttons.
        2. On failure or if WhatsApp is unconfigured, falls back immediately to httpSMS.
        """
        amount_str = kobo_to_naira_str(job_amount_kobo)
        confirm_url = f"{settings.BASE_URL}/confirm/{confirmation_token}"

        text_content = (
            f"Proofwork: Did {worker_name} do {job_type_name} work for you for {amount_str}?\n\n"
            f"Tap to confirm & get your official receipt: {confirm_url}\n"
            f"Or reply: 1 for YES, 2 for NO."
        )

        interactive_buttons = {
            "type": "button",
            "body": {
                "text": f"Did {worker_name} do {job_type_name} work for you for {amount_str}?"
            },
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": f"YES_{confirmation_token}", "title": "Yes, for work"}},
                    {"type": "reply", "reply": {"id": f"NO_{confirmation_token}", "title": "No"}},
                ]
            },
        }

        # Try WhatsApp first if configured
        if settings.WHATSAPP_TOKEN and settings.WHATSAPP_PHONE_NUMBER_ID:
            wa_log = await self.send_whatsapp_message(
                db=db,
                to_phone=client_phone,
                content=text_content,
                interactive=interactive_buttons,
            )
            if wa_log.status != MessageStatus.FAILED:
                return wa_log

        # Fallback to httpSMS
        logger.info("Routing confirmation via httpSMS fallback to %s", client_phone)
        return await self.send_sms(db=db, to_phone=client_phone, content=text_content)

    async def send_money_arrived_alert(
        self,
        db: AsyncSession,
        worker: Worker,
        amount_kobo: int,
        client_phone: str,
        is_confirmed: bool,
    ) -> MessageLog:
        """Alerts the worker that payment has been received on their rails."""
        amount_str = kobo_to_naira_str(amount_kobo)
        status_text = "CONFIRMED" if is_confirmed else "AWAITING CLIENT CONFIRMATION"

        alert_text = (
            f"Proofwork Alert: {amount_str} received from {client_phone}.\n"
            f"Status: {status_text}.\n"
            f"Once verified by the client, it is added to your lender Profile Score."
        )

        if settings.WHATSAPP_TOKEN and settings.WHATSAPP_PHONE_NUMBER_ID:
            wa_log = await self.send_whatsapp_message(db=db, to_phone=worker.phone, content=alert_text)
            if wa_log.status != MessageStatus.FAILED:
                return wa_log

        return await self.send_sms(db=db, to_phone=worker.phone, content=alert_text)

    async def handle_httpsms_webhook(
        self,
        db: AsyncSession,
        payload: dict[str, Any],
    ) -> MessageLog | None:
        """
        Processes incoming httpSMS CloudEvent webhooks:
        - Deduplicates messages using message_id
        - Handles message.phone.received (two-way basic phone reply: 1=YES, 2=NO)
        - Handles message.phone.delivered
        """
        event_type = payload.get("type", "")
        data = payload.get("data", {})

        message_id = str(data.get("message_id") or data.get("id") or payload.get("id") or "")
        if not message_id:
            logger.warning("Received httpSMS webhook without message_id")
            return None

        # 1. Deduplication Check
        stmt = select(MessageLog).where(MessageLog.message_id == message_id)
        existing = (await db.execute(stmt)).scalar_one_or_none()
        if existing:
            logger.info("Duplicate httpSMS webhook message_id=%s ignored", message_id)
            return existing

        from_contact = data.get("contact") or data.get("sender") or ""
        to_owner = data.get("owner") or settings.HTTPSMS_SENDER_PHONE or ""
        content = str(data.get("content") or "").strip()

        direction = MessageDirection.INBOUND if event_type == "message.phone.received" else MessageDirection.OUTBOUND
        status = MessageStatus.RECEIVED if event_type == "message.phone.received" else MessageStatus.DELIVERED

        log_entry = MessageLog(
            channel=MessageChannel.HTTPSMS,
            message_id=message_id,
            direction=direction,
            to_phone=to_owner if direction == MessageDirection.INBOUND else from_contact,
            from_phone=from_contact if direction == MessageDirection.INBOUND else to_owner,
            content=content,
            status=status,
            raw_data=payload,
        )
        db.add(log_entry)
        await db.flush()

        # Inbound reply logic: two-way SMS confirmation
        if direction == MessageDirection.INBOUND and from_contact:
            await self._process_inbound_reply(db, from_phone=from_contact, text=content)

        return log_entry

    async def handle_whatsapp_webhook(
        self,
        db: AsyncSession,
        payload: dict[str, Any],
        raw_body: bytes,
        signature: str | None = None,
    ) -> None:
        """
        Validates WhatsApp webhook signature and processes incoming messages/buttons.
        """
        # Signature verification if secret configured
        if settings.WHATSAPP_APP_SECRET and signature:
            expected_sig = "sha256=" + hmac.new(
                settings.WHATSAPP_APP_SECRET.encode("utf-8"),
                raw_body,
                hashlib.sha256,
            ).hexdigest()
            if not hmac.compare_digest(expected_sig, signature):
                logger.warning("Invalid WhatsApp webhook signature")
                raise ValueError("Invalid WhatsApp signature")

        # Parse WhatsApp entries
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                messages = value.get("messages", [])
                for msg in messages:
                    msg_id = msg.get("id")
                    from_num = f"+{msg.get('from', '')}"
                    msg_type = msg.get("type")

                    # Deduplication
                    stmt = select(MessageLog).where(MessageLog.message_id == msg_id)
                    if (await db.execute(stmt)).scalar_one_or_none():
                        continue

                    text = ""
                    if msg_type == "text":
                        text = msg.get("text", {}).get("body", "")
                    elif msg_type == "interactive":
                        btn_reply = msg.get("interactive", {}).get("button_reply", {})
                        text = btn_reply.get("id", "") or btn_reply.get("title", "")

                    log_entry = MessageLog(
                        channel=MessageChannel.WHATSAPP,
                        message_id=msg_id,
                        direction=MessageDirection.INBOUND,
                        to_phone="WhatsApp-Business",
                        from_phone=from_num,
                        content=text,
                        status=MessageStatus.RECEIVED,
                        raw_data=msg,
                    )
                    db.add(log_entry)
                    await db.flush()

                    if from_num and text:
                        await self._process_inbound_reply(db, from_phone=from_num, text=text)

    async def _process_inbound_reply(
        self,
        db: AsyncSession,
        from_phone: str,
        text: str,
    ) -> None:
        """
        Routes inbound replies from either SMS or WhatsApp to confirm or dispute jobs.
        """
        clean_text = text.strip().upper()
        clean_phone = normalize_phone_number(from_phone)

        # Look for pending confirmation for this client phone
        now = datetime.now(UTC)
        stmt = (
            select(Confirmation)
            .join(Job, Confirmation.job_id == Job.id)
            .where(
                Job.client_phone == clean_phone,
                Confirmation.is_confirmed == False,
                Confirmation.expires_at > now,
                Job.status == JobStatus.AWAITING_CONFIRMATION,
            )
            .options(selectinload(Confirmation.job).selectinload(Job.worker))
            .order_by(Confirmation.created_at.desc())
        )
        conf = (await db.execute(stmt)).scalars().first()
        if not conf:
            return

        if clean_text in ("1", "YES", "CONFIRM") or clean_text.startswith("YES_"):
            job, receipt = await self.job_service.confirm_job(db, conf.confirmation_token)
            receipt_url = f"{settings.BASE_URL}/jobs/verify/{receipt.receipt_number}"
            reply_msg = (
                f"Thank you! Your payment of {kobo_to_naira_str(job.amount_kobo)} has been confirmed.\n"
                f"Official Proofwork Receipt: {receipt_url}"
            )
            await self.send_sms(db, to_phone=clean_phone, content=reply_msg)
            # Alert worker
            await self.send_money_arrived_alert(
                db=db,
                worker=job.worker,
                amount_kobo=job.amount_kobo,
                client_phone=clean_phone,
                is_confirmed=True,
            )

        elif clean_text in ("2", "NO", "DISPUTE") or clean_text.startswith("NO_"):
            job, _dispute = await self.job_service.dispute_job(db, conf.confirmation_token)
            reply_msg = "Your response has been noted. This job has been recorded as disputed."
            await self.send_sms(db, to_phone=clean_phone, content=reply_msg)
