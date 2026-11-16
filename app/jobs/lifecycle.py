import logging
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import JobStatus
from app.jobs.models import Job
from app.reports.models import AuditLog

logger = logging.getLogger(__name__)

# Allowed state transitions in Proofwork
VALID_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.DRAFT: {JobStatus.AWAITING_CONFIRMATION, JobStatus.CONFIRMED, JobStatus.DISPUTED, JobStatus.EXPIRED},
    JobStatus.AWAITING_CONFIRMATION: {JobStatus.CONFIRMED, JobStatus.DISPUTED, JobStatus.EXPIRED},
    JobStatus.CONFIRMED: {JobStatus.DISPUTED},  # Allowed only within dispute window
    JobStatus.DISPUTED: set(),                 # Terminal
    JobStatus.EXPIRED: set(),                  # Terminal
}

DISPUTE_WINDOW_DAYS = 7


async def transition_job_state(
    db: AsyncSession,
    job: Job,
    to_status: JobStatus,
    actor: str,
    reason: str,
) -> Job:
    """
    Central state machine enforcer for all Job transitions.
    1. Validates that the requested transition is permitted.
    2. Enforces dispute window policy.
    3. Commits an immutable record to `audit_log`.
    4. Updates the job status.
    """
    current_status = job.status

    if to_status == current_status:
        return job

    allowed = VALID_TRANSITIONS.get(current_status, set())
    if to_status not in allowed:
        raise ValueError(
            f"Illegal state transition from {current_status.value} to {to_status.value}"
        )

    now = datetime.now(UTC)

    # Enforce dispute window policy on already confirmed jobs
    if current_status == JobStatus.CONFIRMED and to_status == JobStatus.DISPUTED and job.updated_at:
        delta = now - job.updated_at
        if delta.days > DISPUTE_WINDOW_DAYS:
            raise ValueError(
                f"Dispute window of {DISPUTE_WINDOW_DAYS} days has expired for this job"
            )

    # Record in audit log
    audit_entry = AuditLog(
        entity_name="jobs",
        entity_id=str(job.id),
        action=f"TRANSITION_{current_status.value}_TO_{to_status.value}",
        actor=actor,
        changes={
            "from_status": current_status.value,
            "to_status": to_status.value,
            "reason": reason,
            "timestamp": now.isoformat(),
        },
    )
    db.add(audit_entry)

    # Perform transition
    job.status = to_status
    await db.flush()

    logger.info(
        "Job %s transitioned from %s to %s by %s. Reason: %s",
        job.id,
        current_status.value,
        to_status.value,
        actor,
        reason,
    )
    return job
