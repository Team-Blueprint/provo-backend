import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.enums import DayStatus, JobStatus
from app.db.models import AuditEvent, Job, JobDay
from app.domain.ledger import partition_daily_pay

logger = logging.getLogger(__name__)


class JobService:
    async def create_job(
        self,
        db: AsyncSession,
        client_id: uuid.UUID,
        trade: str,
        description: str,
        total_kobo: int,
        days: int = 1,
        worker_id: uuid.UUID | None = None,
        supervisor_id: uuid.UUID | None = None,
    ) -> Job:
        """
        Creates a new escrow job draft and initializes its daily pay schedule.
        """
        daily_schedule = partition_daily_pay(total_kobo=total_kobo, days=days)
        funding_ref = f"PW-ESCROW-{uuid.uuid4().hex[:12].upper()}"

        job = Job(
            client_id=client_id,
            worker_id=worker_id,
            supervisor_id=supervisor_id,
            trade=trade.strip(),
            description=description.strip(),
            total_kobo=total_kobo,
            days=days,
            status=JobStatus.DRAFT.value,
            funding_reference=funding_ref,
        )
        db.add(job)
        await db.flush()

        for idx, day_kobo in enumerate(daily_schedule, start=1):
            day_record = JobDay(
                job_id=job.id,
                day_number=idx,
                amount_kobo=day_kobo,
                status=DayStatus.PENDING.value,
            )
            db.add(day_record)

        audit = AuditEvent(
            entity_name="jobs",
            entity_id=str(job.id),
            action="CREATE_JOB_DRAFT",
            actor=str(client_id),
            payload={
                "trade": trade,
                "total_kobo": total_kobo,
                "days": days,
                "funding_reference": funding_ref,
            },
        )
        db.add(audit)
        await db.flush()

        logger.info(
            "Created Escrow Job %s (%s days, %s kobo) for client %s",
            job.id,
            days,
            total_kobo,
            client_id,
        )
        return job

    async def get_job_by_id(self, db: AsyncSession, job_id: uuid.UUID) -> Job | None:
        stmt = (
            select(Job)
            .where(Job.id == job_id)
            .options(
                selectinload(Job.client),
                selectinload(Job.worker),
                selectinload(Job.supervisor),
                selectinload(Job.days_schedule),
                selectinload(Job.ledger_entries),
                selectinload(Job.payments_in),
                selectinload(Job.receipt),
            )
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    async def assign_worker(
        self,
        db: AsyncSession,
        job_id: uuid.UUID,
        worker_id: uuid.UUID,
    ) -> Job:
        """Assigns an artisan to an existing job."""
        job = await self.get_job_by_id(db, job_id)
        if not job:
            raise ValueError("Job not found")

        job.worker_id = worker_id
        audit = AuditEvent(
            entity_name="jobs",
            entity_id=str(job.id),
            action="ASSIGN_WORKER",
            actor="SYSTEM",
            payload={"worker_id": str(worker_id)},
        )
        db.add(audit)
        await db.flush()
        return job
