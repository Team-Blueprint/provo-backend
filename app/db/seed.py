import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import Base, async_session_maker, engine
from app.core.enums import DayStatus, JobStatus, LedgerKind, PayoutStatus, TradeType
from app.core.security import hash_pin
from app.db.models import (
    Consent,
    Job,
    JobDay,
    LedgerEntry,
    PaymentIn,
    Payout,
    Receipt,
    User,
    WorkerProfile,
)


async def seed_database():
    """
    Seeds realistic demonstration data for competition judging and demo walkthroughs:
    - Exactly 2 Workers:
      1. Emem Bassey (+2348011112222, PIN 123456, BRICKLAYING, Uyo)
         - 4 completed jobs (18 paid days total) across 4 months
         - 2 unique repeat confirmed payers (Chief Adeleke & Amina Bello)
         - 0 cash, 100% escrow bank transfers, 0 auto-releases
         - Tier 1 Credit Score (~87/100)
         - Share Token: EMEM-CREDIT-EVAL-2026
      2. Tunde Bakare (+2348022223333, PIN 123456, PLUMBING, Lagos)
         - 2 completed jobs (6 paid days total)
         - 1 auto-release (0.5x penalty)
         - 3 cash days (capping rule demonstration)
         - Tier 3 Credit Score (~54/100)
         - Share Token: TUNDE-CREDIT-EVAL-2026
    - Exactly 2 Employers:
      1. Chief Babatunde Adeleke (+2348077778888, PIN 123456)
         - 2 completed jobs with Emem + 1 active funded job on site with supervisor
      2. Amina Bello (+2348099990000, PIN 123456)
         - 2 completed jobs with Emem, 1 with Tunde + 1 draft job ready for funding demo
    - 1 Site Supervisor:
      - Segun Odegbami (+2348055556666, PIN 123456, Token SEG-SUPERVISOR-TOKEN-001)
    """
    print("🌱 Seeding Proofwork database with demo data...")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session_maker() as session:
        # Check if already seeded with new demo accounts
        existing = (
            await session.execute(
                select(User).where(User.phone.in_(["+2348011112222", "+2348123456780"]))
            )
        ).scalars().all()
        if existing:
            print("Cleaning up previous demo seed data to ensure fresh environment...")
            old_phones = [
                "+2348011112222",
                "+2348022223333",
                "+2348077778888",
                "+2348099990000",
                "+2348055556666",
                "+2348123456780",
                "+2348033331111",
                "+2348033332222",
                "+2348033333333",
                "+2348033334444",
                "+2348033335555",
            ]
            old_users = (
                await session.execute(select(User).where(User.phone.in_(old_phones)))
            ).scalars().all()
            old_user_ids = [u.id for u in old_users]
            if old_user_ids:
                old_jobs = (
                    await session.execute(
                        select(Job).where(
                            Job.client_id.in_(old_user_ids) | Job.worker_id.in_(old_user_ids)
                        )
                    )
                ).scalars().all()
                old_job_ids = [j.id for j in old_jobs]

                if old_job_ids:
                    await session.execute(delete(Receipt).where(Receipt.job_id.in_(old_job_ids)))
                    await session.execute(delete(Payout).where(Payout.day_id.in_(
                        select(JobDay.id).where(JobDay.job_id.in_(old_job_ids))
                    )))
                    await session.execute(delete(LedgerEntry).where(LedgerEntry.job_id.in_(old_job_ids)))
                    await session.execute(delete(PaymentIn).where(PaymentIn.job_id.in_(old_job_ids)))
                    await session.execute(delete(JobDay).where(JobDay.job_id.in_(old_job_ids)))
                    await session.execute(delete(Job).where(Job.id.in_(old_job_ids)))

                await session.execute(delete(Consent).where(Consent.worker_id.in_(old_user_ids)))
                await session.execute(delete(WorkerProfile).where(WorkerProfile.user_id.in_(old_user_ids)))
                await session.execute(delete(User).where(User.id.in_(old_user_ids)))
                await session.commit()
                print("Old seed data cleared.")

        now = datetime.now(UTC)

        # ---------------------------------------------------------
        # 1. Employers (Clients)
        # ---------------------------------------------------------
        chief_adeleke = User(
            name="Chief Babatunde Adeleke",
            phone="+2348077778888",
            is_client=True,
            is_worker=False,
            is_supervisor=False,
            is_phone_verified=True,
            pin_hash=hash_pin("123456"),
        )
        amina_bello = User(
            name="Amina Bello",
            phone="+2348099990000",
            is_client=True,
            is_worker=False,
            is_supervisor=False,
            is_phone_verified=True,
            pin_hash=hash_pin("123456"),
        )
        session.add_all([chief_adeleke, amina_bello])
        await session.flush()

        # ---------------------------------------------------------
        # 2. Site Supervisor
        # ---------------------------------------------------------
        segun_supervisor = User(
            name="Segun Odegbami",
            phone="+2348055556666",
            is_client=False,
            is_worker=False,
            is_supervisor=True,
            is_phone_verified=True,
            pin_hash=hash_pin("123456"),
        )
        session.add(segun_supervisor)
        await session.flush()

        # ---------------------------------------------------------
        # 3. Worker 1: Emem Bassey (High Tier 1 Artisan)
        # ---------------------------------------------------------
        emem = User(
            name="Emem Bassey",
            phone="+2348011112222",
            is_worker=True,
            is_client=False,
            is_supervisor=False,
            is_phone_verified=True,
            pin_hash=hash_pin("123456"),
            payout_account_number="9901112222",
            payout_bank_code="PAGA",
        )
        session.add(emem)
        await session.flush()

        emem_profile = WorkerProfile(
            user_id=emem.id,
            trade=TradeType.BRICKLAYING.value,
            area="Uyo, Akwa Ibom",
            paga_account_number="9901112222",
            paga_payment_url=f"https://paga.me/pay/proofwork-emem-{emem.id.hex[:6]}",
            is_available=True,
        )
        session.add(emem_profile)

        # ---------------------------------------------------------
        # 4. Worker 2: Tunde Bakare (Tier 3 Artisan with Cash & Auto-releases)
        # ---------------------------------------------------------
        tunde = User(
            name="Tunde Bakare",
            phone="+2348022223333",
            is_worker=True,
            is_client=False,
            is_supervisor=False,
            is_phone_verified=True,
            pin_hash=hash_pin("123456"),
            payout_account_number="9902223333",
            payout_bank_code="PAGA",
        )
        session.add(tunde)
        await session.flush()

        tunde_profile = WorkerProfile(
            user_id=tunde.id,
            trade=TradeType.PLUMBING.value,
            area="Ikeja, Lagos",
            paga_account_number="9902223333",
            paga_payment_url=f"https://paga.me/pay/proofwork-tunde-{tunde.id.hex[:6]}",
            is_available=True,
        )
        session.add(tunde_profile)
        await session.flush()

        # Helper to create completed jobs
        async def create_completed_job(
            client: User,
            worker: User,
            trade: TradeType,
            description: str,
            day_count: int,
            rate_kobo: int,
            days_ago: int,
            auto_release_days: list[int] | None = None,
            is_cash: bool = False,
        ) -> Job:
            if auto_release_days is None:
                auto_release_days = []
            total_kobo = rate_kobo * day_count
            job_date = now - timedelta(days=days_ago)
            ref_prefix = "PW-CASH" if is_cash else "PW-ESCROW"
            job = Job(
                client_id=client.id,
                worker_id=worker.id,
                trade=trade.value,
                description=description,
                total_kobo=total_kobo,
                days=day_count,
                status=JobStatus.COMPLETED.value,
                funding_reference=f"{ref_prefix}-{uuid.uuid4().hex[:6].upper()}",
                paga_account_number=f"99{uuid.uuid4().int % 100000000:08d}",
                created_at=job_date,
            )
            session.add(job)
            await session.flush()

            # Record Inbound Funding & Ledger
            if not is_cash:
                session.add(
                    PaymentIn(
                        paga_event_id=f"PAGA-PAY-{job.id}",
                        job_id=job.id,
                        amount_kobo=total_kobo,
                        payer_name=client.name,
                        payer_phone=client.phone,
                        raw_payload={"status": "SUCCESS"},
                        created_at=job_date,
                    )
                )
            session.add(
                LedgerEntry(
                    job_id=job.id,
                    kind=LedgerKind.FUNDED.value,
                    amount_kobo=total_kobo,
                    created_at=job_date,
                )
            )

            # Daily milestones
            for i in range(1, day_count + 1):
                day_time = job_date + timedelta(days=i)
                is_auto = i in auto_release_days
                job_day = JobDay(
                    job_id=job.id,
                    worker_id=worker.id,
                    day_number=i,
                    amount_kobo=rate_kobo,
                    status=DayStatus.PAID.value,
                    done_at=day_time,
                    release_at=day_time + timedelta(hours=24) if is_auto else None,
                    decided_by=None if is_auto else client.id,
                    decided_at=day_time + timedelta(hours=24) if is_auto else day_time + timedelta(hours=2),
                    created_at=day_time,
                    updated_at=day_time + timedelta(hours=24) if is_auto else day_time + timedelta(hours=2),
                )
                session.add(job_day)
                await session.flush()

                session.add(
                    LedgerEntry(
                        job_id=job.id,
                        day_id=job_day.id,
                        kind=LedgerKind.RELEASED.value,
                        amount_kobo=rate_kobo,
                        created_at=day_time,
                    )
                )
                session.add(
                    Payout(
                        day_id=job_day.id,
                        amount_kobo=rate_kobo,
                        idempotency_ref=f"PAYOUT-SEED-{job_day.id}",
                        status=PayoutStatus.SUCCESS.value,
                        created_at=day_time,
                    )
                )

            # Mint final immutable receipt
            rec_num = f"PW-{job_date.year}-{uuid.uuid4().hex[:6].upper()}"
            session.add(
                Receipt(
                    receipt_number=rec_num,
                    job_id=job.id,
                    issued_at=job_date + timedelta(days=day_count + 1),
                    receipt_data={
                        "receipt_number": rec_num,
                        "job_id": str(job.id),
                        "trade": job.trade,
                        "total_kobo": total_kobo,
                        "total_naira": f"{total_kobo/100:,.2f}",
                        "worker_name": worker.name,
                        "client_name": client.name,
                        "is_cash": is_cash,
                    },
                )
            )
            return job

        # ---------------------------------------------------------
        # Seed Emem Bassey's 4 Completed Jobs (18 Days Total)
        # ---------------------------------------------------------
        # Job 1: Chief Adeleke (5 days, 100 days ago) - Masonry foundation
        await create_completed_job(
            client=chief_adeleke,
            worker=emem,
            trade=TradeType.BRICKLAYING,
            description="Commercial perimeter wall & foundation blocks",
            day_count=5,
            rate_kobo=1500000,  # ₦15k/day = ₦75k
            days_ago=100,
        )
        # Job 2: Amina Bello (5 days, 70 days ago) - Residential plastering
        await create_completed_job(
            client=amina_bello,
            worker=emem,
            trade=TradeType.BRICKLAYING,
            description="Residential compound wall plastering and screeding",
            day_count=5,
            rate_kobo=1400000,  # ₦14k/day = ₦70k
            days_ago=70,
        )
        # Job 3: Chief Adeleke (4 days, 40 days ago) - Repeat employer bonus!
        await create_completed_job(
            client=chief_adeleke,
            worker=emem,
            trade=TradeType.BRICKLAYING,
            description="Plaza security post and generator house masonry",
            day_count=4,
            rate_kobo=1500000,  # ₦15k/day = ₦60k
            days_ago=40,
        )
        # Job 4: Amina Bello (4 days, 15 days ago) - Repeat employer bonus!
        await create_completed_job(
            client=amina_bello,
            worker=emem,
            trade=TradeType.BRICKLAYING,
            description="Drainage channel and interlocking curb casting",
            day_count=4,
            rate_kobo=1600000,  # ₦16k/day = ₦64k
            days_ago=15,
        )

        # ---------------------------------------------------------
        # Seed Tunde Bakare's 2 Completed Jobs (6 Days Total)
        # ---------------------------------------------------------
        # Job 5: Amina Bello (3 days, 45 days ago) - Includes 1 auto-release
        await create_completed_job(
            client=amina_bello,
            worker=tunde,
            trade=TradeType.PLUMBING,
            description="Kitchen and bathroom piping and drainage line",
            day_count=3,
            rate_kobo=1200000,  # ₦12k/day = ₦36k
            days_ago=45,
            auto_release_days=[3],  # Day 3 auto-released (0.5x weight)
        )
        # Job 6: Chief Adeleke (3 days, 20 days ago) - Cash job capping demo
        await create_completed_job(
            client=chief_adeleke,
            worker=tunde,
            trade=TradeType.PLUMBING,
            description="Emergency borehole submersible pump and pressure valve repair",
            day_count=3,
            rate_kobo=1000000,  # ₦10k/day = ₦30k
            days_ago=20,
            is_cash=True,  # Cash job!
        )

        # ---------------------------------------------------------
        # Seed Active Funded Job with Supervisor (Chief Adeleke + Emem + Segun)
        # ---------------------------------------------------------
        active_job = Job(
            client_id=chief_adeleke.id,
            worker_id=emem.id,
            supervisor_id=segun_supervisor.id,
            supervisor_name=segun_supervisor.name,
            supervisor_phone=segun_supervisor.phone,
            supervisor_token="SEG-SUPERVISOR-TOKEN-001",
            trade=TradeType.BRICKLAYING.value,
            description="Commercial Plaza Block Laying & Lintel Casting - Phase 1",
            total_kobo=4500000,  # ₦45k
            days=3,
            status=JobStatus.FUNDED.value,
            funding_reference="PW-SEED-LIVE-FUNDED-01",
            paga_account_number="9908881122",
            created_at=now - timedelta(days=1),
        )
        session.add(active_job)
        await session.flush()

        session.add(
            PaymentIn(
                paga_event_id=f"PAGA-PAY-{active_job.id}",
                job_id=active_job.id,
                amount_kobo=4500000,
                payer_name=chief_adeleke.name,
                payer_phone=chief_adeleke.phone,
                raw_payload={"status": "SUCCESS"},
                created_at=now - timedelta(days=1),
            )
        )
        session.add(
            LedgerEntry(
                job_id=active_job.id,
                kind=LedgerKind.FUNDED.value,
                amount_kobo=4500000,
                created_at=now - timedelta(days=1),
            )
        )

        # Day 1: Done reported awaiting supervisor verification!
        session.add(
            JobDay(
                job_id=active_job.id,
                worker_id=emem.id,
                day_number=1,
                amount_kobo=1500000,
                status=DayStatus.DONE_REPORTED.value,
                done_at=now - timedelta(hours=4),
                release_at=now + timedelta(hours=20),
                proof_photo="https://images.unsplash.com/photo-1541888946425-d0fbb186156a?w=800",
                created_at=now - timedelta(days=1),
            )
        )
        # Day 2 & 3: Pending
        session.add(
            JobDay(
                job_id=active_job.id,
                worker_id=emem.id,
                day_number=2,
                amount_kobo=1500000,
                status=DayStatus.PENDING.value,
                created_at=now - timedelta(days=1),
            )
        )
        session.add(
            JobDay(
                job_id=active_job.id,
                worker_id=emem.id,
                day_number=3,
                amount_kobo=1500000,
                status=DayStatus.PENDING.value,
                created_at=now - timedelta(days=1),
            )
        )

        # ---------------------------------------------------------
        # Seed Draft Job (Amina Bello + Tunde Bakare) for Checkout Demo
        # ---------------------------------------------------------
        draft_job = Job(
            client_id=amina_bello.id,
            worker_id=tunde.id,
            trade=TradeType.PLUMBING.value,
            description="Residential Water Tank Stand & Solar Inverter Piping",
            total_kobo=3000000,  # ₦30k
            days=2,
            status=JobStatus.DRAFT.value,
            funding_reference="PW-SEED-DRAFT-01",
            paga_account_number="9908882233",
            paga_payment_url="https://paga.me/pay/proofwork-draft-demo",
            created_at=now - timedelta(hours=2),
        )
        session.add(draft_job)
        await session.flush()

        session.add(
            JobDay(
                job_id=draft_job.id,
                worker_id=tunde.id,
                day_number=1,
                amount_kobo=1500000,
                status=DayStatus.PENDING.value,
                created_at=now - timedelta(hours=2),
            )
        )
        session.add(
            JobDay(
                job_id=draft_job.id,
                worker_id=tunde.id,
                day_number=2,
                amount_kobo=1500000,
                status=DayStatus.PENDING.value,
                created_at=now - timedelta(hours=2),
            )
        )

        # ---------------------------------------------------------
        # Pre-seeded NDPA 7-day Lender Consent Tokens
        # ---------------------------------------------------------
        session.add(
            Consent(
                worker_id=emem.id,
                token="EMEM-CREDIT-EVAL-2026",
                purpose="Standard Microfinance Credit Evaluation",
                expires_at=now + timedelta(days=7),
                is_revoked=False,
                access_count=1,
                granted_at=now,
            )
        )
        session.add(
            Consent(
                worker_id=tunde.id,
                token="TUNDE-CREDIT-EVAL-2026",
                purpose="Equipment & Tool Loan Underwriting",
                expires_at=now + timedelta(days=7),
                is_revoked=False,
                access_count=0,
                granted_at=now,
            )
        )

        await session.commit()

    print("✅ Seed completed successfully!")
    print("\n--- DEMO ACCOUNTS (All PINs: '123456') ---")
    print(f"👷 Worker 1: Emem Bassey ({emem.phone}) - Bricklayer, Uyo (Tier 1)")
    print(f"👷 Worker 2: Tunde Bakare ({tunde.phone}) - Plumber, Lagos (Tier 3)")
    print(f"👔 Employer 1: Chief Babatunde Adeleke ({chief_adeleke.phone}) - Commercial Client")
    print(f"👔 Employer 2: Amina Bello ({amina_bello.phone}) - Residential Client")
    print(f"🕵️ Site Supervisor: Segun Odegbami ({segun_supervisor.phone})")
    print("\n--- DEMO URLS ---")
    print("👉 Emem's Lender Report: http://localhost:8000/reports/EMEM-CREDIT-EVAL-2026/view")
    print("👉 Tunde's Lender Report: http://localhost:8000/reports/TUNDE-CREDIT-EVAL-2026/view")
    print("👉 Site Supervisor Portal: http://localhost:8000/site/SEG-SUPERVISOR-TOKEN-001/view")
    print(f"👉 Draft Job Checkout: http://localhost:8000/checkout/{draft_job.id}")


if __name__ == "__main__":
    asyncio.run(seed_database())
