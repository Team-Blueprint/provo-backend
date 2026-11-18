from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from app.core.config import settings
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
        [InlineKeyboardButton(text="💼 I'm an Employer", callback_data="role:client")],
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
    """Main dashboard menu for employers."""
    kb = [
        [KeyboardButton(text="➕ Create New Job"), KeyboardButton(text="📋 My Posted Jobs")],
        [KeyboardButton(text="💳 Fund a Job"), KeyboardButton(text="ℹ️ Help")],
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


def worker_main_menu() -> ReplyKeyboardMarkup:
    """Main dashboard menu for artisans/workers."""
    kb = [
        [KeyboardButton(text="💼 My Active Jobs"), KeyboardButton(text="✅ Done Today")],
        [KeyboardButton(text="📊 My Credit Record"), KeyboardButton(text="ℹ️ Help")],
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


def day_review_inline_keyboard(
    job_id: str,
    day_number: int,
    supervisor_token: str | None = None,
) -> InlineKeyboardMarkup:
    """Review card inline buttons for employer and supervisor."""
    row1 = [
        InlineKeyboardButton(
            text="✅ Confirm Day Pay",
            callback_data=f"review_confirm:{job_id}:{day_number}",
        ),
        InlineKeyboardButton(
            text="⚠️ Dispute Work",
            callback_data=f"review_dispute:{job_id}:{day_number}",
        ),
    ]
    rows = [row1]
    rows.append([
        InlineKeyboardButton(
            text="📷 Confirm with Photo",
            callback_data=f"review_photo:{job_id}:{day_number}",
        )
    ])
    if supervisor_token:
        web_base = getattr(settings, "WEB_BASE_URL", "https://useprovo.vercel.app")
        rows.append([
            InlineKeyboardButton(
                text="🌐 Supervisor Web Link",
                url=f"{web_base}/supervisor/verify?job={job_id}&token={supervisor_token}",
            )
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def fund_job_inline_keyboard(
    job_id: str,
    account_number: str | None = None,
    show_assign_worker: bool = False,
    show_assign_supervisor: bool = False,
    supervisor_token: str | None = None,
) -> InlineKeyboardMarkup:
    """Provides a one-click simulate deposit button, assign artisan, and supervisor buttons."""
    buttons = [
        [
            InlineKeyboardButton(
                text="🚀 Simulate Escrow Deposit",
                callback_data=f"sim_fund:{job_id}",
            )
        ]
    ]
    if show_assign_worker:
        buttons.append([
            InlineKeyboardButton(
                text="👷 Assign Artisan Now",
                callback_data=f"assign_artisan:{job_id}",
            )
        ])
    if show_assign_supervisor:
        buttons.append([
            InlineKeyboardButton(
                text="🕵️ Assign Supervisor",
                callback_data=f"assign_supervisor:{job_id}",
            )
        ])
    elif supervisor_token:
        web_base = getattr(settings, "WEB_BASE_URL", "https://useprovo.vercel.app")
        buttons.append([
            InlineKeyboardButton(
                text="🔗 Supervisor Web Link",
                url=f"{web_base}/supervisor/verify?job={job_id}&token={supervisor_token}",
            )
        ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def employer_job_actions_keyboard(
    job_id: str,
    can_assign_worker: bool = False,
    can_assign_supervisor: bool = False,
    supervisor_token: str | None = None,
) -> InlineKeyboardMarkup | None:
    """Combined action keyboard for employer posted jobs."""
    buttons = []
    if can_assign_worker:
        buttons.append([
            InlineKeyboardButton(text="👷 Assign Artisan", callback_data=f"assign_artisan:{job_id}")
        ])
    if can_assign_supervisor:
        buttons.append([
            InlineKeyboardButton(text="🕵️ Assign Supervisor", callback_data=f"assign_supervisor:{job_id}")
        ])
    elif supervisor_token:
        web_base = getattr(settings, "WEB_BASE_URL", "https://useprovo.vercel.app")
        buttons.append([
            InlineKeyboardButton(
                text="🔗 Supervisor Link",
                url=f"{web_base}/supervisor/verify?job={job_id}&token={supervisor_token}",
            )
        ])
    return InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None



def job_trade_picker_keyboard() -> InlineKeyboardMarkup:
    """Trade selection for creating a new job."""
    buttons = [
        InlineKeyboardButton(text=f"🔨 {t.value.capitalize()}", callback_data=f"create_job_trade:{t.value}")
        for t in TradeType
    ]
    grid = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    grid.append([InlineKeyboardButton(text="❌ Cancel", callback_data="create_job_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=grid)


def job_days_picker_keyboard() -> InlineKeyboardMarkup:
    """Quick duration selection buttons."""
    buttons = [
        [
            InlineKeyboardButton(text="1 Day", callback_data="create_job_days:1"),
            InlineKeyboardButton(text="2 Days", callback_data="create_job_days:2"),
        ],
        [
            InlineKeyboardButton(text="3 Days", callback_data="create_job_days:3"),
            InlineKeyboardButton(text="5 Days", callback_data="create_job_days:5"),
        ],
        [
            InlineKeyboardButton(text="✏️ Custom (Enter Days)", callback_data="create_job_days:custom"),
        ],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="create_job_cancel")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def assign_artisan_keyboard(job_id: str) -> InlineKeyboardMarkup:
    """Inline button to assign an artisan to an existing unassigned job."""
    kb = [
        [InlineKeyboardButton(text="👷 Assign Artisan", callback_data=f"assign_artisan:{job_id}")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def assign_artisan_picker_keyboard(artisans: list[dict[str, str]], job_id: str) -> InlineKeyboardMarkup:
    """Lists trade-matched artisans with one-tap selection, plus manual phone option for existing job."""
    buttons = []
    for a in artisans:
        label = f"👷 {a['name']} • {a['area']}"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"assign_artisan_pick:{job_id}:{a['user_id']}")])

    buttons.append([InlineKeyboardButton(text="📱 Enter Phone Number", callback_data=f"assign_artisan_manual:{job_id}")])
    buttons.append([InlineKeyboardButton(text="❌ Cancel", callback_data="create_job_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def artisan_picker_keyboard(artisans: list[dict[str, str]]) -> InlineKeyboardMarkup:
    """Lists trade-matched artisans with one-tap selection, plus phone/skip options."""
    buttons = []
    for a in artisans:
        label = f"👷 {a['name']} • {a['area']}"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"choose_artisan:{a['user_id']}")])

    buttons.append([InlineKeyboardButton(text="📱 Enter Different Phone Number", callback_data="choose_artisan:manual")])
    buttons.append([InlineKeyboardButton(text="⏭️ Skip / Assign Later", callback_data="choose_artisan:skip")])
    buttons.append([InlineKeyboardButton(text="❌ Cancel", callback_data="create_job_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def set_pin_keyboard(phone: str) -> InlineKeyboardMarkup:
    """Inline keyboard to redirect user to web PIN setup page."""
    import urllib.parse
    encoded_phone = urllib.parse.quote(phone)
    web_base = getattr(settings, "WEB_BASE_URL", "https://useprovo.vercel.app")
    kb = [
        [InlineKeyboardButton(
            text="🔐 Set Web PIN",
            url=f"{web_base}/set-pin?phone={encoded_phone}",
        )],
        [InlineKeyboardButton(text="⏭️ Skip for now", callback_data="skip_pin_setup")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)
