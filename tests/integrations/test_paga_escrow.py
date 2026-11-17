import json

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import AsyncSessionLocal, Base, engine
from app.db.models import Job, LedgerEntry, User
from main import app


@pytest.fixture(autouse=True)
async def prepare_database():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.mark.asyncio
async def test_job_creation_and_paga_escrow_funding_flow():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Create client and worker users directly in DB
        async with AsyncSessionLocal() as session:
            client_user = User(
                name="Tunde Client",
                phone="+2348033334444",
                is_client=True,
                is_phone_verified=True,
            )
            worker_user = User(
                name="Emem Bricklayer",
                phone="+2348055556666",
                is_worker=True,
                is_phone_verified=True,
            )
            session.add_all([client_user, worker_user])
            await session.commit()
            client_id = client_user.id
            worker_id = worker_user.id

        # 2. Authenticate as client
        from app.core.security import create_access_token
        token = create_access_token({"sub": str(client_id), "phone": "+2348033334444"})

        # 3. Create Escrow Job (₦60,000 across 3 days)
        job_payload = {
            "trade": "BRICKLAYING",
            "description": "Construct 3-room foundation fence",
            "total_kobo": 6000000,  # 60,000 NGN
            "days": 3,
            "worker_id": str(worker_id),
            "auto_fund_request": False,
        }
        res = await client.post(
            "/jobs",
            json=job_payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 201
        job_data = res.json()
        assert job_data["status"] == "DRAFT"
        assert job_data["total_kobo"] == 6000000
        assert len(job_data["days_schedule"]) == 3
        assert job_data["days_schedule"][0]["amount_kobo"] == 2000000
        assert job_data["days_schedule"][1]["amount_kobo"] == 2000000
        assert job_data["days_schedule"][2]["amount_kobo"] == 2000000
        job_id = job_data["id"]
        funding_ref = job_data["funding_reference"]

        # 4. Request Escrow Funding Link & NUBAN
        fund_req_res = await client.post(
            f"/jobs/{job_id}/fund-request",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert fund_req_res.status_code == 200
        funded_info = fund_req_res.json()
        assert funded_info["status"] == "AWAITING_FUNDING"
        assert funded_info["paga_account_number"] is not None
        assert "http" in funded_info["paga_payment_url"]

        # 5. Paga Webhook Callback (Client pays ₦60,000 from their bank)
        webhook_payload = {
            "transactionId": "TX-PAGA-991122",
            "referenceNumber": funding_ref,
            "amount": 60000.00,
            "currency": "NGN",
            "payerName": "Tunde Client",
            "payerPhoneNumber": "+2348033334444",
            "payerAccountNumber": "0123456789",
        }
        raw_body = json.dumps(webhook_payload).encode("utf-8")

        # Compute mock signature
        import hashlib
        import hmac

        from app.core.config import settings
        key = settings.PAGA_HASH_KEY or "mock-hmac-key-test"
        sig = hmac.new(key.encode("utf-8"), raw_body, hashlib.sha512).hexdigest()

        wb_res = await client.post(
            "/webhooks/paga",
            content=raw_body,
            headers={"Content-Type": "application/json", "X-Paga-Signature": sig},
        )
        assert wb_res.status_code == 200
        wb_data = wb_res.json()
        assert wb_data["job_status"] == "FUNDED"

        # 6. Verify Job and Ledger in DB
        async with AsyncSessionLocal() as session:
            stmt = select(Job).where(Job.id == job_id)
            db_job = (await session.execute(stmt)).scalar_one()
            assert db_job.status == "FUNDED"

            ledger_stmt = select(LedgerEntry).where(LedgerEntry.job_id == job_id)
            entries = list((await session.execute(ledger_stmt)).scalars().all())
            assert len(entries) == 1
            assert entries[0].kind == "FUNDED"
            assert entries[0].amount_kobo == 6000000

        # 7. Duplicate delivery is idempotent
        wb_replay = await client.post(
            "/webhooks/paga",
            content=raw_body,
            headers={"Content-Type": "application/json", "X-Paga-Signature": sig},
        )
        assert wb_replay.status_code == 200

        # Verify no duplicate ledger entry created
        async with AsyncSessionLocal() as session:
            entries = list((await session.execute(ledger_stmt)).scalars().all())
            assert len(entries) == 1

        # 8. Test List Jobs Endpoint with filters
        list_res = await client.get("/jobs", headers={"Authorization": f"Bearer {token}"})
        assert list_res.status_code == 200
        jobs_list = list_res.json()
        assert len(jobs_list) == 1
        assert jobs_list[0]["id"] == str(job_id)

        # Filter by status match
        filter_status = await client.get("/jobs?status=FUNDED", headers={"Authorization": f"Bearer {token}"})
        assert filter_status.status_code == 200
        assert len(filter_status.json()) == 1

        # Filter by status mismatch
        filter_mismatch = await client.get("/jobs?status=COMPLETED", headers={"Authorization": f"Bearer {token}"})
        assert filter_mismatch.status_code == 200
        assert len(filter_mismatch.json()) == 0

        # Filter by trade
        filter_trade = await client.get("/jobs?trade=BRICKLAYING", headers={"Authorization": f"Bearer {token}"})
        assert filter_trade.status_code == 200
        assert len(filter_trade.json()) == 1
