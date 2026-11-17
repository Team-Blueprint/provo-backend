import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.enums import DayStatus, DisputeStatus, JobStatus, LedgerKind, PayoutStatus
from app.core.utils import kobo_to_naira_str
from app.db.models import (
    AuditEvent,
    Dispute,
    Job,
    JobDay,
    LedgerEntry,
    Payout,
    Receipt,
)
from app.domain.ledger import validate_ledger_invariant
from app.domain.lifecycle import (
    DayEvent,
    JobEvent,
    transition_day_state,
    transition_job_state,
)
from app.integrations.paga import PagaAdapter, get_paga_adapter

logger = logging.getLogger(__name__)


class ReleaseService:
    def __init__(self, paga_adapter: PagaAdapter | None = None) -> None:
        self.paga_adapter = paga_adapter or get_paga_adapter()

    async def report_day_done(
        self,
        db: AsyncSession,
        job_id: uuid.UUID,
        day_number: int,
        worker_user_id: uuid.UUID,
    ) -> JobDay:
        """
        Worker reports a working day as 'done'.
        Validates sequential progress and initiates the 24-hour confirmation window.
        """
        stmt = (
            select(Job)
            .where(Job.id == job_id)
            .options(
                selectinload(Job.days_schedule),
                selectinload(Job.client),
                selectinload(Job.supervisor),
                selectinload(Job.worker),
            )
        )
        job = (await db.execute(stmt)).scalar_one_or_none()
        if not job:
            raise ValueError("Job not found")

        # Find targeted day
        day = next((d for d in job.days_schedule if d.day_number == day_number), None)
        if not day:
            raise ValueError(f"Day {day_number} not found for this job")

        # Artisan check: must be either the specific day artisan or the default job artisan
        effective_worker_id = day.worker_id or job.worker_id
        if effective_worker_id != worker_user_id:
            raise ValueError("Unauthorized: Only the assigned artisan for this day can report work done.")

        if job.status not in (JobStatus.FUNDED.value, JobStatus.IN_PROGRESS.value):
            raise ValueError(f"Cannot report work for job with status '{job.status}'")

        if day.status != DayStatus.PENDING.value:
            raise ValueError(f"Day {day_number} cannot be reported done (status: {day.status})")

        # Ensure previous days are already settled
        for prev_day in job.days_schedule:
            if prev_day.day_number < day_number and prev_day.status in (
                DayStatus.PENDING.value,
                DayStatus.DONE_REPORTED.value,
            ):
                raise ValueError(
                    f"Day {prev_day.day_number} must be settled before Day {day_number} can be reported."
                )

        now = datetime.now(UTC)
        day.status = transition_day_state(day.status, DayEvent.WORKER_DONE).value
        day.done_at = now
        day.release_at = now + timedelta(hours=settings.AUTO_RELEASE_WINDOW_HOURS)

        # Transition job to IN_PROGRESS if funded
        if job.status == JobStatus.FUNDED.value:
            job.status = transition_job_state(job.status, JobEvent.START_WORK).value

        audit = AuditEvent(
            entity_name="job_days",
            entity_id=str(day.id),
            action="REPORT_DAY_DONE",
            actor=str(worker_user_id),
            payload={
                "job_id": str(job.id),
                "day_number": day_number,
                "release_at": day.release_at.isoformat(),
            },
        )
        db.add(audit)
        await db.flush()

        logger.info(
            "Job %s Day %s reported done by worker %s. Release window open until %s",
            job.id,
            day_number,
            worker_user_id,
            day.release_at,
        )
        return day

    async def confirm_day(
        self,
        db: AsyncSession,
        job_id: uuid.UUID,
        day_number: int,
        actor_user_id: uuid.UUID,
    ) -> tuple[JobDay, Payout]:
        """
        Client or Supervisor confirms daily work.
        Creates payout outbox record and writes RELEASED ledger entry.
        """
        stmt = (
            select(Job)
            .where(Job.id == job_id)
            .options(
                selectinload(Job.days_schedule).selectinload(JobDay.worker),
                selectinload(Job.ledger_entries),
                selectinload(Job.worker),
                selectinload(Job.receipt),
            )
        )
        job = (await db.execute(stmt)).scalar_one_or_none()
        if not job:
            raise ValueError("Job not found")

        # Authorization: client or supervisor
        is_client = job.client_id == actor_user_id
        is_supervisor = job.supervisor_id == actor_user_id if job.supervisor_id else False
        if not (is_client or is_supervisor):
            raise ValueError("Unauthorized: Only the job client or supervisor can confirm work.")

        day = next((d for d in job.days_schedule if d.day_number == day_number), None)
        if not day:
            raise ValueError(f"Day {day_number} not found for this job")

        if day.status != DayStatus.DONE_REPORTED.value:
            raise ValueError(f"Cannot confirm day with status '{day.status}'")

        now = datetime.now(UTC)
        day.status = transition_day_state(day.status, DayEvent.CLIENT_CONFIRM).value
        day.decided_by = actor_user_id
        day.decided_at = now

        payout = await self._settle_day_release(
            db=db,
            job=job,
            day=day,
            actor="CLIENT" if is_client else "SUPERVISOR",
            reason=f"Day {day_number} confirmed by authorized reviewer",
        )
        return day, payout

    async def dispute_day(
        self,
        db: AsyncSession,
        job_id: uuid.UUID,
        day_number: int,
        actor_user_id: uuid.UUID,
        reason: str,
    ) -> tuple[JobDay, Dispute]:
        """
        Client or Supervisor disputes a day's work.
        Freezes the 24-hour countdown and records the dispute.
        """
        stmt = (
            select(Job)
            .where(Job.id == job_id)
            .options(selectinload(Job.days_schedule))
        )
        job = (await db.execute(stmt)).scalar_one_or_none()
        if not job:
            raise ValueError("Job not found")

        is_client = job.client_id == actor_user_id
        is_supervisor = job.supervisor_id == actor_user_id if job.supervisor_id else False
        if not (is_client or is_supervisor):
            raise ValueError("Unauthorized: Only client or supervisor can dispute work.")

        day = next((d for d in job.days_schedule if d.day_number == day_number), None)
        if not day:
            raise ValueError(f"Day {day_number} not found for this job")

        if day.status != DayStatus.DONE_REPORTED.value:
            raise ValueError(f"Cannot dispute day with status '{day.status}'")

        now = datetime.now(UTC)
        day.status = transition_day_state(day.status, DayEvent.CLIENT_DISPUTE).value
        day.release_at = None  # Freeze 24h timer
        day.decided_by = actor_user_id
        day.decided_at = now

        dispute = Dispute(
            day_id=day.id,
            opened_by=actor_user_id,
            reason=reason.strip(),
            status=DisputeStatus.OPEN.value,
        )
        db.add(dispute)

        audit = AuditEvent(
            entity_name="job_days",
            entity_id=str(day.id),
            action="DISPUTE_DAY_WORK",
            actor=str(actor_user_id),
            payload={"reason": reason},
        )
        db.add(audit)
        await db.flush()

        logger.warning("Job %s Day %s disputed by %s: %s", job.id, day_number, actor_user_id, reason)
        return day, dispute

    async def check_and_settle_job(self, db: AsyncSession, job_id: uuid.UUID) -> Receipt | None:
        """Checks if all days in a job are settled (PAID or CANCELLED) and mints receipt if ready."""
        stmt = (
            select(Job)
            .where(Job.id == job_id)
            .options(
                selectinload(Job.days_schedule),
                selectinload(Job.worker),
                selectinload(Job.receipt),
            )
        )
        job = (await db.execute(stmt)).scalar_one_or_none()
        if not job or job.receipt:
            return job.receipt if job else None

        all_settled = all(
            d.status in (DayStatus.PAID.value, DayStatus.CONFIRMED.value, DayStatus.AUTO_RELEASED.value, "CANCELLED")
            for d in job.days_schedule
        )
        if all_settled:
            now = datetime.now(UTC)
            job.status = transition_job_state(job.status, JobEvent.ALL_DAYS_SETTLED).value
            receipt_num = f"PW-{now.strftime('%Y')}-{uuid.uuid4().hex[:8].upper()}"
            worker_name = job.worker.name if job.worker else "Artisan"

            receipt = Receipt(
                receipt_number=receipt_num,
                job_id=job.id,
                issued_at=now,
                receipt_data={
                    "receipt_number": receipt_num,
                    "job_id": str(job.id),
                    "trade": job.trade,
                    "description": job.description,
                    "total_kobo": job.total_kobo,
                    "total_naira": kobo_to_naira_str(job.total_kobo),
                    "days": job.days,
                    "worker_name": worker_name,
                    "completed_at": now.isoformat(),
                },
            )
            db.add(receipt)
            await db.flush()
            logger.info("Settled Job %s and minted Receipt %s", job.id, receipt_num)
            return receipt
        return None

    async def auto_release_day(
        self,
        db: AsyncSession,
        day_id: uuid.UUID,
    ) -> tuple[JobDay, Payout]:
        """
        Invoked by the background scheduler when 24-hour window lapses without dispute.
        """
        stmt = (
            select(JobDay)
            .where(JobDay.id == day_id)
            .options(
                selectinload(JobDay.job).selectinload(Job.days_schedule),
                selectinload(JobDay.job).selectinload(Job.ledger_entries),
                selectinload(JobDay.job).selectinload(Job.worker),
                selectinload(JobDay.job).selectinload(Job.receipt),
            )
        )
        day = (await db.execute(stmt)).scalar_one_or_none()
        if not day or day.status != DayStatus.DONE_REPORTED.value:
            raise ValueError(f"Day {day_id} not eligible for auto-release")

        now = datetime.now(UTC)
        day.status = transition_day_state(day.status, DayEvent.TIMER_EXPIRED).value
        day.decided_at = now

        payout = await self._settle_day_release(
            db=db,
            job=day.job,
            day=day,
            actor="SYSTEM_AUTO_RELEASE",
            reason=f"Day {day.day_number} released automatically after 24h window",
        )
        return day, payout

    async def _settle_day_release(
        self,
        db: AsyncSession,
        job: Job,
        day: JobDay,
        actor: str,
        reason: str,
    ) -> Payout:
        """
        Internal settlement helper executing the outbox pattern:
        1. Creates Payout row.
        2. Writes RELEASED ledger entry.
        3. Validates ledger invariant.
        4. Triggers Paga disbursement and marks PAID.
        5. Mints receipt if final day.
        """
        now = datetime.now(UTC)
        payout_ref = f"PAYOUT-{day.id}"

        # 1. Create or get Payout outbox record
        stmt = select(Payout).where(Payout.day_id == day.id)
        payout = (await db.execute(stmt)).scalar_one_or_none()
        if not payout:
            payout = Payout(
                day_id=day.id,
                amount_kobo=day.amount_kobo,
                idempotency_ref=payout_ref,
                status=PayoutStatus.PENDING.value,
            )
            db.add(payout)

        # 2. Add RELEASED ledger entry
        ledger = LedgerEntry(
            job_id=job.id,
            day_id=day.id,
            kind=LedgerKind.RELEASED.value,
            amount_kobo=day.amount_kobo,
            created_at=now,
        )
        db.add(ledger)
        await db.flush()

        # 3. Validate ledger invariant
        released_kobo = sum(
            entry.amount_kobo
            for entry in job.ledger_entries
            if entry.kind == LedgerKind.RELEASED.value
        )
        refunded_kobo = sum(
            entry.amount_kobo
            for entry in job.ledger_entries
            if entry.kind == LedgerKind.REFUNDED.value
        )
        validate_ledger_invariant(
            funded_kobo=job.total_kobo,
            released_kobo=released_kobo,
            refunded_kobo=refunded_kobo,
        )

        # 4. Trigger Paga Payout to the day's designated artisan (or fallback to job artisan)
        artisan = day.worker or job.worker
        destination = (
            artisan.payout_account_number
            if artisan and artisan.payout_account_number
            else "9900000001"
        )
        bank_code = (
            artisan.payout_bank_code
            if artisan and artisan.payout_bank_code
            else "PAGA"
        )
        worker_name = artisan.name if artisan else "Artisan"

        try:
            payout_res = await self.paga_adapter.disburse_payout(
                payout_ref=payout_ref,
                destination_account=destination,
                bank_code=bank_code,
                amount_kobo=day.amount_kobo,
                recipient_name=worker_name,
            )
            payout.status = PayoutStatus.SUCCESS.value
            payout.paga_response = payout_res.raw_response
            day.status = transition_day_state(day.status, DayEvent.PAYOUT_SUCCESS).value
        except Exception as e:
            logger.error("Failed to disburse payout for day %s: %s", day.id, e)
            payout.status = PayoutStatus.FAILED.value
            payout.attempts += 1

        # 5. Check if all days settled -> Mint Proofwork Receipt
        all_settled = all(
            d.status in (DayStatus.PAID.value, DayStatus.CONFIRMED.value, DayStatus.AUTO_RELEASED.value)
            for d in job.days_schedule
        )
        if all_settled and not job.receipt:
            job.status = transition_job_state(job.status, JobEvent.ALL_DAYS_SETTLED).value
            receipt_year = now.strftime("%Y")
            receipt_num = f"PW-{receipt_year}-{uuid.uuid4().hex[:8].upper()}"

            receipt = Receipt(
                receipt_number=receipt_num,
                job_id=job.id,
                issued_at=now,
                receipt_data={
                    "receipt_number": receipt_num,
                    "job_id": str(job.id),
                    "trade": job.trade,
                    "description": job.description,
                    "total_kobo": job.total_kobo,
                    "total_naira": kobo_to_naira_str(job.total_kobo),
                    "days": job.days,
                    "worker_name": worker_name,
                    "completed_at": now.isoformat(),
                },
            )
            db.add(receipt)
            logger.info("All days settled. Minted Proofwork Receipt %s for Job %s", receipt_num, job.id)

        audit = AuditEvent(
            entity_name="job_days",
            entity_id=str(day.id),
            action="SETTLE_DAY_RELEASE",
            actor=actor,
            payload={
                "reason": reason,
                "amount_kobo": day.amount_kobo,
                "payout_status": payout.status,
            },
        )
        db.add(audit)
        await db.flush()
        return payout
