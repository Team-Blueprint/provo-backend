from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal, Base, engine
from app.db.models import Job, JobDay, LedgerEntry, Payout, User
from app.workers.scheduler import scan_and_auto_release_expired_days


@pytest.fixture(autouse=True)
async def prepare_database():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.mark.asyncio
async def test_scheduler_auto_releases_days_past_24h_window():
    now = datetime.now(UTC)
    past_release = now - timedelta(hours=1)  # Window expired 1 hour ago

    async with AsyncSessionLocal() as session:
        c_user = User(name="Client", phone="+2348055551111", is_client=True, is_phone_verified=True)
        w_user = User(name="Artisan", phone="+2348055552222", is_worker=True, is_phone_verified=True, payout_account_number="9900223344")
        session.add_all([c_user, w_user])
        await session.commit()

        job = Job(
            client_id=c_user.id,
            worker_id=w_user.id,
            trade="CARPENTRY",
            description="Build wooden shelves",
            total_kobo=2500000,
            days=1,
            status="IN_PROGRESS",
            funding_reference="PW-ESCROW-SCHEDULER-TEST",
        )
        session.add(job)
        await session.flush()
        session.add(LedgerEntry(job_id=job.id, kind="FUNDED", amount_kobo=2500000))

        day = JobDay(
            job_id=job.id,
            day_number=1,
            amount_kobo=2500000,
            status="DONE_REPORTED",
            done_at=past_release - timedelta(hours=24),
            release_at=past_release,  # Expired
        )
        session.add(day)
        await session.commit()
        day_id = day.id

    # Run scheduler scan
    async with AsyncSessionLocal() as session:
        count = await scan_and_auto_release_expired_days(session)
        assert count == 1

    # Verify day transitioned to AUTO_RELEASED or PAID
    async with AsyncSessionLocal() as session:
        stmt = select(JobDay).where(JobDay.id == day_id)
        updated_day = (await session.execute(stmt)).scalar_one()
        assert updated_day.status in ("AUTO_RELEASED", "PAID")

        # Verify RELEASED ledger entry
        ledger_stmt = select(LedgerEntry).where(LedgerEntry.kind == "RELEASED")
        entries = list((await session.execute(ledger_stmt)).scalars().all())
        assert len(entries) == 1
        assert entries[0].amount_kobo == 2500000

        # Verify Payout created
        payout_stmt = select(Payout).where(Payout.day_id == day_id)
        payout = (await session.execute(payout_stmt)).scalar_one_or_none()
        assert payout is not None
        assert payout.status == "SUCCESS"
