import uuid
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import AsyncSessionLocal, Base, engine
from app.core.security import hash_pin
from app.db.models import Job, User
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
async def test_demo_pin_bypass():
    """Verifies that '123456' works as universal PIN in demo mode alongside real PIN."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create a verified user with a different PIN
        phone = "+2348011223344"
        async with AsyncSessionLocal() as session:
            user = User(
                name="Demo Tester",
                phone=phone,
                pin_hash=hash_pin("998877"),
                is_phone_verified=True,
                is_client=True,
            )
            session.add(user)
            await session.commit()

        # 1. Login with their actual PIN (998877) -> succeeds
        res_real = await client.post("/auth/login", json={"phone": phone, "pin": "998877"})
        assert res_real.status_code == 200

        # 2. Login with universal demo PIN (123456) -> succeeds because DEMO_MODE=True
        res_demo = await client.post("/auth/login", json={"phone": phone, "pin": "123456"})
        assert res_demo.status_code == 200
        assert "access_token" in res_demo.json()

        # 3. Login with an arbitrary wrong PIN (000000) -> fails
        res_bad = await client.post("/auth/login", json={"phone": phone, "pin": "000000"})
        assert res_bad.status_code == 401


@pytest.mark.asyncio
async def test_telegram_to_web_pin_setup_flow():
    """
    Tests the cross-platform flow:
    1. User starts on Telegram (has telegram_user_id, verified phone, no pin_hash).
    2. Attempting web signup redirects them to set their PIN.
    3. Attempting web login with invalid PIN informs them to set their PIN.
    4. They call /auth/request-set-pin and receive an OTP.
    5. They submit /auth/set-pin with the OTP and their new 6-digit PIN.
    6. They can now log in on the web with their new PIN!
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        phone = "+2348055554444"
        # Seed Telegram user
        async with AsyncSessionLocal() as session:
            tg_user = User(
                name="Telegram Pioneer",
                phone=phone,
                telegram_user_id=987654321,
                is_phone_verified=True,
                is_client=True,
                pin_hash=None,  # No web PIN yet
            )
            session.add(tg_user)
            await session.commit()

        # 1. User tries web signup -> helpful message directs to set-pin
        signup_res = await client.post(
            "/auth/signup",
            json={"name": "Telegram Pioneer", "phone": phone, "pin": "654321", "role": "client"},
        )
        assert signup_res.status_code == 400
        assert "registered via Telegram" in signup_res.json()["detail"]
        assert "set-pin" in signup_res.json()["detail"]

        # 2. User tries login with wrong PIN -> helpful message directs to set-pin
        bad_login = await client.post("/auth/login", json={"phone": phone, "pin": "000000"})
        assert bad_login.status_code == 401
        assert "registered via Telegram" in bad_login.json()["detail"]
        assert "set-pin" in bad_login.json()["detail"]

        # 3. Request set-pin OTP
        req_res = await client.post("/auth/request-set-pin", json={"phone": phone})
        assert req_res.status_code == 200
        data = req_res.json()
        assert "PIN setup OTP sent" in data["message"]
        assert "otp_code" not in data

        # 4. Set PIN with OTP (123456 demo code)
        set_res = await client.post(
            "/auth/set-pin",
            json={"phone": phone, "otp_code": "123456", "new_pin": "556677"},
        )
        assert set_res.status_code == 200
        tokens = set_res.json()
        assert "access_token" in tokens
        access_token = tokens["access_token"]

        # 5. Check /auth/me -> needs_pin_setup should now be False
        me_res = await client.get("/auth/me", headers={"Authorization": f"Bearer {access_token}"})
        assert me_res.status_code == 200
        assert me_res.json()["needs_pin_setup"] is False

        # 6. User can now log in directly using the new PIN!
        login_res = await client.post("/auth/login", json={"phone": phone, "pin": "556677"})
        assert login_res.status_code == 200
        assert "access_token" in login_res.json()


@pytest.mark.asyncio
async def test_assign_worker_later_via_api_and_web():
    """
    Verifies that a job created without an artisan ('Assign later')
    can be assigned an artisan later via REST API and web checkout page.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create client user
        client_phone = "+2348033332222"
        worker_phone = "+2348088887777"

        async with AsyncSessionLocal() as session:
            c_user = User(
                name="Madam Kemi",
                phone=client_phone,
                pin_hash=hash_pin("123456"),
                is_phone_verified=True,
                is_client=True,
            )
            session.add(c_user)
            await session.commit()
            client_id = c_user.id

        # Login to get token
        login_res = await client.post("/auth/login", json={"phone": client_phone, "pin": "123456"})
        token = login_res.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Create job without artisan (worker_id=None)
        job_create_payload = {
            "trade": "PAINTING",
            "description": "Painting 3 bedroom flat",
            "total_kobo": 3000000,
            "days": 3,
            "worker_id": None,
            "auto_fund_request": False,
        }
        create_res = await client.post("/jobs", json=job_create_payload, headers=headers)
        assert create_res.status_code == 201
        job_data = create_res.json()
        job_id = job_data["id"]
        assert job_data["worker_id"] is None

        # 1. Assign worker via REST API endpoint /jobs/{job_id}/assign-worker
        assign_res = await client.post(
            f"/jobs/{job_id}/assign-worker",
            json={"worker_phone": worker_phone},
            headers=headers,
        )
        assert assign_res.status_code == 200
        updated_job = assign_res.json()
        assert updated_job["worker_id"] is not None

        # Verify daily schedule days were also updated with the worker_id
        for day in updated_job["days_schedule"]:
            assert day["worker_id"] == updated_job["worker_id"]

        # 2. Test web checkout assign endpoint on a second unassigned job
        create_res2 = await client.post(
            "/jobs",
            json={
                "trade": "PLUMBING",
                "description": "Fix borehole connection",
                "total_kobo": 1500000,
                "days": 1,
                "worker_id": None,
                "auto_fund_request": False,
            },
            headers=headers,
        )
        job2_id = create_res2.json()["id"]

        # Call web checkout assign endpoint
        web_assign_res = await client.post(
            f"/checkout/{job2_id}/assign-worker",
            json={"worker_phone": "+2348099991111"},
        )
        assert web_assign_res.status_code == 200
        assert web_assign_res.json()["success"] is True

        # Check checkout page HTML renders 200 OK
        checkout_page_res = await client.get(f"/checkout/{job2_id}")
        assert checkout_page_res.status_code == 200
        assert "Proofwork Escrow Funding" in checkout_page_res.text


@pytest.mark.asyncio
async def test_set_pin_web_view_render():
    """Verifies that GET /set-pin renders a responsive HTML page."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/set-pin?phone=%2B2348012345678")
        assert res.status_code == 200
        assert "Proofwork" in res.text
        assert "Set your 6-digit security PIN" in res.text
        assert "+2348012345678" in res.text
