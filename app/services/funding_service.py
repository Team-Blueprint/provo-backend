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

        # Compute cumulative received funds across all payments for this job (Spec: Section 4)
        stmt_all = select(PaymentIn).where(PaymentIn.job_id == job.id)
        all_payments = list((await db.execute(stmt_all)).scalars().all())
        cumulative_kobo = sum(p.amount_kobo for p in all_payments)
        prev_kobo = cumulative_kobo - amount_kobo
        now = datetime.now(UTC)

        if cumulative_kobo == job.total_kobo:
            # Case A: Exact Match
            if job.status != JobStatus.FUNDED.value:
                job.status = transition_job_state(job.status, JobEvent.PAYMENT_RECEIVED).value

            ledger = LedgerEntry(
                job_id=job.id,
                kind=LedgerKind.FUNDED.value,
                amount_kobo=amount_kobo,
                created_at=now,
            )
            db.add(ledger)

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
                    "cumulative_kobo": cumulative_kobo,
                    "status": job.status,
                },
            )
            db.add(audit)
            logger.info("Job %s fully funded (exact %s kobo). Status -> FUNDED", job.id, cumulative_kobo)

        elif cumulative_kobo < job.total_kobo:
            # Case B: Underfunding (e.g. split bank transfers or fee deduction)
            remaining_kobo = job.total_kobo - cumulative_kobo
            ledger = LedgerEntry(
                job_id=job.id,
                kind=LedgerKind.PARTIAL_FUNDED.value,
                amount_kobo=amount_kobo,
                created_at=now,
            )
            db.add(ledger)

            audit = AuditEvent(
                entity_name="jobs",
                entity_id=str(job.id),
                action="PARTIAL_ESCROW_FUNDED",
                actor="PAGA_WEBHOOK",
                payload={
                    "paga_event_id": paga_event_id,
                    "received_kobo": amount_kobo,
                    "cumulative_kobo": cumulative_kobo,
                    "remaining_kobo": remaining_kobo,
                },
            )
            db.add(audit)
            logger.warning(
                "Job %s partially funded. Received %s of %s kobo (%s kobo remaining). Status remains %s",
                job.id,
                cumulative_kobo,
                job.total_kobo,
                remaining_kobo,
                job.status,
            )

        else:
            # Case C: Overfunding (cumulative_kobo > job.total_kobo)
            if job.status != JobStatus.FUNDED.value:
                job.status = transition_job_state(job.status, JobEvent.PAYMENT_RECEIVED).value

            if job.days_schedule:
                day_1 = job.days_schedule[0]
                day_1.status = DayStatus.PENDING.value

            needed_kobo = max(0, job.total_kobo - prev_kobo)
            excess_kobo = amount_kobo - needed_kobo

            if needed_kobo > 0:
                db.add(
                    LedgerEntry(
                        job_id=job.id,
                        kind=LedgerKind.FUNDED.value,
                        amount_kobo=needed_kobo,
                        created_at=now,
                    )
                )

            if excess_kobo > 0:
                db.add(
                    LedgerEntry(
                        job_id=job.id,
                        kind=LedgerKind.EXCESS_HELD.value,
                        amount_kobo=excess_kobo,
                        created_at=now,
                    )
                )

            audit = AuditEvent(
                entity_name="jobs",
                entity_id=str(job.id),
                action="ESCROW_OVERFUNDED",
                actor="PAGA_WEBHOOK",
                payload={
                    "paga_event_id": paga_event_id,
                    "cumulative_kobo": cumulative_kobo,
                    "total_required_kobo": job.total_kobo,
                    "excess_kobo": excess_kobo,
                },
            )
            db.add(audit)
            logger.info(
                "Job %s overfunded. Total %s kobo locked; %s kobo held as excess for refund.",
                job.id,
                job.total_kobo,
                excess_kobo,
            )

        await db.flush()
        return job, payment_in
