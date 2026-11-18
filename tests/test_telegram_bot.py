import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.config import settings
from app.core.database import AsyncSessionLocal, Base, engine
from app.db.models import User
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
async def test_telegram_webhook_secret_header_verification():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # If secret is set
        original_secret = settings.TELEGRAM_WEBHOOK_SECRET
        settings.TELEGRAM_WEBHOOK_SECRET = "secure-tg-secret-123"

        try:
            # Bad secret returns 403
            bad_res = await client.post(
                "/webhooks/telegram",
                json={"update_id": 100},
                headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-secret"},
            )
            assert bad_res.status_code == 403

            # Correct secret returns 200
            good_res = await client.post(
                "/webhooks/telegram",
                json={"update_id": 101},
                headers={"X-Telegram-Bot-Api-Secret-Token": "secure-tg-secret-123"},
            )
            assert good_res.status_code == 200
            assert good_res.json()["ok"] is True
        finally:
            settings.TELEGRAM_WEBHOOK_SECRET = original_secret


@pytest.mark.asyncio
async def test_telegram_contact_share_registration():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Mock contact update from Telegram
        update_payload = {
            "update_id": 2001,
            "message": {
                "message_id": 10,
                "date": 1700000000,
                "chat": {"id": 888777, "type": "private"},
                "from": {
                    "id": 888777,
                    "is_bot": False,
                    "first_name": "Emem",
                    "last_name": "Udoh",
                    "username": "emem_artisan",
                },
                "contact": {
                    "phone_number": "+2348033339999",
                    "first_name": "Emem",
                    "user_id": 888777,
                },
            },
        }

        res = await client.post("/webhooks/telegram", json=update_payload)
        assert res.status_code == 200
        assert res.json()["ok"] is True

        # Verify User created in DB with telegram_user_id and phone verified
        async with AsyncSessionLocal() as session:
            stmt = select(User).where(User.telegram_user_id == 888777)
            user = (await session.execute(stmt)).scalar_one_or_none()
            assert user is not None
            assert user.phone == "+2348033339999"
            assert user.is_phone_verified is True


@pytest.mark.asyncio
async def test_job_days_custom_picker_and_input():
    from unittest.mock import AsyncMock, MagicMock
    from app.integrations.telegram.keyboards import job_days_picker_keyboard
    from app.integrations.telegram.handlers import (
        process_job_days_button,
        process_job_days_text,
        CreateJobStates,
    )

    kb = job_days_picker_keyboard()
    # Check that custom button is in the keyboard
    found_custom = False
    for row in kb.inline_keyboard:
        for btn in row:
            if btn.callback_data == "create_job_days:custom":
                found_custom = True
                assert "Custom" in btn.text
    assert found_custom is True

    # Test tapping the custom button
    mock_query = MagicMock()
    mock_query.data = "create_job_days:custom"
    mock_query.message = MagicMock()
    mock_query.message.answer = AsyncMock()
    mock_query.answer = AsyncMock()

    mock_state = MagicMock()
    mock_state.update_data = AsyncMock()
    mock_state.set_state = AsyncMock()

    await process_job_days_button(mock_query, mock_state)
    assert mock_query.message.answer.called
    assert "Enter Custom Duration" in mock_query.message.answer.call_args[0][0]

    # Test typing custom days (e.g. 14 days)
    mock_msg = MagicMock()
    mock_msg.text = "14"
    mock_msg.answer = AsyncMock()

    await process_job_days_text(mock_msg, mock_state)
    assert mock_state.update_data.called
    assert mock_state.update_data.call_args[1]["days"] == 14
    assert mock_state.set_state.called
    assert mock_state.set_state.call_args[0][0] == CreateJobStates.total_amount
    assert "Duration: *14 Day(s)* ✅" in mock_msg.answer.call_args[0][0]

