from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.database import AsyncSessionLocal, Base, engine
from app.core.enums import DayStatus, DisputeStatus, JobStatus
from app.core.security import create_access_token
from app.db.models import (
    Dispute,
    Job,
    JobDay,
    LedgerEntry,
    Receipt,
    User,
    WorkerProfile,
)
from main import app


@pytest.fixture(autouse=True)
async def clean_database():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.mark.asyncio
async def test_worker_directory_and_lender_report_flow():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Setup Worker and Client
        async with AsyncSessionLocal() as session:
            worker = User(
                name="Emem Bassey",
                phone="+2348011112222",
                is_worker=True,
                is_phone_verified=True,
            )
            client_user = User(
                name="Tunde Client",
                phone="+2348033334444",
                is_client=True,
                is_phone_verified=True,
            )
            session.add_all([worker, client_user])
            await session.flush()

            profile = WorkerProfile(
                user_id=worker.id,
                trade="BRICKLAYING",
                area="Uyo",
                is_available=True,
            )
            session.add(profile)

            # Create a completed job with 2 paid days
            job = Job(
                client_id=client_user.id,
                worker_id=worker.id,
                trade="BRICKLAYING",
                description="Foundation wall",
                total_kobo=3000000,
                days=2,
                status=JobStatus.COMPLETED.value,
                funding_reference="PW-TEST-REF-001",
            )
            session.add(job)
            await session.flush()

            d1 = JobDay(
                job_id=job.id,
                day_number=1,
                amount_kobo=1500000,
                status=DayStatus.PAID.value,
                done_at=datetime.now(UTC),
            )
            d2 = JobDay(
                job_id=job.id,
                day_number=2,
                amount_kobo=1500000,
                status=DayStatus.PAID.value,
                done_at=datetime.now(UTC),
            )
            session.add_all([d1, d2])

            receipt = Receipt(
                receipt_number="PW-2026-TEST01",
                job_id=job.id,
                receipt_data={"total_naira": "30,000.00"},
            )
            session.add(receipt)
            await session.commit()

            worker_id = worker.id
            job_id = job.id

        # 2. Worker Directory Query
        dir_res = await client.get("/workers/available?trade=BRICKLAYING")
        assert dir_res.status_code == 200
        workers = dir_res.json()
        assert len(workers) == 1
        assert workers[0]["name"] == "Emem Bassey"
        assert workers[0]["verified_days_count"] == 2
        assert workers[0]["badge_grade"] == "BRONZE_VERIFIED"

        # 3. Worker creates 7-day share link
        worker_token = create_access_token({"sub": str(worker_id)})
        share_res = await client.post(
            f"/workers/{worker_id}/shares",
            headers={"Authorization": f"Bearer {worker_token}"},
            json={"purpose": "Microfinance Loan Application", "valid_days": 7},
        )
        assert share_res.status_code == 200
        share_data = share_res.json()
        consent_token = share_data["token"]
        assert "reports/" in share_data["report_url"]

        # 4. Lender accesses verified report (JSON)
        report_res = await client.get(f"/reports/{consent_token}")
        assert report_res.status_code == 200
        report = report_res.json()
        assert report["status"] == "VALID"
        assert report["artisan"]["name"] == "Emem Bassey"
        assert report["credit_score"]["score"] > 0
        assert report["earnings_summary"]["total_settled_days"] == 2
        assert len(report["payer_diversity"]["payers"]) == 1
        # Check tokenized client pseudonym
        payer_id = report["payer_diversity"]["payers"][0]["token"]
        assert payer_id.startswith("Client-")

        # 5. Access Web Views (HTML)
        web_checkout = await client.get(f"/checkout/{job_id}")
        assert web_checkout.status_code == 200
        assert "Proofwork Escrow Funding" in web_checkout.text

        web_report = await client.get(f"/reports/{consent_token}/view")
        assert web_report.status_code == 200
        assert "Verified Credit Report" in web_report.text
        assert "Emem Bassey" in web_report.text

        web_receipt = await client.get("/receipts/PW-2026-TEST01")
        assert web_receipt.status_code == 200
        assert "Certificate of Completion" in web_receipt.text


@pytest.mark.asyncio
async def test_admin_dispute_resolution_flow():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Setup Admin, Client, Worker, and a Disputed Day
        async with AsyncSessionLocal() as session:
            admin = User(name="System Admin", phone="+2348000000001", is_admin=True, is_phone_verified=True)
            client_user = User(name="Client A", phone="+2348022223333", is_client=True, is_phone_verified=True)
            worker = User(name="Artisan B", phone="+2348044445555", is_worker=True, is_phone_verified=True)
            session.add_all([admin, client_user, worker])
            await session.flush()

            job = Job(
                client_id=client_user.id,
                worker_id=worker.id,
                trade="PLUMBING",
                description="Pipe repair",
                total_kobo=2000000,
                days=1,
                status=JobStatus.FUNDED.value,
                funding_reference="PW-DISP-001",
            )
            session.add(job)
            await session.flush()

            entry = LedgerEntry(
                job_id=job.id,
                kind="FUNDED",
                amount_kobo=2000000,
            )
            session.add(entry)

            day = JobDay(
                job_id=job.id,
                day_number=1,
                amount_kobo=2000000,
                status=DayStatus.DISPUTED.value,
            )
            session.add(day)
            await session.flush()

            dispute = Dispute(
                day_id=day.id,
                opened_by=client_user.id,
                reason="Work was only half finished",
                status=DisputeStatus.OPEN.value,
            )
            session.add(dispute)
            await session.commit()

            admin_id = admin.id
            dispute_id = dispute.id

        admin_token = create_access_token({"sub": str(admin_id)})

        # Admin resolves via SPLIT (₦10k to worker, ₦10k refund to client)
        resolve_res = await client.post(
            f"/admin/disputes/{dispute_id}/resolve",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "action": "SPLIT",
                "resolution_note": "Artisan agreed to 50% settlement for completed section",
                "worker_amount_kobo": 1000000,
                "client_amount_kobo": 1000000,
            },
        )
        assert resolve_res.status_code == 200
        res_data = resolve_res.json()
        assert res_data["status"] == "RESOLVED"
        assert res_data["action"] == "SPLIT"

        # Check outbox inspection
        outbox_res = await client.get(
            "/admin/outbox",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert outbox_res.status_code == 200
        outbox = outbox_res.json()
        assert len(outbox) >= 1
        assert outbox[0]["amount_kobo"] == 1000000
