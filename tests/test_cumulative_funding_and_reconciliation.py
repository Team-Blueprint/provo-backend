import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.cli.reconcile import run_reconciliation
from app.core.database import AsyncSessionLocal, Base, engine
from app.core.enums import JobStatus, LedgerKind
from app.core.security import create_access_token
from app.db.models import Job, LedgerEntry, User
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
async def test_cumulative_split_funding_and_overfunding():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create users & job with total ₦42,000 (4,200,000 kobo)
        async with AsyncSessionLocal() as session:
            client_user = User(name="Tunde", phone="+2348033334444", is_client=True, is_phone_verified=True)
            worker_user = User(name="Emem", phone="+2348055556666", is_worker=True, is_phone_verified=True)
            session.add_all([client_user, worker_user])
            await session.commit()
            client_id = client_user.id
            worker_id = worker_user.id

        token = create_access_token({"sub": str(client_id)})
        create_res = await client.post(
            "/jobs",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "trade": "CARPENTRY",
                "description": "Roof roofing",
                "total_kobo": 4200000,
                "days": 2,
                "worker_id": str(worker_id),
            },
        )
        assert create_res.status_code == 201
        job_data = create_res.json()
        job_id = job_data["id"]
        reference = job_data["funding_reference"]

        # Request fund request to generate virtual account and move to AWAITING_FUNDING
        fund_req = await client.post(
            f"/jobs/{job_id}/fund-request",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert fund_req.status_code == 200
        assert fund_req.json()["status"] == JobStatus.AWAITING_FUNDING.value

        # 1. First split payment: ₦25,000
        split_1 = {
            "transactionId": "PAGA-EVT-SPLIT-1",
            "referenceNumber": reference,
            "amount": 25000.00,
            "currency": "NGN",
            "payerPhoneNumber": "+2348033334444",
        }
        res1 = await client.post("/webhooks/paga", json=split_1)
        assert res1.status_code == 200
        assert res1.json()["job_status"] == JobStatus.AWAITING_FUNDING.value

        # Check job in DB is still AWAITING_FUNDING
        async with AsyncSessionLocal() as session:
            j1 = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
            assert j1.status == JobStatus.AWAITING_FUNDING.value

        # 2. Second payment completing the total: ₦17,000 (total = ₦42,000)
        split_2 = {
            "transactionId": "PAGA-EVT-SPLIT-2",
            "referenceNumber": reference,
            "amount": 17000.00,
            "currency": "NGN",
            "payerPhoneNumber": "+2348033334444",
        }
        res2 = await client.post("/webhooks/paga", json=split_2)
        assert res2.status_code == 200
        assert res2.json()["job_status"] == JobStatus.FUNDED.value

        # Check job in DB is now FUNDED!
        async with AsyncSessionLocal() as session:
            j2 = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
            assert j2.status == JobStatus.FUNDED.value

        # 3. Third payment causing OVERFUNDING: ₦5,000 extra
        extra = {
            "transactionId": "PAGA-EVT-EXTRA-3",
            "referenceNumber": reference,
            "amount": 5000.00,
            "currency": "NGN",
            "payerPhoneNumber": "+2348033334444",
        }
        res3 = await client.post("/webhooks/paga", json=extra)
        assert res3.status_code == 200
        assert res3.json()["job_status"] == JobStatus.FUNDED.value

        # Verify ledger entries in DB
        async with AsyncSessionLocal() as session:
            entries = (
                await session.execute(
                    select(LedgerEntry).where(LedgerEntry.job_id == job_id)
                )
            ).scalars().all()
            kinds = [e.kind for e in entries]
            assert LedgerKind.FUNDED.value in kinds
            assert LedgerKind.EXCESS_HELD.value in kinds

        # 4. Run CLI Reconciliation audit
        audit_success = await run_reconciliation()
        assert audit_success is True
