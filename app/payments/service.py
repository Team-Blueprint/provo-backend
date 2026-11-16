import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.enums import (
    JobStatus,
    JobType,
    MessageChannel,
    PaymentEventStatus,
    VerificationGrade,
)
from app.core.security import generate_secure_token
from app.core.utils import normalize_phone_number
from app.jobs.models import Confirmation, Job
from app.messaging.service import MessagingService
from app.payments.adapters.base import PagaAdapter
from app.payments.adapters.mock import MockPagaAdapter
from app.payments.adapters.real import RealPagaAdapter
from app.payments.models import Payer, PaymentEvent
from app.workers.models import Worker

logger = logging.getLogger(__name__)


def get_paga_adapter() -> PagaAdapter:
    """Factory function returning the configured Paga adapter (Mock vs Real Sandbox)."""
    if not settings.PAGA_USE_MOCK and settings.PAGA_HMAC_KEY:
        return RealPagaAdapter()
    return MockPagaAdapter()


class PaymentService:
    def __init__(
        self,
        adapter: PagaAdapter | None = None,
        messaging_service: MessagingService | None = None,
    ):
        self.adapter = adapter or get_paga_adapter()
        self.messaging_service = messaging_service or MessagingService()

    async def register_worker_paga_account(
        self,
        db: AsyncSession,
        worker: Worker,
    ) -> Worker:
        """
        Creates and associates a dedicated Paga virtual account with the worker.
        """
        account_ref = f"PW-ACC-{worker.id.hex[:12].upper()}"
        res = await self.adapter.create_persistent_account(
            worker_id=str(worker.id),
            name=worker.name,
            phone=worker.phone,
            account_reference=account_ref,
        )

        worker.paga_account_number = res.account_number
        worker.paga_account_reference = res.account_reference
        db.add(worker)
        await db.flush()
        return worker

    async def create_job_payment_link(
        self,
        db: AsyncSession,
        worker_id: uuid.UUID,
        amount_kobo: int,
        payer_phone: str,
        job_type: JobType,
        payer_name: str = "Client",
        description: str = "Payment for job",
    ) -> tuple[Job, str]:
        """
        Initiates a Grade 1 payment link for a job.
        Creates Job in AWAITING_CONFIRMATION status.
        """
        clean_payer_phone = normalize_phone_number(payer_phone)
        reference = f"PW-LINK-{uuid.uuid4().hex[:12].upper()}"
        callback_url = f"{settings.BASE_URL}/payments/paga/callback"

        # 1. Call Paga adapter for payment link
        res = await self.adapter.create_payment_request(
            reference=reference,
            amount_kobo=amount_kobo,
            currency="NGN",
            payer_phone=clean_payer_phone,
            payer_name=payer_name,
            description=description,
            callback_url=callback_url,
        )

        # 2. Find or create Payer
        payer = await self._get_or_create_payer(db, clean_payer_phone, payer_name)

        # 3. Create initial Job record
        job = Job(
            worker_id=worker_id,
            payer_id=payer.id,
            amount_kobo=amount_kobo,
            job_type=job_type,
            status=JobStatus.AWAITING_CONFIRMATION,
            grade=VerificationGrade.GRADE_1_LINK,
            client_phone=clean_payer_phone,
        )
        db.add(job)
        await db.flush()

        # 4. Create confirmation token for when payment arrives
        token = generate_secure_token(24)
        conf = Confirmation(
            job_id=job.id,
            confirmation_token=token,
            channel=MessageChannel.HTTPSMS,
            is_confirmed=False,
            expires_at=datetime.now(UTC) + timedelta(hours=48),
        )
        db.add(conf)
        await db.flush()

        return job, res.payment_url

    async def handle_paga_callback(
        self,
        db: AsyncSession,
        raw_body: bytes,
        signature: str,
        payload: dict[str, Any],
    ) -> PaymentEvent:
        """
        Idempotent callback processor:
        1. Checks signature.
        2. Detects duplicates on paga_transaction_id.
        3. Identifies worker and payer.
        4. Classifies grade (Grade 1 Link vs Grade 2 Transfer).
        5. Stores PaymentEvent.
        6. Transitions/links Job & dispatches confirmation prompt to client.
        """
        # 1. Signature Check
        is_valid = self.adapter.verify_webhook_signature(raw_body, signature)
        if not is_valid:
            logger.warning("Invalid Paga webhook signature received")
            raise ValueError("Invalid webhook signature")

        txn_id = str(payload.get("transactionId") or payload.get("transaction_id") or "")
        ref = str(payload.get("referenceNumber") or payload.get("reference") or txn_id)
        raw_amount = float(payload.get("amount") or 0.0)
        amount_kobo = round(raw_amount * 100)

        if not txn_id:
            raise ValueError("Missing transactionId in Paga webhook payload")

        # 2. Idempotency Check: Ignore duplicates
        existing_stmt = select(PaymentEvent).where(PaymentEvent.paga_transaction_id == txn_id)
        result = await db.execute(existing_stmt)
        existing_event = result.scalar_one_or_none()
        if existing_event:
            logger.info("Duplicate Paga callback ignored for txn_id=%s", txn_id)
            return existing_event

        # 3. Match Worker
        account_ref = payload.get("accountReference")
        account_num = payload.get("accountNumber")

        worker = None
        if account_ref:
            stmt = select(Worker).where(Worker.paga_account_reference == account_ref)
            worker = (await db.execute(stmt)).scalar_one_or_none()
        if not worker and account_num:
            stmt = select(Worker).where(Worker.paga_account_number == account_num)
            worker = (await db.execute(stmt)).scalar_one_or_none()

        if not worker:
            stmt = select(Worker).order_by(Worker.created_at.asc()).limit(1)
            worker = (await db.execute(stmt)).scalar_one_or_none()
            if not worker:
                raise ValueError("No worker found matching Paga transaction reference")

        # 4. Classify Grade & Payer
        payer_phone = payload.get("payerPhone")
        payer_name = payload.get("payerName") or "Payer"
        channel = str(payload.get("channel") or "BANK_TRANSFER")

        if ref.startswith("PW-LINK-") or channel == "PAYMENT_LINK":
            grade = VerificationGrade.GRADE_1_LINK
        else:
            grade = VerificationGrade.GRADE_2_TRANSFER

        payer = None
        if payer_phone:
            clean_phone = normalize_phone_number(str(payer_phone))
            payer = await self._get_or_create_payer(db, clean_phone, payer_name)

        # 5. Persist PaymentEvent
        payment_event = PaymentEvent(
            paga_transaction_id=txn_id,
            reference=ref,
            amount_kobo=amount_kobo,
            worker_id=worker.id,
            payer_id=payer.id if payer else None,
            channel=channel,
            grade=grade,
            raw_payload=payload,
            status=PaymentEventStatus.PROCESSED,
        )
        db.add(payment_event)
        await db.flush()

        # 6. Job State Transition & Client Confirmation Prompt
        job = None
        if payer_phone:
            clean_client_phone = normalize_phone_number(str(payer_phone))

            # Look for existing job awaiting confirmation with matching amount & client
            job_stmt = (
                select(Job)
                .where(
                    Job.worker_id == worker.id,
                    Job.client_phone == clean_client_phone,
                    Job.status == JobStatus.AWAITING_CONFIRMATION,
                    Job.payment_event_id.is_(None),
                )
                .options(selectinload(Job.confirmation))
                .order_by(Job.created_at.desc())
            )
            job = (await db.execute(job_stmt)).scalars().first()

            if not job:
                # Grade 2 direct bank transfer: create new Job awaiting confirmation
                job = Job(
                    worker_id=worker.id,
                    payer_id=payer.id if payer else None,
                    payment_event_id=payment_event.id,
                    amount_kobo=amount_kobo,
                    job_type=JobType.OTHER,
                    status=JobStatus.AWAITING_CONFIRMATION,
                    grade=grade,
                    client_phone=clean_client_phone,
                )
                db.add(job)
                await db.flush()

                token = generate_secure_token(24)
                conf = Confirmation(
                    job_id=job.id,
                    confirmation_token=token,
                    channel=MessageChannel.HTTPSMS,
                    is_confirmed=False,
                    expires_at=datetime.now(UTC) + timedelta(hours=48),
                )
                db.add(conf)
                await db.flush()
            else:
                job.payment_event_id = payment_event.id
                conf = job.confirmation

            # Dispatch confirmation prompt to client via Unified Messaging Router
            if conf:
                await self.messaging_service.send_confirmation_prompt(
                    db=db,
                    worker_name=worker.name,
                    job_amount_kobo=amount_kobo,
                    client_phone=clean_client_phone,
                    confirmation_token=conf.confirmation_token,
                    job_type_name=job.job_type.value,
                )

        # 7. Alert Worker that money arrived on their account
        await self.messaging_service.send_money_arrived_alert(
            db=db,
            worker=worker,
            amount_kobo=amount_kobo,
            client_phone=payer_phone or "Client",
            is_confirmed=False,
        )

        return payment_event

    async def _get_or_create_payer(
        self,
        db: AsyncSession,
        phone: str,
        name: str | None = None,
    ) -> Payer:
        clean_phone = normalize_phone_number(phone)
        stmt = select(Payer).where(Payer.phone == clean_phone)
        res = await db.execute(stmt)
        payer = res.scalar_one_or_none()
        if not payer:
            payer = Payer(phone=clean_phone, name=name)
            db.add(payer)
            await db.flush()
        return payer
