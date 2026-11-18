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
async def test_ussd_simulator_endpoints_for_worker(monkeypatch):
    # Mock SMS sending to avoid external 402 / network calls during test
    from app.integrations.sms.client import SMSClient
    async def mock_send_sms(*args, **kwargs):
        return {"status": "success", "id": "mock-sms-id"}
    monkeypatch.setattr(SMSClient, "send_sms", mock_send_sms)

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
        assert "otp_code" not in signup_res.json()

        # Verify OTP (this activates and provisions worker Paga account)
        verify_res = await client.post(
            "/auth/verify-otp",
            json={"phone": "+2348122060708", "code": "123456"},
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
        assert c_signup.status_code == 201
        assert "otp_code" not in c_signup.json()
        c_verify = await client.post(
            "/auth/verify-otp",
            json={"phone": "+2348033334444", "code": "123456"},
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


@pytest.mark.asyncio
async def test_ussd_withdrawal_lifecycle_and_balance_deduction(monkeypatch):
    from app.integrations.sms.client import SMSClient
    async def mock_send_sms(*args, **kwargs):
        return {"status": "success", "id": "mock-sms-id"}
    monkeypatch.setattr(SMSClient, "send_sms", mock_send_sms)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Register worker
        signup_res = await client.post(
            "/auth/signup",
            json={
                "name": "Chidi Nnamdi",
                "phone": "+2348055554444",
                "pin": "123456",
                "role": "worker",
                "trade": "ELECTRICAL",
            },
        )
        assert signup_res.status_code == 201

        verify_res = await client.post(
            "/auth/verify-otp",
            json={"phone": "+2348055554444", "code": "123456"},
        )
        assert verify_res.status_code == 200
        worker_id = verify_res.json()["user_id"]

        # 2. Register client & create job
        c_signup = await client.post(
            "/auth/signup",
            json={
                "name": "Dr Olatunji",
                "phone": "+2348066667777",
                "pin": "123456",
                "role": "client",
            },
        )
        assert c_signup.status_code == 201
        c_verify = await client.post(
            "/auth/verify-otp",
            json={"phone": "+2348066667777", "code": "123456"},
        )
        client_token = c_verify.json()["access_token"]

        job_res = await client.post(
            "/jobs",
            headers={"Authorization": f"Bearer {client_token}"},
            json={
                "worker_id": worker_id,
                "trade": "ELECTRICAL",
                "description": "Wiring clinic",
                "total_kobo": 500000,  # ₦5,000.00
                "days": 1,
                "auto_fund_request": False,
            },
        )
        assert job_res.status_code == 201
        job_id = job_res.json()["id"]

        # Settle Day 1 as PAID in database
        from sqlalchemy import update
        from app.core.database import AsyncSessionLocal
        from app.core.enums import DayStatus, JobStatus
        from app.db.models import Job, JobDay, Withdrawal

        async with AsyncSessionLocal() as session:
            await session.execute(update(Job).where(Job.id == job_id).values(status=JobStatus.COMPLETED.value))
            await session.execute(update(JobDay).where(JobDay.job_id == job_id).values(status=DayStatus.PAID.value))
            await session.commit()

        # 3. Check Initial Balance (₦5,000.00 earned, ₦0.00 withdrawn, ₦5,000.00 available)
        bal_res = await client.get("/ussd/balance", params={"phone": "+2348055554444"})
        assert bal_res.status_code == 200
        bal_data = bal_res.json()
        assert bal_data["total_earned_kobo"] == 500000
        assert bal_data["total_withdrawn_kobo"] == 0
        assert bal_data["available_balance_kobo"] == 500000
        assert bal_data["total_earned_naira"] == "₦5,000.00"
        assert bal_data["available_balance_naira"] == "₦5,000.00"
        assert "Available: ₦5,000.00" in bal_data["display_text"]

        # 4. Inquiry without amount (returns account info & balance)
        inquiry_res = await client.post("/ussd/withdraw", json={"phone": "+2348055554444"})
        assert inquiry_res.status_code == 200
        assert inquiry_res.json()["remaining_balance_naira"] == "₦5,000.00"

        # 5. Wrong PIN rejection (401)
        bad_pin_res = await client.post(
            "/ussd/withdraw",
            json={
                "phone": "+2348055554444",
                "amount_naira": 2000,
                "pin": "999999",
            },
        )
        assert bad_pin_res.status_code == 401
        assert "Invalid transaction PIN" in bad_pin_res.json()["detail"]

        # 6. Zero or negative amount rejection (400)
        zero_res = await client.post(
            "/ussd/withdraw",
            json={
                "phone": "+2348055554444",
                "amount_naira": 0,
                "pin": "123456",
            },
        )
        assert zero_res.status_code == 400
        assert "greater than zero" in zero_res.json()["detail"]

        # 7. Overdraft rejection (400 - requesting ₦8,000 when available is ₦5,000)
        overdraft_res = await client.post(
            "/ussd/withdraw",
            json={
                "phone": "+2348055554444",
                "amount_naira": 8000,
                "pin": "123456",
            },
        )
        assert overdraft_res.status_code == 400
        assert "Insufficient available balance" in overdraft_res.json()["detail"]

        # 8. Successful partial withdrawal (₦2,000)
        wd1_res = await client.post(
            "/ussd/withdraw",
            json={
                "phone": "+2348055554444",
                "amount_naira": 2000,
                "bank_code": "058",
                "account_number": "0123456789",
                "pin": "123456",
            },
        )
        assert wd1_res.status_code == 200
        wd1_data = wd1_res.json()
        assert wd1_data["status"] == "success"
        assert wd1_data["worker_name"] == "Chidi Nnamdi"
        assert wd1_data["bank_name"] == "058"
        assert wd1_data["account_number"] == "0123456789"
        assert wd1_data["amount_withdrawn_naira"] == "₦2,000.00"
        assert wd1_data["remaining_balance_naira"] == "₦3,000.00"
        assert wd1_data["reference"].startswith("WD-")

        # 9. Verify balance reflects deduction
        bal2_res = await client.get("/ussd/balance", params={"phone": "+2348055554444"})
        assert bal2_res.status_code == 200
        bal2_data = bal2_res.json()
        assert bal2_data["total_earned_kobo"] == 500000  # Credit score gross earnings untouched
        assert bal2_data["total_withdrawn_kobo"] == 200000
        assert bal2_data["available_balance_kobo"] == 300000
        assert bal2_data["available_balance_naira"] == "₦3,000.00"

        # 10. Withdraw remaining ₦3,000
        wd2_res = await client.post(
            "/ussd/withdraw",
            json={
                "phone": "+2348055554444",
                "amount_naira": 3000,
                "pin": "123456",
            },
        )
        assert wd2_res.status_code == 200
        assert wd2_res.json()["remaining_balance_naira"] == "₦0.00"

        # 11. Third attempt when empty fails (400)
        wd3_res = await client.post(
            "/ussd/withdraw",
            json={
                "phone": "+2348055554444",
                "amount_naira": 500,
                "pin": "123456",
            },
        )
        assert wd3_res.status_code == 400
        assert "Insufficient available balance" in wd3_res.json()["detail"]

        # 12. Verify Database records directly
        from sqlalchemy import select
        async with AsyncSessionLocal() as session:
            stmt = select(Withdrawal).where(Withdrawal.worker_id == worker_id)
            records = (await session.execute(stmt)).scalars().all()
            assert len(records) == 2
            assert sum(r.amount_kobo for r in records) == 500000
