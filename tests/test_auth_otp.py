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
async def test_passwordless_signup_and_verify_otp_flow():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Signup
        signup_payload = {
            "name": "Babatunde Alabi",
            "phone": "+2348012345678",
            "pin": "123456",
            "role": "worker",
        }
        res = await client.post("/auth/signup", json=signup_payload)
        assert res.status_code == 201
        data = res.json()
        assert "Verification code sent" in data["message"]

        # 2. Duplicate signup before verification updates or succeeds
        # Query DB directly to get the code or verify using known mock
        # Let's inspect DB for the generated code
        from sqlalchemy import select

        from app.core.database import AsyncSessionLocal
        from app.db.models import AuthOTP

        async with AsyncSessionLocal() as session:
            stmt = select(AuthOTP).where(AuthOTP.phone == "+2348012345678")
            otp_record = (await session.execute(stmt)).scalars().first()
            assert otp_record is not None
            assert not otp_record.is_used

        # 3. Wrong OTP attempt fails
        bad_res = await client.post(
            "/auth/verify-otp",
            json={"phone": "+2348012345678", "code": "000000"},
        )
        assert bad_res.status_code == 400
        assert "Incorrect OTP code" in bad_res.json()["detail"]

        # 4. For testing verification, generate a known code and verify
        # Let's test with a simulated code by checking against hash
        from app.core.security import hash_otp_code
        test_code = "772211"
        async with AsyncSessionLocal() as session:
            stmt = select(AuthOTP).where(AuthOTP.phone == "+2348012345678")
            record = (await session.execute(stmt)).scalars().first()
            record.code_hash = hash_otp_code(test_code)
            record.attempts = 0
            await session.commit()

        # 5. Submit valid code
        verify_res = await client.post(
            "/auth/verify-otp",
            json={"phone": "+2348012345678", "code": test_code},
        )
        assert verify_res.status_code == 200
        token_data = verify_res.json()
        assert "access_token" in token_data
        assert "refresh_token" in token_data
        assert token_data["is_worker"] is True
        assert token_data["name"] == "Babatunde Alabi"

        # 6. Verify token via /auth/me
        token = token_data["access_token"]
        me_res = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me_res.status_code == 200
        assert me_res.json()["phone"] == "+2348012345678"


@pytest.mark.asyncio
async def test_passwordless_login_for_registered_user():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Unregistered phone fails login
        unreg_res = await client.post("/auth/login", json={"phone": "+2348099998888", "pin": "123456"})
        assert unreg_res.status_code == 401
        assert "Invalid phone number or PIN" in unreg_res.json()["detail"]

        # Register first
        signup_res = await client.post(
            "/auth/signup",
            json={"name": "Kelechi Okafor", "phone": "+2348099998888", "pin": "654321", "role": "client"},
        )
        assert signup_res.status_code == 201

        # Wrong PIN fails login
        bad_pin_res = await client.post("/auth/login", json={"phone": "+2348099998888", "pin": "111111"})
        assert bad_pin_res.status_code == 401
        assert "Invalid phone number or PIN" in bad_pin_res.json()["detail"]

        # Before phone verification, login requires verification first
        unverified_res = await client.post("/auth/login", json={"phone": "+2348099998888", "pin": "654321"})
        assert unverified_res.status_code == 401
        assert "Account not yet verified" in unverified_res.json()["detail"]

        # Verify phone with demo or real OTP code
        otp_code = signup_res.json()["otp_code"]
        verify_res = await client.post("/auth/verify-otp", json={"phone": "+2348099998888", "code": otp_code})
        assert verify_res.status_code == 200

        # Login with correct PIN now succeeds directly without OTP!
        login_res = await client.post("/auth/login", json={"phone": "+2348099998888", "pin": "654321"})
        assert login_res.status_code == 200
        tokens = login_res.json()
        assert "access_token" in tokens
        assert "refresh_token" in tokens
        assert tokens["phone"] == "+2348099998888"

        # Resend OTP succeeds
        resend_res = await client.post("/auth/resend-otp", json={"phone": "+2348099998888"})
        assert resend_res.status_code == 200
        assert "Fresh verification code" in resend_res.json()["message"]
        assert resend_res.json()["otp_code"] is not None


@pytest.mark.asyncio
async def test_unified_worker_and_client_signup_and_refresh():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Worker signup with artisan track
        worker_res = await client.post(
            "/auth/signup",
            json={
                "name": "Emem Udoh",
                "phone": "+2348077778888",
                "pin": "123456",
                "role": "worker",
                "trade": "BRICKLAYING",
            },
        )
        assert worker_res.status_code == 201
        worker_data = worker_res.json()
        assert worker_data["otp_code"] is not None

        # Client signup
        client_res = await client.post(
            "/auth/signup",
            json={
                "name": "Chief Bassey",
                "phone": "+2348022223333",
                "pin": "654321",
                "role": "client",
            },
        )
        assert client_res.status_code == 201
        client_data = client_res.json()
        assert client_data["otp_code"] is not None

        # Verify client OTP to get real refresh_token
        verify_client = await client.post(
            "/auth/verify-otp",
            json={
                "phone": "+2348022223333",
                "code": client_data["otp_code"],
            },
        )
        assert verify_client.status_code == 200
        client_tokens = verify_client.json()
        assert client_tokens["access_token"] is not None
        assert client_tokens["refresh_token"] is not None

        # Test token refresh endpoint
        refresh_res = await client.post(
            "/auth/refresh",
            json={"refresh_token": client_tokens["refresh_token"]},
        )
        assert refresh_res.status_code == 200
        refreshed_data = refresh_res.json()
        assert refreshed_data["access_token"] is not None
        assert refreshed_data["refresh_token"] is not None

        # Verify worker profile created with trade in DB
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        from app.core.database import AsyncSessionLocal
        from app.db.models import User

        async with AsyncSessionLocal() as session:
            stmt = (
                select(User)
                .where(User.phone == "+2348077778888")
                .options(selectinload(User.worker_profile))
            )
            w_user = (await session.execute(stmt)).scalar_one()
            assert w_user.is_worker is True
            assert w_user.worker_profile is not None
            assert w_user.worker_profile.trade == "BRICKLAYING"
