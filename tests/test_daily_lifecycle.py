import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import AsyncSessionLocal, Base, engine
from app.core.security import create_access_token
from app.db.models import Dispute, Job, LedgerEntry, Payout, Receipt, User
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
async def test_full_daily_work_lifecycle_and_settlement():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Setup Client, Worker, and Supervisor in DB
        async with AsyncSessionLocal() as session:
            c_user = User(name="Client Chief", phone="+2348011110001", is_client=True, is_phone_verified=True)
            w_user = User(name="Emem Bricklayer", phone="+2348011110002", is_worker=True, is_phone_verified=True, payout_account_number="9900112233")
            s_user = User(name="Site Foreman", phone="+2348011110003", is_supervisor=True, is_phone_verified=True)
            session.add_all([c_user, w_user, s_user])
            await session.commit()
            client_id = c_user.id
            worker_id = w_user.id
            supervisor_id = s_user.id

        client_token = create_access_token({"sub": str(client_id), "phone": "+2348011110001"})
        worker_token = create_access_token({"sub": str(worker_id), "phone": "+2348011110002"})
        sup_token = create_access_token({"sub": str(supervisor_id), "phone": "+2348011110003"})

        # 2. Create Job (2 Days, 40,000 NGN)
        res = await client.post(
            "/jobs",
            json={
                "trade": "BRICKLAYING",
                "description": "Construct gate fence",
                "total_kobo": 4000000,
                "days": 2,
                "worker_id": str(worker_id),
                "supervisor_id": str(supervisor_id),
            },
            headers={"Authorization": f"Bearer {client_token}"},
        )
        assert res.status_code == 201
        job_id = res.json()["id"]

        # Fund the job directly in DB (simulating Paga Webhook)
        async with AsyncSessionLocal() as session:
            stmt = select(Job).where(Job.id == uuid.UUID(job_id))
            job = (await session.execute(stmt)).scalar_one()
            job.status = "FUNDED"
            # Add FUNDED ledger entry
            ledger = LedgerEntry(job_id=job.id, kind="FUNDED", amount_kobo=4000000)
            session.add(ledger)
            await session.commit()

        # 3. Worker reports Day 1 as done
        d1_res = await client.post(
            f"/jobs/{job_id}/days/1/done",
            headers={"Authorization": f"Bearer {worker_token}"},
        )
        assert d1_res.status_code == 200
        d1_data = d1_res.json()
        assert d1_data["status"] == "DONE_REPORTED"
        assert d1_data["release_at"] is not None

        # 4. Supervisor confirms Day 1
        conf_res = await client.post(
            f"/jobs/{job_id}/days/1/confirm",
            headers={"Authorization": f"Bearer {sup_token}"},
        )
        assert conf_res.status_code == 200
        assert conf_res.json()["status"] == "PAID"

        # Check Payout and Ledger records for Day 1
        async with AsyncSessionLocal() as session:
            payout_stmt = select(Payout)
            payouts = list((await session.execute(payout_stmt)).scalars().all())
            assert len(payouts) == 1
            assert payouts[0].status == "SUCCESS"

            ledger_stmt = select(LedgerEntry).where(LedgerEntry.kind == "RELEASED")
            released_entries = list((await session.execute(ledger_stmt)).scalars().all())
            assert len(released_entries) == 1
            assert released_entries[0].amount_kobo == 2000000

        # 5. Worker reports Day 2 as done
        d2_res = await client.post(
            f"/jobs/{job_id}/days/2/done",
            headers={"Authorization": f"Bearer {worker_token}"},
        )
        assert d2_res.status_code == 200

        # 6. Client confirms Day 2 (Final day)
        conf_d2_res = await client.post(
            f"/jobs/{job_id}/days/2/confirm",
            headers={"Authorization": f"Bearer {client_token}"},
        )
        assert conf_d2_res.status_code == 200
        assert conf_d2_res.json()["status"] == "PAID"

        # 7. Verify Job is COMPLETED and Proofwork Receipt is minted
        async with AsyncSessionLocal() as session:
            stmt = select(Job).where(Job.id == uuid.UUID(job_id))
            comp_job = (await session.execute(stmt)).scalar_one()
            assert comp_job.status == "COMPLETED"

            rec_stmt = select(Receipt).where(Receipt.job_id == uuid.UUID(job_id))
            receipt = (await session.execute(rec_stmt)).scalar_one_or_none()
            assert receipt is not None
            assert receipt.receipt_number.startswith("PW-")
            receipt_number = receipt.receipt_number

        # 8. Verify public receipt verification endpoint
        v_res = await client.get(f"/jobs/verify/{receipt_number}")
        assert v_res.status_code == 200
        assert v_res.json()["receipt_number"] == receipt_number


@pytest.mark.asyncio
async def test_dispute_freezes_day_and_stops_auto_release():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Setup users
        async with AsyncSessionLocal() as session:
            c_user = User(name="Client Chief", phone="+2348022220001", is_client=True, is_phone_verified=True)
            w_user = User(name="Artisan", phone="+2348022220002", is_worker=True, is_phone_verified=True)
            session.add_all([c_user, w_user])
            await session.commit()
            client_id = c_user.id
            worker_id = w_user.id

        client_token = create_access_token({"sub": str(client_id), "phone": "+2348022220001"})
        worker_token = create_access_token({"sub": str(worker_id), "phone": "+2348022220002"})

        # Create & Fund Job
        res = await client.post(
            "/jobs",
            json={"trade": "PLUMBING", "description": "Install pipes", "total_kobo": 1000000, "days": 1, "worker_id": str(worker_id)},
            headers={"Authorization": f"Bearer {client_token}"},
        )
        job_id = res.json()["id"]

        async with AsyncSessionLocal() as session:
            job = (await session.execute(select(Job).where(Job.id == uuid.UUID(job_id)))).scalar_one()
            job.status = "FUNDED"
            session.add(LedgerEntry(job_id=job.id, kind="FUNDED", amount_kobo=1000000))
            await session.commit()

        # Worker reports done
        await client.post(f"/jobs/{job_id}/days/1/done", headers={"Authorization": f"Bearer {worker_token}"})

        # Client disputes
        disp_res = await client.post(
            f"/jobs/{job_id}/days/1/dispute",
            json={"reason": "Pipes were not connected properly."},
            headers={"Authorization": f"Bearer {client_token}"},
        )
        assert disp_res.status_code == 200
        disp_data = disp_res.json()
        assert disp_data["status"] == "DISPUTED"
        assert disp_data["release_at"] is None  # Frozen

        # Verify dispute stored in DB
        async with AsyncSessionLocal() as session:
            stmt = select(Dispute)
            disp = (await session.execute(stmt)).scalar_one()
            assert "not connected" in disp.reason
