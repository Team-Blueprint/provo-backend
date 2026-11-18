import uuid
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.database import AsyncSessionLocal, Base, engine
from app.core.enums import DayStatus, JobStatus, TradeType
from app.core.security import hash_pin
from app.db.models import Dispute, Job, JobDay, User
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
async def test_assign_supervisor_flow():
    """
    Tests POST /jobs/{job_id}/supervisor:
    - Employer assigns supervisor with name and phone.
    - Token starting with 'sup_' is generated.
    - Unauthorized user cannot assign supervisor.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create employer and stranger users
        client_phone = "+2348011112222"
        stranger_phone = "+2348099990000"

        async with AsyncSessionLocal() as session:
            c_user = User(
                name="Chief Okon",
                phone=client_phone,
                pin_hash=hash_pin("123456"),
                is_phone_verified=True,
                is_client=True,
            )
            s_user = User(
                name="Stranger",
                phone=stranger_phone,
                pin_hash=hash_pin("123456"),
                is_phone_verified=True,
                is_client=True,
            )
            session.add_all([c_user, s_user])
            await session.commit()

        # Login employer
        login_res = await client.post("/auth/login", json={"phone": client_phone, "pin": "123456"})
        token = login_res.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Create job
        job_res = await client.post(
            "/jobs",
            json={
                "trade": "BRICKLAYING",
                "description": "Perimeter block wall",
                "total_kobo": 4500000,
                "days": 3,
                "auto_fund_request": False,
            },
            headers=headers,
        )
        assert job_res.status_code == 201
        job_id = job_res.json()["id"]

        # Assign supervisor
        sup_payload = {
            "supervisor_name": "Foreman Bassey",
            "supervisor_phone": "+2348051120944",
        }
        assign_res = await client.post(
            f"/jobs/{job_id}/supervisor",
            json=sup_payload,
            headers=headers,
        )
        assert assign_res.status_code == 200
        assigned_data = assign_res.json()
        assert assigned_data["supervisor_name"] == "Foreman Bassey"
        assert assigned_data["supervisor_phone"] == "+2348051120944"
        assert assigned_data["supervisor_token"] is not None
        assert assigned_data["supervisor_token"].startswith("sup_")

        # Stranger attempts to assign supervisor -> 400 Unauthorized
        login_stranger = await client.post("/auth/login", json={"phone": stranger_phone, "pin": "123456"})
        stranger_token = login_stranger.json()["access_token"]
        bad_assign = await client.post(
            f"/jobs/{job_id}/supervisor",
            json=sup_payload,
            headers={"Authorization": f"Bearer {stranger_token}"},
        )
        assert bad_assign.status_code == 400
        assert "Only the job's employer can assign a supervisor" in bad_assign.json()["detail"]


@pytest.mark.asyncio
async def test_supervisor_view_sanitization():
    """
    Tests GET /jobs/{job_id}/supervisor-view?token={supervisor_token}:
    - Public, token-gated endpoint.
    - Zero client PII (no client_id, client name, client phone).
    - Invalid token returns 404.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client_phone = "+2348011112222"
        worker_phone = "+2348022223333"

        async with AsyncSessionLocal() as session:
            c_user = User(
                name="Secret Client Name",
                phone=client_phone,
                pin_hash=hash_pin("123456"),
                is_phone_verified=True,
                is_client=True,
            )
            w_user = User(
                name="Emem Bassey",
                phone=worker_phone,
                pin_hash=hash_pin("123456"),
                is_phone_verified=True,
                is_worker=True,
            )
            session.add_all([c_user, w_user])
            await session.commit()

        login_res = await client.post("/auth/login", json={"phone": client_phone, "pin": "123456"})
        token = login_res.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Create job
        job_res = await client.post(
            "/jobs",
            json={
                "trade": "BRICKLAYING",
                "description": "Perimeter block wall",
                "total_kobo": 4500000,
                "days": 3,
                "auto_fund_request": False,
            },
            headers=headers,
        )
        job_id = job_res.json()["id"]

        # Assign worker
        await client.post(f"/jobs/{job_id}/assign-worker", json={"worker_phone": worker_phone}, headers=headers)

        # Assign supervisor
        sup_res = await client.post(
            f"/jobs/{job_id}/supervisor",
            json={"supervisor_name": "Foreman Bassey", "supervisor_phone": "+2348051120944"},
            headers=headers,
        )
        sup_token = sup_res.json()["supervisor_token"]

        # 1. Access with valid token (unauthenticated public client)
        view_res = await client.get(f"/jobs/{job_id}/supervisor-view?token={sup_token}")
        assert view_res.status_code == 200
        view_data = view_res.json()

        # Check required fields
        assert view_data["trade"] == "BRICKLAYING"
        assert view_data["description"] == "Perimeter block wall"
        assert view_data["total_kobo"] == 4500000
        assert view_data["days_count"] == 3
        assert view_data["worker_name"] == "Emem Bassey"
        assert view_data["supervisor_name"] == "Foreman Bassey"
        assert view_data["supervisor_phone"] == "+2348051120944"
        assert len(view_data["days_schedule"]) == 3

        # Critical: Verify NO client PII in response
        assert "client_id" not in view_data
        assert "client_name" not in view_data
        assert "client_phone" not in view_data
        assert "Secret Client Name" not in view_res.text
        assert client_phone not in view_res.text

        # 2. Access with invalid token -> 404
        bad_token_res = await client.get(f"/jobs/{job_id}/supervisor-view?token=sup_invalidtoken123")
        assert bad_token_res.status_code == 404

        # 3. Access with missing token -> 422 Unprocessable Entity
        missing_token_res = await client.get(f"/jobs/{job_id}/supervisor-view")
        assert missing_token_res.status_code == 422


@pytest.mark.asyncio
async def test_supervisor_confirm_day_with_proof_photo():
    """
    Tests POST /jobs/{job_id}/days/{day_number}/supervisor-confirm?token={supervisor_token}:
    - Worker marks day done.
    - Supervisor confirms with proof photograph and phone cross-check.
    - Payout is triggered and day status becomes CONFIRMED/PAID.
    - Invalid token or phone mismatch returns 403.
    - Confirming pending day (not DONE_REPORTED) returns 400.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client_phone = "+2348011112222"
        worker_phone = "+2348022223333"
        supervisor_phone = "+2348051120944"

        async with AsyncSessionLocal() as session:
            c_user = User(
                name="Alhaji Danladi",
                phone=client_phone,
                pin_hash=hash_pin("123456"),
                is_phone_verified=True,
                is_client=True,
            )
            w_user = User(
                name="Emem Bassey",
                phone=worker_phone,
                pin_hash=hash_pin("123456"),
                is_phone_verified=True,
                is_worker=True,
            )
            session.add_all([c_user, w_user])
            await session.commit()

        # Login employer & worker
        client_login = await client.post("/auth/login", json={"phone": client_phone, "pin": "123456"})
        client_token = client_login.json()["access_token"]
        client_headers = {"Authorization": f"Bearer {client_token}"}

        worker_login = await client.post("/auth/login", json={"phone": worker_phone, "pin": "123456"})
        worker_token = worker_login.json()["access_token"]
        worker_headers = {"Authorization": f"Bearer {worker_token}"}

        # Create funded job
        job_res = await client.post(
            "/jobs",
            json={
                "trade": "BRICKLAYING",
                "description": "Foundation setting",
                "total_kobo": 3000000,
                "days": 2,
                "worker_id": None,
                "auto_fund_request": False,
            },
            headers=client_headers,
        )
        job_id = job_res.json()["id"]

        # Assign worker & supervisor
        await client.post(f"/jobs/{job_id}/assign-worker", json={"worker_phone": worker_phone}, headers=client_headers)
        sup_res = await client.post(
            f"/jobs/{job_id}/supervisor",
            json={"supervisor_name": "Foreman Bassey", "supervisor_phone": supervisor_phone},
            headers=client_headers,
        )
        sup_token = sup_res.json()["supervisor_token"]

        # Fund the job in DB
        async with AsyncSessionLocal() as session:
            stmt = select(Job).where(Job.id == uuid.UUID(job_id))
            job = (await session.execute(stmt)).scalar_one()
            job.status = JobStatus.FUNDED.value
            await session.commit()

        # 1. Attempt to confirm Day 1 while still PENDING -> returns 400
        early_confirm = await client.post(
            f"/jobs/{job_id}/days/1/supervisor-confirm?token={sup_token}",
            json={},
        )
        assert early_confirm.status_code == 400
        assert "Cannot confirm day with status 'PENDING'" in early_confirm.json()["detail"]

        # 2. Worker reports Day 1 done
        done_res = await client.post(f"/jobs/{job_id}/days/1/done", headers=worker_headers)
        assert done_res.status_code == 200
        assert done_res.json()["status"] == DayStatus.DONE_REPORTED.value

        # 3. Supervisor confirms with mismatched phone number -> returns 403 Forbidden
        bad_phone_confirm = await client.post(
            f"/jobs/{job_id}/days/1/supervisor-confirm?token={sup_token}",
            json={"phone": "+2348000000000"},
        )
        assert bad_phone_confirm.status_code == 403
        assert "Phone number does not match" in bad_phone_confirm.json()["detail"]

        # 4. Supervisor confirms with wrong token -> returns 403 Forbidden
        bad_token_confirm = await client.post(
            f"/jobs/{job_id}/days/1/supervisor-confirm?token=sup_wrongtoken",
            json={"phone": supervisor_phone},
        )
        assert bad_token_confirm.status_code == 403

        # 5. Supervisor confirms with valid token, correct phone, and proof photo
        proof_photo_b64 = "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQEASABIAAD/sample"
        good_confirm = await client.post(
            f"/jobs/{job_id}/days/1/supervisor-confirm?token={sup_token}",
            json={
                "proof_photo": proof_photo_b64,
                "phone": supervisor_phone,
            },
        )
        assert good_confirm.status_code == 200
        confirm_data = good_confirm.json()
        assert confirm_data["status"] in (DayStatus.CONFIRMED.value, DayStatus.PAID.value)
        assert confirm_data["proof_photo"] == proof_photo_b64

        # 6. Verify in DB that proof_photo is saved
        async with AsyncSessionLocal() as session:
            stmt = select(JobDay).where(JobDay.job_id == uuid.UUID(job_id), JobDay.day_number == 1)
            day1 = (await session.execute(stmt)).scalar_one()
            assert day1.proof_photo == proof_photo_b64


@pytest.mark.asyncio
async def test_supervisor_dispute_day_flow():
    """
    Tests POST /jobs/{job_id}/days/{day_number}/supervisor-dispute?token={supervisor_token}:
    - Freezes the 24-hour auto-release timer.
    - Transitions day status to DISPUTED.
    - Creates Dispute row with opened_by falling back to job.client_id.
    - Invalid token returns 403.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client_phone = "+2348011112222"
        worker_phone = "+2348022223333"
        supervisor_phone = "+2348051120944"

        async with AsyncSessionLocal() as session:
            c_user = User(
                name="Alhaji Danladi",
                phone=client_phone,
                pin_hash=hash_pin("123456"),
                is_phone_verified=True,
                is_client=True,
            )
            w_user = User(
                name="Emem Bassey",
                phone=worker_phone,
                pin_hash=hash_pin("123456"),
                is_phone_verified=True,
                is_worker=True,
            )
            session.add_all([c_user, w_user])
            await session.commit()
            client_id = c_user.id

        client_login = await client.post("/auth/login", json={"phone": client_phone, "pin": "123456"})
        client_headers = {"Authorization": f"Bearer {client_login.json()['access_token']}"}

        worker_login = await client.post("/auth/login", json={"phone": worker_phone, "pin": "123456"})
        worker_headers = {"Authorization": f"Bearer {worker_login.json()['access_token']}"}

        # Create & fund job
        job_res = await client.post(
            "/jobs",
            json={
                "trade": "ELECTRICAL",
                "description": "Conduit piping",
                "total_kobo": 2000000,
                "days": 1,
                "auto_fund_request": False,
            },
            headers=client_headers,
        )
        job_id = job_res.json()["id"]

        await client.post(f"/jobs/{job_id}/assign-worker", json={"worker_phone": worker_phone}, headers=client_headers)
        sup_res = await client.post(
            f"/jobs/{job_id}/supervisor",
            json={"supervisor_name": "Site Engineer Tunde", "supervisor_phone": supervisor_phone},
            headers=client_headers,
        )
        sup_token = sup_res.json()["supervisor_token"]

        async with AsyncSessionLocal() as session:
            stmt = select(Job).where(Job.id == uuid.UUID(job_id))
            job = (await session.execute(stmt)).scalar_one()
            job.status = JobStatus.FUNDED.value
            await session.commit()

        # Worker reports done
        await client.post(f"/jobs/{job_id}/days/1/done", headers=worker_headers)

        # 1. Invalid token dispute -> 403
        bad_dispute = await client.post(
            f"/jobs/{job_id}/days/1/supervisor-dispute?token=sup_invalid",
            json={"reason": "Pipes not laid to depth"},
        )
        assert bad_dispute.status_code == 403

        # 2. Valid token dispute
        dispute_res = await client.post(
            f"/jobs/{job_id}/days/1/supervisor-dispute?token={sup_token}",
            json={"reason": "Pipes not laid to depth specification"},
        )
        assert dispute_res.status_code == 200
        dispute_data = dispute_res.json()
        assert dispute_data["status"] == DayStatus.DISPUTED.value

        # 3. Verify in DB: release_at is None (frozen) and Dispute record created with opened_by fallback
        async with AsyncSessionLocal() as session:
            stmt = select(JobDay).where(JobDay.job_id == uuid.UUID(job_id), JobDay.day_number == 1)
            day = (await session.execute(stmt)).scalar_one()
            assert day.status == DayStatus.DISPUTED.value
            assert day.release_at is None

            disp_stmt = select(Dispute).where(Dispute.day_id == day.id)
            dispute = (await session.execute(disp_stmt)).scalar_one()
            assert dispute.reason == "Pipes not laid to depth specification"
            assert dispute.opened_by == client_id  # fallback to job.client_id
