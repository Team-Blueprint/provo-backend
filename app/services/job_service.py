import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.enums import DayStatus, JobStatus, TradeType
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
        days_breakdown: list[dict[str, Any]] | None = None,
    ) -> Job:
        """
        Creates a new escrow job draft and initializes its daily pay schedule.
        Supports multi-artisan assignment where individual days can be assigned to different artisans.
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

        # Build day records
        custom_breakdown_map = {item["day_number"]: item for item in (days_breakdown or [])}

        for idx, day_kobo in enumerate(daily_schedule, start=1):
            custom_info = custom_breakdown_map.get(idx, {})
            # If custom amount is provided, use it, else partition
            effective_amount = custom_info.get("amount_kobo") or day_kobo
            # If custom worker is assigned for this day, use them, otherwise default to job's worker_id
            effective_worker = custom_info.get("worker_id") or worker_id

            day_record = JobDay(
                job_id=job.id,
                worker_id=effective_worker,
                day_number=idx,
                amount_kobo=effective_amount,
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

    async def list_jobs(
        self,
        db: AsyncSession,
        user_id: uuid.UUID | None = None,
        is_admin: bool = False,
        status: JobStatus | None = None,
        trade: TradeType | None = None,
        worker_id: uuid.UUID | None = None,
        client_id: uuid.UUID | None = None,
        supervisor_id: uuid.UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Job]:
        """
        Lists jobs with filtering and pagination.
        Non-admin users can see jobs where they are the client, default worker, day-assigned worker, or supervisor,
        or public jobs needing an artisan (e.g., worker_id is None).
        """
        stmt = (
            select(Job)
            .options(
                selectinload(Job.client),
                selectinload(Job.worker),
                selectinload(Job.supervisor),
                selectinload(Job.days_schedule).selectinload(JobDay.worker),
                selectinload(Job.ledger_entries),
                selectinload(Job.payments_in),
                selectinload(Job.receipt),
            )
            .order_by(Job.created_at.desc())
        )

        # Access scope: if not admin and user_id is provided, limit to jobs they are party to,
        # or jobs currently unassigned/open
        if not is_admin and user_id:
            from sqlalchemy import or_

            stmt = stmt.outerjoin(Job.days_schedule).where(
                or_(
                    Job.client_id == user_id,
                    Job.worker_id == user_id,
                    JobDay.worker_id == user_id,
                    Job.supervisor_id == user_id,
                    Job.worker_id.is_(None),  # open job marketplace
                )
            ).distinct()

        # Filters
        if status:
            stmt = stmt.where(Job.status == status.value)
        if trade:
            stmt = stmt.where(Job.trade == trade.value)
        if worker_id:
            stmt = stmt.where(Job.worker_id == worker_id)
        if client_id:
            stmt = stmt.where(Job.client_id == client_id)
        if supervisor_id:
            stmt = stmt.where(Job.supervisor_id == supervisor_id)

        stmt = stmt.limit(limit).offset(offset)
        result = await db.execute(stmt)
        return list(result.scalars().all())

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
