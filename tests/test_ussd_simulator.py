import pytest
from httpx import ASGITransport, AsyncClient

from app.core.database import Base, engine
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
async def test_ussd_simulator_endpoints_for_worker():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Sign up worker
        signup_res = await client.post(
            "/auth/signup",
            json={
                "name": "Ifeanyi Okon",
                "phone": "+2348122060708",
                "pin": "123456",
                "role": "worker",
                "trade": "BRICKLAYING",
            },
        )
        assert signup_res.status_code == 201
        otp_code = signup_res.json()["otp_code"]

        # Verify OTP (this activates and provisions worker Paga account)
        verify_res = await client.post(
            "/auth/verify-otp",
            json={"phone": "+2348122060708", "code": otp_code},
        )
        assert verify_res.status_code == 200
        worker_id = verify_res.json()["user_id"]

        # Sign up a client
        c_signup = await client.post(
            "/auth/signup",
            json={
                "name": "Chief Client",
                "phone": "+2348033334444",
                "pin": "123456",
                "role": "client",
            },
        )
        c_otp = c_signup.json()["otp_code"]
        c_verify = await client.post(
            "/auth/verify-otp",
            json={"phone": "+2348033334444", "code": c_otp},
        )
        client_token = c_verify.json()["access_token"]

        # 2. Key 1: Test USSD Balance endpoint
        bal_res = await client.get("/ussd/balance", params={"phone": "+2348122060708"})
        assert bal_res.status_code == 200
        bal_data = bal_res.json()
        assert bal_data["worker_name"] == "Ifeanyi Okon"
        assert bal_data["bank_name"] == "Paga"
        assert "PROVO Balance" in bal_data["display_text"]

        # Client phone cannot use worker USSD balance
        c_bal_res = await client.get("/ussd/balance", params={"phone": "+2348033334444"})
        assert c_bal_res.status_code == 403
        assert "reserved for registered PROVO artisans" in c_bal_res.json()["detail"]

        # 3. Create a funded job for the worker
        job_res = await client.post(
            "/jobs",
            headers={"Authorization": f"Bearer {client_token}"},
            json={
                "worker_id": worker_id,
                "trade": "BRICKLAYING",
                "description": "Store foundation work",
                "total_kobo": 1000000,
                "days": 2,
                "auto_fund_request": False,
            },
        )
        assert job_res.status_code == 201
        job_id = job_res.json()["id"]

        # Mark job as FUNDED in DB for testing
        from sqlalchemy import update

        from app.core.database import AsyncSessionLocal
        from app.db.models import Job
        async with AsyncSessionLocal() as session:
            await session.execute(update(Job).where(Job.id == job_id).values(status="FUNDED"))
            await session.commit()

        # 4. Key 2: Test USSD Pending Jobs endpoint
        pending_res = await client.get("/ussd/pending-jobs", params={"phone": "+2348122060708"})
        assert pending_res.status_code == 200
        p_data = pending_res.json()
        assert p_data["total_pending_jobs"] == 1
        assert p_data["jobs"][0]["trade"] == "BRICKLAYING"
        assert "Store foundation work" in p_data["display_text"]

        # 5. Test USSD Report Done endpoint
        report_res = await client.post(
            "/ussd/report-done",
            json={"phone": "+2348122060708", "job_id": job_id},
        )
        assert report_res.status_code == 200
        assert report_res.json()["day_number"] == 1
        assert "Client has 24hrs to confirm" in report_res.json()["display_text"]

        # 6. Key 3: Test USSD Withdraw endpoint
        withdraw_res = await client.post(
            "/ussd/withdraw",
            json={"phone": "+2348122060708"},
        )
        assert withdraw_res.status_code == 200
        w_data = withdraw_res.json()
        assert w_data["bank_name"] == "Paga"
        assert "Paga NUBAN" in w_data["display_text"]

        # 7. Test interactive session gateway (*347*900#)
        dial_root = await client.post("/ussd", json={"phoneNumber": "+2348122060708", "text": ""})
        assert dial_root.status_code == 200
        assert dial_root.json()["action"] == "CON"
        assert "1. Balance" in dial_root.json()["message"]

        dial_key1 = await client.post("/ussd", json={"phoneNumber": "+2348122060708", "text": "1"})
        assert dial_key1.status_code == 200
        assert dial_key1.json()["action"] == "END"
        assert "Total Paid:" in dial_key1.json()["message"]
