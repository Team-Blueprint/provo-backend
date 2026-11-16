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
        unreg_res = await client.post("/auth/login", json={"phone": "+2348099998888"})
        assert unreg_res.status_code == 400
        assert "not registered" in unreg_res.json()["detail"]

        # Register first
        await client.post(
            "/auth/signup",
            json={"name": "Kelechi Okafor", "phone": "+2348099998888", "role": "client"},
        )

        # Login request succeeds
        login_res = await client.post("/auth/login", json={"phone": "+2348099998888"})
        assert login_res.status_code == 200
        assert "Login verification code sent" in login_res.json()["message"]


@pytest.mark.asyncio
async def test_dedicated_worker_and_client_signup_endpoints():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Worker signup with artisan track
        worker_res = await client.post(
            "/auth/signup/worker",
            json={
                "name": "Emem Udoh",
                "phone": "+2348077778888",
                "trade": "BRICKLAYING",
            },
        )
        assert worker_res.status_code == 201
        assert "artisan profile" in worker_res.json()["message"]

        # Client signup
        client_res = await client.post(
            "/auth/signup/client",
            json={
                "name": "Chief Bassey",
                "phone": "+2348022223333",
            },
        )
        assert client_res.status_code == 201
        assert "client account" in client_res.json()["message"]

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
