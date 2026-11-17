from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from app.core.enums import TradeType


def contact_keyboard() -> ReplyKeyboardMarkup:
    """Requests phone number verification via Telegram contact share."""
    kb = [
        [KeyboardButton(text="📱 Share Phone Number", request_contact=True)],
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True, one_time_keyboard=True)


def role_selection_keyboard() -> InlineKeyboardMarkup:
    """Prompts new user to select whether they are an Artisan or a Client."""
    kb = [
        [InlineKeyboardButton(text="👷 I'm an Artisan / Worker", callback_data="role:worker")],
        [InlineKeyboardButton(text="💼 I'm a Client", callback_data="role:client")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def trade_selection_keyboard() -> InlineKeyboardMarkup:
    """Grid of artisan trades for worker track selection."""
    buttons = [
        InlineKeyboardButton(text=f"🔨 {t.value.capitalize()}", callback_data=f"trade:{t.value}")
        for t in TradeType
    ]
    # Arrange in pairs of 2
    grid = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    return InlineKeyboardMarkup(inline_keyboard=grid)


def client_main_menu() -> ReplyKeyboardMarkup:
    """Main dashboard menu for clients."""
    kb = [
        [KeyboardButton(text="📋 My Posted Jobs"), KeyboardButton(text="💳 Fund a Job")],
        [KeyboardButton(text="ℹ️ Help")],
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


def worker_main_menu() -> ReplyKeyboardMarkup:
    """Main dashboard menu for artisans/workers."""
    kb = [
        [KeyboardButton(text="💼 My Active Jobs"), KeyboardButton(text="✅ Done Today")],
        [KeyboardButton(text="📊 My Credit Record"), KeyboardButton(text="ℹ️ Help")],
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


def day_review_inline_keyboard(job_id: str, day_number: int) -> InlineKeyboardMarkup:
    """Review card inline buttons for client and supervisor."""
    kb = [
        [
            InlineKeyboardButton(
                text="✅ Confirm Day Pay",
                callback_data=f"review_confirm:{job_id}:{day_number}",
            ),
            InlineKeyboardButton(
                text="⚠️ Dispute Work",
                callback_data=f"review_dispute:{job_id}:{day_number}",
            ),
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def fund_job_inline_keyboard(job_id: str, account_number: str | None) -> InlineKeyboardMarkup:
    """Provides a one-click simulate deposit button for testing/demo in Telegram."""
    buttons = [
        [
            InlineKeyboardButton(
                text="🚀 Simulate Escrow Deposit",
                callback_data=f"sim_fund:{job_id}",
            )
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)
