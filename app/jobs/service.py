import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.enums import JobStatus, JobType, MessageChannel, VerificationGrade
from app.core.security import generate_secure_token
from app.core.utils import kobo_to_naira_str, normalize_phone_number
from app.jobs.lifecycle import transition_job_state
from app.jobs.models import Confirmation, Dispute, Job, Receipt
from app.payments.models import Payer

logger = logging.getLogger(__name__)


class JobService:
    async def create_cash_job_draft(
        self,
        db: AsyncSession,
        worker_id: uuid.UUID,
        amount_kobo: int,
        client_phone: str,
        job_type: JobType,
        channel: MessageChannel = MessageChannel.HTTPSMS,
        expiry_hours: int = 48,
    ) -> tuple[Job, Confirmation]:
        """
        Creates an unverified cash job draft logged by a worker.
        Crucial Rule: Drafts carry zero value until the client confirms.
        """
        clean_phone = normalize_phone_number(client_phone)

        # 1. Payer lookup / creation
        stmt = select(Payer).where(Payer.phone == clean_phone)
        payer = (await db.execute(stmt)).scalar_one_or_none()
        if not payer:
            payer = Payer(phone=clean_phone, name="Cash Client")
            db.add(payer)
            await db.flush()

        # 2. Create Job in DRAFT status
        job = Job(
            worker_id=worker_id,
            payer_id=payer.id,
            amount_kobo=amount_kobo,
            job_type=job_type,
            status=JobStatus.DRAFT,
            grade=VerificationGrade.GRADE_3_CASH,
            client_phone=clean_phone,
        )
        db.add(job)
        await db.flush()

        # 3. Create single-use Confirmation Token
        token = generate_secure_token(24)
        expires_at = datetime.now(UTC) + timedelta(hours=expiry_hours)

        confirmation = Confirmation(
            job_id=job.id,
            confirmation_token=token,
            channel=channel,
            is_confirmed=False,
            expires_at=expires_at,
        )
        db.add(confirmation)
        await db.flush()

        logger.info(
            "Created Cash Job Draft %s for worker %s, awaiting client confirmation",
            job.id,
            worker_id,
        )
        return job, confirmation

    async def get_confirmation_by_token(
        self,
        db: AsyncSession,
        confirmation_token: str,
    ) -> Confirmation | None:
        """Retrieves confirmation and eager-loads the job, worker, and receipt."""
        stmt = (
            select(Confirmation)
            .where(Confirmation.confirmation_token == confirmation_token)
            .options(
                selectinload(Confirmation.job).selectinload(Job.worker),
                selectinload(Confirmation.job).selectinload(Job.payer),
                selectinload(Confirmation.job).selectinload(Job.receipt),
            )
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    async def confirm_job(
        self,
        db: AsyncSession,
        confirmation_token: str,
        job_type: JobType | None = None,
        actor: str = "CLIENT",
    ) -> tuple[Job, Receipt]:
        """
        Client confirms the job was legitimately performed for work.
        Transitions state to CONFIRMED and mints an immutable Proofwork Receipt.
        """
        conf = await self.get_confirmation_by_token(db, confirmation_token)

        if not conf:
            raise ValueError("Invalid confirmation token")

        if conf.is_confirmed:
            raise ValueError("Job has already been confirmed")

        now = datetime.now(UTC)
        if conf.expires_at < now:
            await transition_job_state(
                db=db,
                job=conf.job,
                to_status=JobStatus.EXPIRED,
                actor="SYSTEM",
                reason="Confirmation link expired before client responded",
            )
            raise ValueError("Confirmation token has expired")

        job = conf.job

        # Update Job Type if specified by client
        if job_type:
            job.job_type = job_type

        # Transition state using central state machine
        await transition_job_state(
            db=db,
            job=job,
            to_status=JobStatus.CONFIRMED,
            actor=actor,
            reason=f"Client confirmed work via token on {conf.channel.value}",
        )

        conf.is_confirmed = True
        conf.confirmed_at = now

        # Mint Receipt
        receipt_year = now.strftime("%Y")
        receipt_suffix = uuid.uuid4().hex[:8].upper()
        receipt_number = f"PW-{receipt_year}-{receipt_suffix}"

        receipt_data: dict[str, Any] = {
            "receipt_number": receipt_number,
            "job_id": str(job.id),
            "worker_name": job.worker.name if job.worker else "Unknown",
            "worker_trade": job.worker.trade if job.worker else "Trade",
            "amount_kobo": job.amount_kobo,
            "amount_formatted": kobo_to_naira_str(job.amount_kobo),
            "job_type": job.job_type.value,
            "grade": job.grade.value,
            "client_phone": job.client_phone,
            "confirmed_at": now.isoformat(),
        }

        receipt = Receipt(
            job_id=job.id,
            receipt_number=receipt_number,
            issued_at=now,
            receipt_data=receipt_data,
        )
        db.add(receipt)
        await db.flush()

        logger.info("Job %s confirmed. Issued Receipt %s", job.id, receipt_number)
        return job, receipt

    async def dispute_job(
        self,
        db: AsyncSession,
        confirmation_token: str,
        reason: str = "Client answered NO to confirmation prompt",
        actor: str = "CLIENT",
    ) -> tuple[Job, Dispute]:
        """Client marks payment as NOT for work."""
        conf = await self.get_confirmation_by_token(db, confirmation_token)
        if not conf:
            raise ValueError("Invalid confirmation token")

        job = conf.job

        await transition_job_state(
            db=db,
            job=job,
            to_status=JobStatus.DISPUTED,
            actor=actor,
            reason=reason,
        )

        dispute = Dispute(
            job_id=job.id,
            reason=reason,
            status="OPEN",
        )
        db.add(dispute)
        await db.flush()

        logger.warning("Job %s marked DISPUTED: %s", job.id, reason)
        return job, dispute

    async def verify_receipt_by_number(
        self,
        db: AsyncSession,
        receipt_number: str,
    ) -> Receipt | None:
        """Public endpoint to verify a Proofwork receipt."""
        stmt = (
            select(Receipt)
            .where(Receipt.receipt_number == receipt_number.strip().upper())
            .options(
                selectinload(Receipt.job).selectinload(Job.worker),
            )
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    async def expire_stale_confirmations(self, db: AsyncSession) -> int:
        """
        Background task to mark unanswered confirmations past expiration as EXPIRED.
        """
        now = datetime.now(UTC)
        stmt = (
            select(Confirmation)
            .join(Job, Confirmation.job_id == Job.id)
            .where(
                Confirmation.is_confirmed.is_(False),
                Confirmation.expires_at < now,
                Job.status.in_([JobStatus.DRAFT, JobStatus.AWAITING_CONFIRMATION]),
            )
            .options(selectinload(Confirmation.job))
        )
        stale_confs = (await db.execute(stmt)).scalars().all()
        expired_count = 0

        for conf in stale_confs:
            await transition_job_state(
                db=db,
                job=conf.job,
                to_status=JobStatus.EXPIRED,
                actor="SYSTEM_EXPIRATION_TASK",
                reason="Confirmation expired without client response",
            )
            expired_count += 1

        return expired_count
