import logging
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.types import Update

from app.core.config import settings
from app.integrations.telegram.handlers import router as telegram_router

logger = logging.getLogger(__name__)

# Use configured bot token or safe fallback for testing
token = settings.TELEGRAM_BOT_TOKEN.strip() if settings.TELEGRAM_BOT_TOKEN else "123456789:AAG_mock_token_for_tests_proofwork"

bot = Bot(token=token)
dp = Dispatcher()
dp.include_router(telegram_router)


async def process_telegram_webhook_update(raw_update: dict[str, Any]) -> None:
    """Feeds an incoming Telegram update payload into the aiogram Dispatcher."""
    try:
        update = Update.model_validate(raw_update, context={"bot": bot})
        await dp.feed_update(bot=bot, update=update)
    except Exception as e:
        logger.error("Error processing Telegram update: %s", e)
        raise
