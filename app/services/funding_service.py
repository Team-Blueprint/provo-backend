import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.enums import DayStatus, JobStatus, LedgerKind
from app.db.models import AuditEvent, Job, LedgerEntry, PaymentIn
from app.domain.lifecycle import JobEvent, transition_job_state
from app.integrations.paga import PagaAdapter, get_paga_adapter

logger = logging.getLogger(__name__)


class FundingService:
    def __init__(self, paga_adapter: PagaAdapter | None = None) -> None:
        self.paga_adapter = paga_adapter or get_paga_adapter()

    async def initiate_job_escrow(self, db: AsyncSession, job_id: uuid.UUID) -> Job:
        """
        Requests an escrow payment link and dedicated NUBAN from Paga for a job.
        Transitions job state to AWAITING_FUNDING.
        """
        stmt = select(Job).where(Job.id == job_id).options(selectinload(Job.client))
        job = (await db.execute(stmt)).scalar_one_or_none()
        if not job:
            raise ValueError("Job not found")

        if job.status != JobStatus.DRAFT.value:
            raise ValueError(f"Cannot request escrow for job with status '{job.status}'")

        callback_url = f"{settings.BASE_URL}/webhooks/paga"
        result = await self.paga_adapter.create_escrow_payment_request(
            reference=job.funding_reference,
            amount_kobo=job.total_kobo,
            client_phone=job.client.phone if job.client else "",
            client_name=job.client.name if job.client else "Client",
            description=f"Proofwork Escrow for {job.trade}: {job.description}",
            callback_url=callback_url,
        )

        job.paga_payment_url = result.payment_url
        job.paga_account_number = result.account_number
        job.status = transition_job_state(job.status, JobEvent.REQUEST_FUNDING).value

        audit = AuditEvent(
            entity_name="jobs",
            entity_id=str(job.id),
            action="REQUEST_ESCROW_FUNDING",
            actor="SYSTEM",
            payload={"account_number": result.account_number, "payment_url": result.payment_url},
        )
        db.add(audit)
        await db.flush()

        logger.info("Initiated escrow funding for Job %s (ref=%s)", job.id, job.funding_reference)
        return job

    async def process_paga_webhook(
        self,
        db: AsyncSession,
        payload: dict[str, Any],
        raw_body: bytes,
        signature: str | None,
    ) -> tuple[Job, PaymentIn]:
        """
        Processes Paga Collect callback:
        1. Validates HMAC signature.
        2. Deduplicates using payments_in.paga_event_id.
        3. Matches job and validates exact amount.
        4. Transitions job to FUNDED and creates FUNDED ledger entry.
        """
        # Signature verification
        if signature and not self.paga_adapter.verify_webhook_signature(raw_body, signature):
            logger.warning("Paga webhook signature verification failed.")
            raise ValueError("Invalid Paga webhook signature")

        paga_event_id = str(
            payload.get("transactionId")
            or payload.get("referenceNumber")
            or payload.get("event_id")
            or uuid.uuid4().hex
        )

        # Idempotency check
        existing_stmt = select(PaymentIn).where(PaymentIn.paga_event_id == paga_event_id)
        existing_payment = (await db.execute(existing_stmt)).scalar_one_or_none()
        if existing_payment:
            logger.info("Paga webhook event %s already processed (idempotent).", paga_event_id)
            job_stmt = select(Job).where(Job.id == existing_payment.job_id)
            job = (await db.execute(job_stmt)).scalar_one()
            return job, existing_payment

        # Look up Job by funding reference
        reference = payload.get("referenceNumber") or payload.get("merchantTransactionId")
        if not reference:
            raise ValueError("Webhook payload missing referenceNumber")

        job_stmt = (
            select(Job)
            .where(Job.funding_reference == reference)
            .options(selectinload(Job.days_schedule))
        )
        job = (await db.execute(job_stmt)).scalar_one_or_none()
        if not job:
            raise ValueError(f"No job found matching funding reference: {reference}")

        # Amount validation
        amount_raw = payload.get("amount", 0)
        amount_kobo = round(float(amount_raw) * 100)

        payer_name = payload.get("payerName") or payload.get("payer", {}).get("name")
        payer_phone = payload.get("payerPhoneNumber") or payload.get("payer", {}).get("phoneNumber")
        payer_account = payload.get("payerAccountNumber")

        # Record payment event in DB
        payment_in = PaymentIn(
            paga_event_id=paga_event_id,
            job_id=job.id,
            amount_kobo=amount_kobo,
            payer_name=payer_name,
            payer_phone=payer_phone,
            payer_account_number=payer_account,
            raw_payload=payload,
        )
        db.add(payment_in)
        await db.flush()

        # Check exact funding match
        if amount_kobo == job.total_kobo:
            job.status = transition_job_state(job.status, JobEvent.PAYMENT_RECEIVED).value

            # Record internal ledger entry
            ledger = LedgerEntry(
                job_id=job.id,
                kind=LedgerKind.FUNDED.value,
                amount_kobo=amount_kobo,
                created_at=datetime.now(UTC),
            )
            db.add(ledger)

            # Ensure Day 1 is in PENDING state
            if job.days_schedule:
                day_1 = job.days_schedule[0]
                day_1.status = DayStatus.PENDING.value

            audit = AuditEvent(
                entity_name="jobs",
                entity_id=str(job.id),
                action="JOB_ESCROW_FUNDED",
                actor="PAGA_WEBHOOK",
                payload={
                    "paga_event_id": paga_event_id,
                    "amount_kobo": amount_kobo,
                    "status": job.status,
                },
            )
            db.add(audit)
            logger.info("Job %s funded with %s kobo. Status -> %s", job.id, amount_kobo, job.status)
        else:
            logger.warning(
                "Job %s funding mismatch! Expected %s kobo, got %s kobo.",
                job.id,
                job.total_kobo,
                amount_kobo,
            )
            audit = AuditEvent(
                entity_name="jobs",
                entity_id=str(job.id),
                action="FUNDING_AMOUNT_MISMATCH",
                actor="PAGA_WEBHOOK",
                payload={"expected_kobo": job.total_kobo, "received_kobo": amount_kobo},
            )
            db.add(audit)

        await db.flush()
        return job, payment_in
