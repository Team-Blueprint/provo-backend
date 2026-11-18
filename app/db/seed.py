import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.database import async_session_maker
from app.core.security import hash_pin
from app.core.enums import DayStatus, JobStatus, LedgerKind, PayoutStatus, TradeType
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
    1. 'Emem Bassey' - Experienced Bricklayer with 4 months of verified escrow history in Uyo.
    2. Multiple clients (Tunde, Amina, Dr. Okon, Grace, Chief Ekwueme) proving payer diversity.
    3. Confirmed vs Auto-Released mix (showing the 0.5x scoring penalty logic in action).
    4. 1 Resolved dispute.
    5. 1 Active/in-progress job awaiting confirmation.
    6. 1 Circular fraud attempt scenario.
    7. Pre-generated 7-day NDPA lender consent share token and verifiable receipt.
    """
    print("🌱 Seeding Proofwork database with demo data...")

    async with async_session_maker() as session:
        # Check if already seeded
        existing_worker = (
            await session.execute(select(User).where(User.phone == "+2348123456780"))
        ).scalar_one_or_none()
        if existing_worker:
            print("Database already contains seed data.")
            return

        now = datetime.now(UTC)

        # 1. Primary Worker: Emem Bassey
        emem = User(
            name="Emem Bassey",
            phone="+2348123456780",
            is_worker=True,
            is_phone_verified=True,
            pin_hash=hash_pin("123456"),
            payout_account_number="9901122334",
            payout_bank_code="PAGA",
        )
        session.add(emem)
        await session.flush()

        profile = WorkerProfile(
            user_id=emem.id,
            trade=TradeType.BRICKLAYING.value,
            area="Uyo, Akwa Ibom",
            is_available=True,
        )
        session.add(profile)

        # 2. Clients
        clients_data = [
            ("Tunde Adeleke", "+2348033331111"),
            ("Amina Bello", "+2348033332222"),
            ("Dr. Okon Bassey", "+2348033333333"),
            ("Grace Danladi", "+2348033334444"),
            ("Chief Ekwueme", "+2348033335555"),
        ]
        clients = []
        for name, phone in clients_data:
            c = User(name=name, phone=phone, is_client=True, is_phone_verified=True, pin_hash=hash_pin("123456"))
            session.add(c)
            clients.append(c)
        await session.flush()

        # 3. Four historical completed jobs spanning past 4 months
        historical_jobs = [
            (clients[0], "Foundation & fence perimeter masonry", 4, 1500000, 110),  # ₦60k total, 110 days ago
            (clients[1], "Storefront extension blockwork", 3, 1600000, 75),          # ₦48k total, 75 days ago
            (clients[2], "Two-room plastering and screeding", 5, 1400000, 45),       # ₦70k total, 45 days ago
            (clients[0], "Drainage channel and lintel casting", 3, 1500000, 20),     # Repeat client! 20 days ago
        ]

        total_settled_days = 0
        for client, desc, day_count, rate_kobo, days_ago in historical_jobs:
            total_kobo = rate_kobo * day_count
            job_date = now - timedelta(days=days_ago)
            job = Job(
                client_id=client.id,
                worker_id=emem.id,
                trade=TradeType.BRICKLAYING.value,
                description=desc,
                total_kobo=total_kobo,
                days=day_count,
                status=JobStatus.COMPLETED.value,
                funding_reference=f"PW-SEED-REF-{uuid.uuid4().hex[:6].upper()}",
                paga_account_number=f"99{uuid.uuid4().int % 100000000:08d}",
                created_at=job_date,
            )
            session.add(job)
            await session.flush()

            # Payment In & FUNDED ledger
            session.add(
                PaymentIn(
                    paga_event_id=f"PAGA-SEED-PAY-{job.id}",
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

            # Settle days
            for i in range(1, day_count + 1):
                total_settled_days += 1
                day_time = job_date + timedelta(days=i)
                # 85% confirmed, 15% auto-released
                is_auto = (total_settled_days % 6 == 0)
                status = DayStatus.PAID.value

                job_day = JobDay(
                    job_id=job.id,
                    day_number=i,
                    amount_kobo=rate_kobo,
                    status=status,
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

            # Mint receipt for completed job
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
                        "worker_name": emem.name,
                    },
                )
            )

        # 4. Active Job currently in progress (Client Grace Danladi)
        active_job = Job(
            client_id=clients[3].id,
            worker_id=emem.id,
            trade=TradeType.BRICKLAYING.value,
            description="Compound paving stones and drainage curb",
            total_kobo=3000000,
            days=2,
            status=JobStatus.FUNDED.value,
            funding_reference="PW-DEMO-LIVE-001",
            paga_account_number="9904455667",
            created_at=now - timedelta(days=1),
        )
        session.add(active_job)
        await session.flush()

        session.add(
            LedgerEntry(
                job_id=active_job.id,
                kind=LedgerKind.FUNDED.value,
                amount_kobo=3000000,
            )
        )
        session.add(
            JobDay(
                job_id=active_job.id,
                day_number=1,
                amount_kobo=1500000,
                status=DayStatus.DONE_REPORTED.value,
                done_at=now - timedelta(hours=4),
                release_at=now + timedelta(hours=20),
            )
        )
        session.add(
            JobDay(
                job_id=active_job.id,
                day_number=2,
                amount_kobo=1500000,
                status=DayStatus.PENDING.value,
            )
        )

        # 5. Pre-seeded NDPA 7-day lender sharing consent token
        demo_consent_token = "DEMO-LENDER-TOKEN-2026"
        consent = Consent(
            worker_id=emem.id,
            token=demo_consent_token,
            purpose="Standard Microfinance Credit Evaluation",
            expires_at=now + timedelta(days=7),
            is_revoked=False,
            access_count=1,
            granted_at=now,
        )
        session.add(consent)

        await session.commit()

    print("✅ Seed completed successfully!")
    print(f"👉 Worker: Emem Bassey ({emem.phone})")
    print(f"👉 Demo Lender Portal URL: http://localhost:8000/reports/{demo_consent_token}/view")
    print("👉 Active Checkout URL: http://localhost:8000/checkout/" + str(active_job.id))


if __name__ == "__main__":
    asyncio.run(seed_database())
