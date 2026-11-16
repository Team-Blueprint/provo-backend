import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory
from app.core.enums import DayStatus
from app.db.models import JobDay
from app.services.release_service import ReleaseService

logger = logging.getLogger(__name__)
release_service = ReleaseService()


async def scan_and_auto_release_expired_days(db: AsyncSession) -> int:
    """
    Scans for work days in DONE_REPORTED status where the 24-hour review window has passed.
    Releases day pay automatically (discounted score weight) and triggers payout.
    """
    now = datetime.now(UTC)

    # Note: SQLite does not support FOR UPDATE SKIP LOCKED; asyncpg/Postgres does.
    # We gracefully fall back based on dialect.
    stmt = (
        select(JobDay)
        .where(
            JobDay.status == DayStatus.DONE_REPORTED.value,
            JobDay.release_at.is_not(None),
            JobDay.release_at <= now,
        )
    )

    try:
        bind = db.get_bind()
        if bind.dialect.name == "postgresql":
            stmt = stmt.with_for_update(skip_locked=True)
    except Exception as e:
        logger.debug("Skipping FOR UPDATE SKIP LOCKED on unsupported dialect: %s", e)

    expired_days = list((await db.execute(stmt)).scalars().all())
    count = 0

    for day in expired_days:
        try:
            await release_service.auto_release_day(db=db, day_id=day.id)
            count += 1
            logger.info("Auto-released Day %s (Job %s) after 24h window.", day.day_number, day.job_id)
        except Exception as e:
            logger.error("Failed to auto-release day %s: %s", day.id, e)

    if count > 0:
        await db.commit()

    return count


async def run_scheduler_background_task(poll_interval_seconds: int = 60, stop_event: asyncio.Event | None = None) -> None:
    """
    Long-running background task loop executed during FastAPI lifespan or in worker container.
    """
    logger.info("Starting Proofwork 24-hour auto-release scheduler (interval=%ss)...", poll_interval_seconds)
    while stop_event is None or not stop_event.is_set():
        try:
            async with async_session_factory() as session:
                released_count = await scan_and_auto_release_expired_days(session)
                if released_count > 0:
                    logger.info("Scheduler cycle completed: %s day(s) auto-released.", released_count)
        except Exception as e:
            logger.error("Error in scheduler loop: %s", e)

        try:
            await asyncio.sleep(poll_interval_seconds)
        except asyncio.CancelledError:
            logger.info("Scheduler background loop cancelled.")
            break
