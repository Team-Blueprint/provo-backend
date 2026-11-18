import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.database import AsyncSessionLocal, Base, engine
from app.core.enums import DayStatus, JobStatus, TradeType
from app.core.security import hash_pin
from app.db.models import Job, JobDay, User, WorkerProfile
from app.integrations.telegram.handlers import (
    AssignSupervisorStates,
    ReviewPhotoStates,
    handle_assign_supervisor_name,
    handle_assign_supervisor_phone,
    handle_assign_supervisor_start,
    handle_inline_review_photo_received,
    handle_inline_review_photo_start,
    handle_start,
)
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
async def test_supervisor_verify_web_view_zero_employer_pii():
    """
    Tests GET /supervisor/verify?job={job_id}&token={token}:
    - Returns 200 HTML with job trade, artisan info, day schedule.
    - Zero employer PII is present in HTML (crucial privacy constraint).
    - Returns 404 for invalid job or token.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create client, worker, and job in DB
        async with AsyncSessionLocal() as session:
            employer = User(
                name="Chief Okonkwo Billionaire",
                phone="+2348011223344",
                pin_hash=hash_pin("123456"),
                is_phone_verified=True,
                is_client=True,
            )
            worker = User(
                name="Tunde The Plumber",
                phone="+2348099887766",
                pin_hash=hash_pin("123456"),
                is_phone_verified=True,
                is_worker=True,
            )
            session.add_all([employer, worker])
            await session.flush()

            job = Job(
                client_id=employer.id,
                worker_id=worker.id,
                funding_reference=f"REF-{uuid.uuid4().hex[:12].upper()}",
                trade=TradeType.PLUMBING.value,
                description="Fix master bedroom jacuzzi plumbing",
                total_kobo=5000000,
                days=2,
                status=JobStatus.IN_PROGRESS.value,
                supervisor_name="Engr. Babatunde",
                supervisor_phone="+2348077665544",
                supervisor_token="sup_testtoken123456",
            )
            session.add(job)
            await session.flush()

            day1 = JobDay(
                job_id=job.id,
                day_number=1,
                amount_kobo=2500000,
                status=DayStatus.DONE_REPORTED.value,
            )
            day2 = JobDay(
                job_id=job.id,
                day_number=2,
                amount_kobo=2500000,
                status=DayStatus.PENDING.value,
            )
            session.add_all([day1, day2])
            await session.commit()
            job_id = job.id
            token = job.supervisor_token

        # 1. Valid request
        res = await client.get(f"/supervisor/verify?job={job_id}&token={token}")
        assert res.status_code == 200
        html = res.text

        # Verify supervisor portal content
        assert "PLUMBING" in html
        assert "Fix master bedroom jacuzzi plumbing" in html
        assert "Tunde The Plumber" in html
        assert "Engr. Babatunde" in html
        assert "Day 1" in html
        assert "Day 2" in html
        assert "Confirm Day Pay" in html

        # ZERO Employer PII check
        assert "Chief Okonkwo Billionaire" not in html
        assert "+2348011223344" not in html
        assert "employer" not in html.lower() or "job employer" in html.lower()  # Only generic text

        # 2. Invalid token -> 404
        bad_token_res = await client.get(f"/supervisor/verify?job={job_id}&token=wrongtoken")
        assert bad_token_res.status_code == 404
        assert "Invalid or Expired Link" in bad_token_res.text


@pytest.mark.asyncio
async def test_telegram_start_deep_link_supervisor():
    """
    Tests Telegram /start sup_<token> deep link:
    - Automatically displays supervisor portal with day schedule.
    - Zero employer PII displayed.
    """
    async with AsyncSessionLocal() as session:
        employer = User(
            name="Senator Employer",
            phone="+2348011223355",
            is_client=True,
        )
        worker = User(
            name="Musa Painter",
            phone="+2348099887755",
            is_worker=True,
        )
        session.add_all([employer, worker])
        await session.flush()

        job = Job(
            client_id=employer.id,
            worker_id=worker.id,
            funding_reference=f"REF-{uuid.uuid4().hex[:12].upper()}",
            trade=TradeType.PAINTING.value,
            description="Exterior duplex painting",
            total_kobo=3000000,
            days=1,
            status=JobStatus.IN_PROGRESS.value,
            supervisor_name="Foreman Dan",
            supervisor_phone="+2348033221100",
            supervisor_token="sup_deep123",
        )
        session.add(job)
        await session.flush()

        day1 = JobDay(
            job_id=job.id,
            day_number=1,
            amount_kobo=3000000,
            status=DayStatus.DONE_REPORTED.value,
        )
        session.add(day1)
        await session.commit()

    # Mock Telegram Message
    mock_message = MagicMock()
    mock_message.text = "/start sup_deep123"
    mock_message.from_user = MagicMock(id=998877)
    mock_message.answer = AsyncMock()

    await handle_start(mock_message)

    assert mock_message.answer.called
    call_args = mock_message.answer.call_args
    sent_text = call_args[0][0]
    sent_keyboard = call_args[1].get("reply_markup")

    # Check text content
    assert "Supervisor Portal: PAINTING" in sent_text
    assert "Exterior duplex painting" in sent_text
    assert "Musa Painter" in sent_text
    assert "Day 1: ⏳ `DONE_REPORTED`" in sent_text

    # Check zero employer PII
    assert "Senator Employer" not in sent_text
    assert "+2348011223355" not in sent_text

    # Check keyboard has review actions
    assert sent_keyboard is not None


@pytest.mark.asyncio
async def test_telegram_assign_supervisor_fsm():
    """
    Tests Telegram FSM flow for assigning a supervisor to a job:
    1. Callback 'assign_supervisor:<job_id>' prompts for name.
    2. Message with name sets state to waiting_for_phone.
    3. Message with phone completes assignment and returns supervisor links.
    """
    tg_user_id = 12345678
    async with AsyncSessionLocal() as session:
        employer = User(
            name="Madam Kemi",
            phone="+2348012345678",
            telegram_user_id=tg_user_id,
            is_client=True,
            is_phone_verified=True,
        )
        session.add(employer)
        await session.flush()

        job = Job(
            client_id=employer.id,
            funding_reference=f"REF-{uuid.uuid4().hex[:12].upper()}",
            trade=TradeType.CARPENTRY.value,
            description="Living room cabinets",
            total_kobo=4000000,
            days=2,
            status=JobStatus.DRAFT.value,
        )
        session.add(job)
        await session.commit()
        job_id = job.id

    # Mock FSM State
    state_data = {}

    class MockFSM:
        def __init__(self):
            self.current_state = None

        async def set_state(self, state):
            self.current_state = state

        async def update_data(self, **kwargs):
            state_data.update(kwargs)

        async def get_data(self):
            return state_data

        async def clear(self):
            state_data.clear()
            self.current_state = None

    mock_state = MockFSM()

    # Step 1: Callback query to initiate
    mock_query = MagicMock()
    mock_query.data = f"assign_supervisor:{job_id}"
    mock_query.from_user = MagicMock(id=tg_user_id)
    mock_query.message = MagicMock()
    mock_query.message.answer = AsyncMock()
    mock_query.answer = AsyncMock()

    await handle_assign_supervisor_start(mock_query, mock_state)
    assert mock_state.current_state == AssignSupervisorStates.waiting_for_name
    assert state_data["supervisor_job_id"] == str(job_id)

    # Step 2: Employer provides supervisor name
    name_msg = MagicMock()
    name_msg.text = "Engr. Nnamdi"
    name_msg.from_user = MagicMock(id=tg_user_id)
    name_msg.answer = AsyncMock()

    await handle_assign_supervisor_name(name_msg, mock_state)
    assert mock_state.current_state == AssignSupervisorStates.waiting_for_phone
    assert state_data["supervisor_name"] == "Engr. Nnamdi"

    # Step 3: Employer provides supervisor phone
    phone_msg = MagicMock()
    phone_msg.text = "08091234567"
    phone_msg.from_user = MagicMock(id=tg_user_id)
    phone_msg.answer = AsyncMock()

    await handle_assign_supervisor_phone(phone_msg, mock_state)

    # Verify response
    assert phone_msg.answer.called
    response_text = phone_msg.answer.call_args[0][0]
    assert "Supervisor Assigned Successfully!" in response_text
    assert "Engr. Nnamdi" in response_text
    assert "https://proofwork.app/supervisor/verify" in response_text
    assert "t.me/provo_bot?start=sup_" in response_text

    # Verify DB state
    async with AsyncSessionLocal() as session:
        stmt = select(Job).where(Job.id == job_id)
        saved_job = (await session.execute(stmt)).scalar_one()
        assert saved_job.supervisor_name == "Engr. Nnamdi"
        assert saved_job.supervisor_phone == "+2348091234567"
        assert saved_job.supervisor_token.startswith("sup_")


@pytest.mark.asyncio
async def test_telegram_review_with_photo_flow():
    """
    Tests Telegram photo confirmation flow:
    - User clicks review_photo:<job_id>:<day_number>
    - User sends photo
    - Milestone is confirmed and proof_photo is saved
    """
    tg_user_id = 99881122
    async with AsyncSessionLocal() as session:
        employer = User(
            name="Mr. David",
            phone="+2348055443322",
            telegram_user_id=tg_user_id,
            is_client=True,
            is_phone_verified=True,
        )
        worker = User(
            name="Artisan Joe",
            phone="+2348066554433",
            is_worker=True,
            is_phone_verified=True,
        )
        session.add_all([employer, worker])
        await session.flush()

        job = Job(
            client_id=employer.id,
            worker_id=worker.id,
            funding_reference=f"REF-{uuid.uuid4().hex[:12].upper()}",
            trade=TradeType.CARPENTRY.value,
            description="Wardrobe installation",
            total_kobo=2000000,
            days=1,
            status=JobStatus.IN_PROGRESS.value,
        )
        session.add(job)
        await session.flush()

        day1 = JobDay(
            job_id=job.id,
            day_number=1,
            amount_kobo=2000000,
            status=DayStatus.DONE_REPORTED.value,
        )
        session.add(day1)
        await session.commit()
        job_id = job.id

    state_data = {}

    class MockFSM:
        def __init__(self):
            self.current_state = None

        async def set_state(self, state):
            self.current_state = state

        async def update_data(self, **kwargs):
            state_data.update(kwargs)

        async def get_data(self):
            return state_data

        async def clear(self):
            state_data.clear()
            self.current_state = None

    mock_state = MockFSM()

    # Step 1: Click photo button
    mock_query = MagicMock()
    mock_query.data = f"review_photo:{job_id}:1"
    mock_query.from_user = MagicMock(id=tg_user_id)
    mock_query.message = MagicMock()
    mock_query.message.answer = AsyncMock()
    mock_query.answer = AsyncMock()

    await handle_inline_review_photo_start(mock_query, mock_state)
    assert mock_state.current_state == ReviewPhotoStates.waiting_for_photo
    assert state_data["photo_job_id"] == str(job_id)
    assert state_data["photo_day_number"] == 1

    # Step 2: User uploads photo
    photo_item = MagicMock(file_id="AgACAgIAAxkBAAIBYm...")
    mock_photo_msg = MagicMock()
    mock_photo_msg.photo = [photo_item]
    mock_photo_msg.from_user = MagicMock(id=tg_user_id)
    mock_photo_msg.answer = AsyncMock()

    await handle_inline_review_photo_received(mock_photo_msg, mock_state)

    assert mock_photo_msg.answer.called
    reply = mock_photo_msg.answer.call_args[0][0]
    assert "Confirmed with Proof Photo!" in reply

    # Verify DB state
    async with AsyncSessionLocal() as session:
        stmt = select(JobDay).where(JobDay.job_id == job_id, JobDay.day_number == 1)
        saved_day = (await session.execute(stmt)).scalar_one()
        assert saved_day.status in (DayStatus.CONFIRMED.value, DayStatus.PAID.value)
        assert saved_day.proof_photo == "tg_file:AgACAgIAAxkBAAIBYm..."
