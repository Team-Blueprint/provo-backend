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

    async def assign_worker_to_job(
        self,
        db: AsyncSession,
        job_id: uuid.UUID,
        actor_id: uuid.UUID,
        worker_phone: str | None = None,
        worker_id: uuid.UUID | None = None,
    ) -> Job:
        """
        Assigns (or re-assigns) an artisan to an existing unassigned job.
        Only the job's employer can call this, and only while job is DRAFT, AWAITING_FUNDING, or FUNDED.
        Accepts either worker_id or worker_phone.
        """
        from app.core.utils import normalize_phone_number
        from app.db.models import User, WorkerProfile

        if not worker_phone and not worker_id:
            raise ValueError("Either worker_phone or worker_id must be provided.")

        stmt = (
            select(Job)
            .where(Job.id == job_id)
            .options(selectinload(Job.days_schedule), selectinload(Job.worker))
        )
        job = (await db.execute(stmt)).scalar_one_or_none()
        if not job:
            raise ValueError("Job not found.")
        if job.client_id != actor_id:
            raise ValueError("Only the job's employer can assign an artisan.")
        if job.status not in (JobStatus.DRAFT.value, JobStatus.AWAITING_FUNDING.value, JobStatus.FUNDED.value):
            raise ValueError("Can only assign an artisan before work starts (DRAFT, AWAITING_FUNDING, or FUNDED status).")
        if job.worker_id is not None:
            raise ValueError("An artisan is already assigned to this job. Remove them first.")

        if worker_id:
            worker_stmt = select(User).where(User.id == worker_id).options(selectinload(User.worker_profile))
            worker = (await db.execute(worker_stmt)).scalar_one_or_none()
            if not worker:
                raise ValueError("Selected artisan not found.")
            if not worker.is_worker:
                worker.is_worker = True
                if not worker.worker_profile:
                    profile = WorkerProfile(user_id=worker.id, trade=job.trade)
                    db.add(profile)
                await db.flush()
        else:
            clean_phone = normalize_phone_number(worker_phone)
            worker_stmt = select(User).where(User.phone == clean_phone).options(selectinload(User.worker_profile))
            worker = (await db.execute(worker_stmt)).scalar_one_or_none()

            if not worker:
                worker = User(
                    name=f"Artisan ({clean_phone[-4:]})",
                    phone=clean_phone,
                    is_phone_verified=False,
                    is_worker=True,
                )
                db.add(worker)
                await db.flush()
                profile = WorkerProfile(user_id=worker.id, trade=job.trade)
                db.add(profile)
                await db.flush()
            else:
                if not worker.is_worker:
                    worker.is_worker = True
                    if not worker.worker_profile:
                        profile = WorkerProfile(user_id=worker.id, trade=job.trade)
                        db.add(profile)
                    await db.flush()

        job.worker_id = worker.id
        # Also update any unassigned days (days with no worker_id set)
        for day in job.days_schedule:
            if day.worker_id is None:
                day.worker_id = worker.id

        audit = AuditEvent(
            entity_name="jobs",
            entity_id=str(job.id),
            action="ASSIGN_WORKER",
            actor=str(actor_id),
            payload={
                "worker_id": str(worker.id),
                "worker_phone": worker.phone,
            },
        )
        db.add(audit)
        await db.flush()
        return job

    async def assign_supervisor(
        self,
        db: AsyncSession,
        job_id: uuid.UUID,
        actor_id: uuid.UUID,
        supervisor_name: str,
        supervisor_phone: str,
    ) -> Job:
        """
        Assigns an invited supervisor by name and phone number without requiring them to register.
        Generates a unique supervisor_token if not already set.
        Only the job's employer (client_id) or an admin can assign.
        """
        import secrets
        from app.core.utils import normalize_phone_number
        from app.db.models import User

        stmt = (
            select(Job)
            .where(Job.id == job_id)
            .options(
                selectinload(Job.days_schedule),
                selectinload(Job.worker),
            )
        )
        job = (await db.execute(stmt)).scalar_one_or_none()
        if not job:
            raise ValueError("Job not found.")
        if job.client_id != actor_id:
            actor_stmt = select(User).where(User.id == actor_id)
            actor = (await db.execute(actor_stmt)).scalar_one_or_none()
            if not actor or not actor.is_admin:
                raise ValueError("Unauthorized: Only the job's employer can assign a supervisor.")

        clean_phone = normalize_phone_number(supervisor_phone)
        clean_name = supervisor_name.strip()

        job.supervisor_name = clean_name
        job.supervisor_phone = clean_phone
        if not job.supervisor_token:
            job.supervisor_token = f"sup_{secrets.token_urlsafe(32)}"

        audit = AuditEvent(
            entity_name="jobs",
            entity_id=str(job.id),
            action="ASSIGN_SUPERVISOR",
            actor=str(actor_id),
            payload={
                "supervisor_name": clean_name,
                "supervisor_phone": clean_phone,
                "supervisor_token": job.supervisor_token,
            },
        )
        db.add(audit)
        await db.flush()
        return job
