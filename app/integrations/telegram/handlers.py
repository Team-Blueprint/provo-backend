import logging
import uuid
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.core.database import async_session_factory
from app.core.enums import DayStatus, JobStatus
from app.core.utils import kobo_to_naira_str, normalize_phone_number
from app.db.models import ClientProfile, Job, JobDay, User, WorkerProfile
from app.domain.scoring import DayScoreInput, calculate_worker_score
from app.integrations.telegram.keyboards import (
    artisan_picker_keyboard,
    assign_artisan_keyboard,
    assign_artisan_picker_keyboard,
    client_main_menu,
    contact_keyboard,
    day_review_inline_keyboard,
    employer_job_actions_keyboard,
    fund_job_inline_keyboard,
    job_days_picker_keyboard,
    job_trade_picker_keyboard,
    role_selection_keyboard,
    set_pin_keyboard,
    trade_selection_keyboard,
    worker_main_menu,
)
from app.services.job_service import JobService
from app.services.release_service import ReleaseService

logger = logging.getLogger(__name__)
router = Router()
release_service = ReleaseService()
job_service = JobService()


class CreateJobStates(StatesGroup):
    trade = State()
    description = State()
    days = State()
    total_amount = State()
    artisan_selection = State()
    manual_artisan_phone = State()


class AssignWorkerStates(StatesGroup):
    waiting_for_phone = State()


class AssignSupervisorStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_phone = State()


class ReviewPhotoStates(StatesGroup):
    waiting_for_photo = State()


def get_funding_service():
    from app.services.funding_service import FundingService
    return FundingService()


@router.message(CommandStart())
async def handle_start(message: Message) -> None:
    """Welcomes the user and initializes onboarding, supervisor portal, or main dashboard."""
    tg_id = message.from_user.id if message.from_user else 0

    # Check for supervisor deep-link token in /start arg (e.g. /start sup_<token> or /start supervisor_<token>)
    parts = message.text.split(maxsplit=1) if message.text else []
    if len(parts) > 1:
        raw_arg = parts[1].strip()
        candidates = [raw_arg]
        if raw_arg.startswith("supervisor_"):
            candidates.append(raw_arg[11:])
            candidates.append(f"sup_{raw_arg[11:]}")
        elif raw_arg.startswith("sup_"):
            candidates.append(raw_arg[4:])

        async with async_session_factory() as session:
            stmt = (
                select(Job)
                .where(Job.supervisor_token.in_(candidates))
                .options(selectinload(Job.days_schedule), selectinload(Job.worker))
            )
            sup_job = (await session.execute(stmt)).scalar_one_or_none()
            if sup_job:
                worker_name = sup_job.worker.name if sup_job.worker else "Assigned Artisan"
                token_val = sup_job.supervisor_token
                web_link = f"https://proofwork.app/supervisor/verify?job={sup_job.id}&token={token_val}"

                # Render schedule with zero employer PII
                days_summary = []
                pending_review_day = None
                for d in sorted(sup_job.days_schedule, key=lambda x: x.day_number):
                    status_icon = (
                        "✅" if d.status in ("CONFIRMED", "PAID")
                        else ("⚠️" if d.status == "DISPUTED"
                              else ("⏳" if d.status == "DONE_REPORTED" else "⚪"))
                    )
                    days_summary.append(f"Day {d.day_number}: {status_icon} `{d.status}` ({kobo_to_naira_str(d.amount_kobo)})")
                    if d.status == "DONE_REPORTED" and pending_review_day is None:
                        pending_review_day = d

                schedule_text = "\n".join(days_summary)
                portal_text = (
                    f"🕵️ *Supervisor Portal: {sup_job.trade}*\n\n"
                    f"• *Job:* {sup_job.description}\n"
                    f"• *Artisan:* {worker_name}\n"
                    f"• *Duration:* {sup_job.days} Day(s)\n"
                    f"• *Total Escrow:* {kobo_to_naira_str(sup_job.total_kobo)}\n\n"
                    f"*Daily Schedule:*\n{schedule_text}\n\n"
                    f"You have been assigned to verify and confirm daily work milestones on site."
                )

                if pending_review_day:
                    kb = day_review_inline_keyboard(str(sup_job.id), pending_review_day.day_number, supervisor_token=token_val)
                else:
                    kb = InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(text="🌐 Open Supervisor Web View", url=web_link)
                    ]])

                await message.answer(portal_text, parse_mode="Markdown", reply_markup=kb)
                return

    async with async_session_factory() as session:
        stmt = (
            select(User)
            .where(User.telegram_user_id == tg_id)
            .options(selectinload(User.worker_profile), selectinload(User.client_profile))
        )
        user = (await session.execute(stmt)).scalar_one_or_none()

        if user:
            if user.is_worker:
                await message.answer(
                    f"Welcome back, {user.name}! 🔨\nHere is your Artisan dashboard.",
                    reply_markup=worker_main_menu(),
                )
            else:
                await message.answer(
                    f"Welcome back, {user.name}! 💼\nHere is your employer dashboard.",
                    reply_markup=client_main_menu(),
                )
            return

    # User not yet registered on Telegram
    await message.answer(
        "Welcome to *Provo*! 🛡️\n\n"
        "Provo simplifies work payments, ensures workers are paid for completed tasks, "
        "builds verified work records that lenders can trust.\n\n"
        "To get started, please tap the button below to share and verify your phone number:",
        parse_mode="Markdown",
        reply_markup=contact_keyboard(),
    )


@router.message(F.contact)
async def handle_contact_share(message: Message) -> None:
    """Handles phone verification via Telegram's cryptographic contact sharing."""
    if not message.contact or not message.from_user:
        return

    raw_phone = message.contact.phone_number
    clean_phone = normalize_phone_number(raw_phone)
    tg_id = message.from_user.id
    name = message.from_user.full_name or "User"

    async with async_session_factory() as session:
        stmt = (
            select(User)
            .where((User.phone == clean_phone) | (User.telegram_user_id == tg_id))
            .options(selectinload(User.worker_profile), selectinload(User.client_profile))
        )
        user = (await session.execute(stmt)).scalars().first()

        if not user:
            user = User(
                name=name,
                phone=clean_phone,
                telegram_user_id=tg_id,
                is_phone_verified=True,
            )
            session.add(user)
            await session.commit()
            is_existing_onboarded = False
        else:
            user.telegram_user_id = tg_id
            user.is_phone_verified = True
            is_existing_onboarded = bool(user.is_worker or user.is_client)
            await session.commit()

    if is_existing_onboarded:
        if user.is_worker:
            await message.answer(
                f"Account linked successfully, {user.name}! 🔨\n"
                f"Welcome to your Provo Artisan dashboard.",
                reply_markup=worker_main_menu(),
            )
        else:
            await message.answer(
                f"Account linked successfully, {user.name}! 💼\n"
                f"Welcome to your Provo Employer dashboard.",
                reply_markup=client_main_menu(),
            )
        return

    await message.answer(
        f"Phone number verified: *{clean_phone}* ✅\n\n"
        "Are you using Provo as an Artisan (to do work and earn) or as an Employer (to hire artisans)?",
        parse_mode="Markdown",
        reply_markup=role_selection_keyboard(),
    )


@router.callback_query(F.data.startswith("role:"))
async def handle_role_selection(query: CallbackQuery) -> None:
    """Routes user into either the Worker track or Employer track."""
    if not query.message or not query.from_user:
        return

    role = query.data.split(":")[1]
    tg_id = query.from_user.id

    async with async_session_factory() as session:
        stmt = (
            select(User)
            .where(User.telegram_user_id == tg_id)
            .options(selectinload(User.worker_profile), selectinload(User.client_profile))
        )
        user = (await session.execute(stmt)).scalar_one_or_none()
        if not user:
            await query.answer("User profile not found. Send /start to begin.")
            return

        if role == "worker":
            user.is_worker = True
            if not user.worker_profile:
                user.worker_profile = WorkerProfile(user_id=user.id)
            await session.commit()

            await query.message.edit_text(
                "Great! Please select your primary *Artisan Track*:",
                parse_mode="Markdown",
                reply_markup=trade_selection_keyboard(),
            )
        else:
            user.is_client = True
            if not user.client_profile:
                user.client_profile = ClientProfile(user_id=user.id)
            await session.commit()

            await query.message.edit_text(
                "Welcome as a Provo Employer! 💼\n"
                "You can now create escrow-backed jobs for artisans.",
                reply_markup=None,
            )
            await query.message.answer("Dashboard ready:", reply_markup=client_main_menu())

            # Prompt to set web PIN if not yet set
            if not user.pin_hash:
                await query.message.answer(
                    "🔐 *Set Your Web PIN (Optional)*\n\n"
                    "To also access Proofwork on the web, you'll need a 6-digit PIN. "
                    "You won't type it here — tap the button below to set it on a secure page:\n\n"
                    "_(You can skip this and do it later via /setpin)_",
                    parse_mode="Markdown",
                    reply_markup=set_pin_keyboard(user.phone),
                )

    await query.answer()


@router.callback_query(F.data.startswith("trade:"))
async def handle_trade_selection(query: CallbackQuery) -> None:
    """Sets the artisan's trade track."""
    if not query.message or not query.from_user:
        return

    selected_trade = query.data.split(":")[1]
    tg_id = query.from_user.id

    async with async_session_factory() as session:
        stmt = (
            select(User)
            .where(User.telegram_user_id == tg_id)
            .options(selectinload(User.worker_profile))
        )
        user = (await session.execute(stmt)).scalar_one_or_none()
        if user and user.worker_profile:
            user.worker_profile.trade = selected_trade
            await session.commit()

    await query.message.edit_text(
        f"Artisan Track selected: *{selected_trade.capitalize()}* 🔨\n\n"
        "Your profile is active! When employers hire you through Provo, you will see your jobs here.",
        parse_mode="Markdown",
    )
    await query.message.answer("Artisan Dashboard:", reply_markup=worker_main_menu())

    # Prompt to set web PIN if not yet set
    if user and not user.pin_hash:
        await query.message.answer(
            "🔐 *Set Your Web PIN (Optional)*\n\n"
            "To also access Proofwork on the web, you'll need a 6-digit PIN. "
            "You won't type it here — tap the button below to set it on a secure page:\n\n"
            "_(You can skip this and do it later via /setpin)_",
            parse_mode="Markdown",
            reply_markup=set_pin_keyboard(user.phone),
        )

    await query.answer()


@router.callback_query(F.data == "skip_pin_setup")
async def handle_skip_pin_setup(query: CallbackQuery) -> None:
    """Dismisses the PIN setup prompt."""
    if query.message:
        await query.message.edit_reply_markup(reply_markup=None)
    await query.answer("You can set your web PIN anytime via /setpin.")


@router.message(Command("setpin"))
async def handle_setpin_command(message: Message) -> None:
    """Allows users to set/update their web PIN from the bot."""
    tg_id = message.from_user.id if message.from_user else 0
    async with async_session_factory() as session:
        stmt = select(User).where(User.telegram_user_id == tg_id)
        user = (await session.execute(stmt)).scalar_one_or_none()
        if not user:
            await message.answer("Please send /start to register first.")
            return
        status_text = "not yet set" if not user.pin_hash else "already set (tap to update)"
        await message.answer(
            f"🔐 *Web PIN Setup*\n\n"
            f"PIN status: *{status_text}*\n\n"
            "Tap below to set or update your 6-digit PIN on the secure Proofwork web page:\n\n"
            "_(Your PIN is never entered directly in Telegram for maximum security)_",
            parse_mode="Markdown",
            reply_markup=set_pin_keyboard(user.phone),
        )


@router.message(F.text == "💼 My Active Jobs")
async def handle_my_active_jobs(message: Message) -> None:
    """Lists current active escrow jobs for the artisan (worker view)."""
    tg_id = message.from_user.id if message.from_user else 0

    async with async_session_factory() as session:
        user_stmt = select(User).where(User.telegram_user_id == tg_id)
        user = (await session.execute(user_stmt)).scalar_one_or_none()
        if not user:
            await message.answer("Please send /start to register first.")
            return

        job_stmt = (
            select(Job)
            .where(
                Job.worker_id == user.id,
                Job.status.in_([JobStatus.FUNDED.value, JobStatus.IN_PROGRESS.value]),
            )
            .options(selectinload(Job.days_schedule))
        )
        jobs = list((await session.execute(job_stmt)).scalars().all())

        if not jobs:
            await message.answer(
                "You have no active escrow jobs right now.\n"
                "Tell your employer to hire you on Provo or enter your phone number when creating a job!"
            )
            return

        text = "💼 *Your Active Escrow Jobs:*\n\n"
        for job in jobs:
            text += (
                f"• *{job.trade}*: {job.description}\n"
                f"  Total Locked: {kobo_to_naira_str(job.total_kobo)} ({job.days} day(s))\n"
                f"  Status: `{job.status}`\n\n"
            )

        await message.answer(text, parse_mode="Markdown")


@router.message(F.text == "📋 My Posted Jobs")
async def handle_client_posted_jobs(message: Message) -> None:
    """Lists jobs the client has posted with escrow funding status."""
    tg_id = message.from_user.id if message.from_user else 0

    async with async_session_factory() as session:
        user_stmt = select(User).where(User.telegram_user_id == tg_id)
        user = (await session.execute(user_stmt)).scalar_one_or_none()
        if not user:
            await message.answer("Please send /start to register first.")
            return

        job_stmt = (
            select(Job)
            .where(Job.client_id == user.id)
            .options(selectinload(Job.days_schedule), selectinload(Job.worker))
            .order_by(Job.created_at.desc())
        )
        jobs = list((await session.execute(job_stmt)).scalars().all())

        if not jobs:
            await message.answer(
                "You have not posted any jobs yet.\n"
                "Create a job on the Provo app to get started!"
            )
            return

        await message.answer("📋 *Your Posted Jobs:*", parse_mode="Markdown")
        for job in jobs:
            worker_name = job.worker.name if job.worker else "Not assigned"
            supervisor_name = job.supervisor_name if job.supervisor_name else "None assigned"
            job_text = (
                f"• *{job.trade}*: {job.description}\n"
                f"  Total: {kobo_to_naira_str(job.total_kobo)} | Status: `{job.status}`\n"
                f"  Artisan: {worker_name}\n"
                f"  Supervisor: {supervisor_name}\n"
                f"  Account: `{job.paga_account_number or 'pending'}`"
            )
            can_assign_worker = (
                job.worker_id is None
                and job.status in (JobStatus.DRAFT.value, JobStatus.AWAITING_FUNDING.value, JobStatus.FUNDED.value)
            )
            can_assign_supervisor = (
                job.supervisor_name is None
                and job.status in (JobStatus.DRAFT.value, JobStatus.AWAITING_FUNDING.value, JobStatus.FUNDED.value, JobStatus.IN_PROGRESS.value)
            )
            actions_kb = employer_job_actions_keyboard(
                str(job.id),
                can_assign_worker=can_assign_worker,
                can_assign_supervisor=can_assign_supervisor,
                supervisor_token=job.supervisor_token,
            )
            await message.answer(
                job_text,
                parse_mode="Markdown",
                reply_markup=actions_kb,
            )


@router.callback_query(F.data.startswith("assign_artisan:"))
async def handle_assign_artisan_start(query: CallbackQuery, state: FSMContext) -> None:
    """Starts the assign artisan flow for a posted job: offers directory artisans or manual phone entry."""
    if not query.message or not query.from_user:
        return
    job_id_str = query.data.split(":")[1]
    job_id = uuid.UUID(job_id_str)
    tg_id = query.from_user.id

    async with async_session_factory() as session:
        job_stmt = select(Job).where(Job.id == job_id)
        job = (await session.execute(job_stmt)).scalar_one_or_none()
        if not job:
            await query.answer("Job not found.", show_alert=True)
            return

        trade = job.trade
        # Query matching available artisans in directory
        stmt = (
            select(WorkerProfile)
            .where(WorkerProfile.trade == trade, WorkerProfile.is_available.is_(True))
            .options(selectinload(WorkerProfile.user))
            .limit(6)
        )
        profiles = list((await session.execute(stmt)).scalars().all())

        artisan_list = []
        for prof in profiles:
            u = prof.user
            if not u:
                continue
            count_stmt = (
                select(func.count(JobDay.id))
                .join(Job, JobDay.job_id == Job.id)
                .where(Job.worker_id == u.id, JobDay.status == DayStatus.PAID.value)
            )
            v_days = (await session.execute(count_stmt)).scalar() or 0
            badge = "⭐ Gold" if v_days >= 30 else ("⭐ Silver" if v_days >= 10 else ("⭐ Bronze" if v_days >= 1 else "Unverified"))
            artisan_list.append({
                "user_id": str(u.id),
                "name": u.name,
                "area": prof.area,
                "badge": badge,
            })

    if artisan_list:
        await query.message.answer(
            f"👷 *Assign an Artisan for {trade}*\n\n"
            f"Found {len(artisan_list)} registered artisan(s) in this trade.\n"
            "Tap to assign directly, or enter their phone number:",
            parse_mode="Markdown",
            reply_markup=assign_artisan_picker_keyboard(artisan_list, job_id_str),
        )
    else:
        await state.set_state(AssignWorkerStates.waiting_for_phone)
        await state.update_data(assign_job_id=job_id_str)
        await query.message.answer(
            f"👷 *Assign an Artisan for {trade}*\n\n"
            "Please enter the artisan's phone number (e.g. `08012345678`):",
            parse_mode="Markdown",
        )
    await query.answer()


@router.callback_query(F.data.startswith("assign_artisan_pick:"))
async def handle_assign_artisan_pick(query: CallbackQuery) -> None:
    """Assigns a directory artisan directly to an unassigned job."""
    if not query.message or not query.from_user:
        return
    parts = query.data.split(":")
    job_id = uuid.UUID(parts[1])
    worker_id = uuid.UUID(parts[2])
    tg_id = query.from_user.id

    async with async_session_factory() as session:
        user_stmt = select(User).where(User.telegram_user_id == tg_id)
        actor = (await session.execute(user_stmt)).scalar_one_or_none()
        if not actor:
            await query.answer("You are not registered.", show_alert=True)
            return

        try:
            job = await job_service.assign_worker_to_job(
                db=session,
                job_id=job_id,
                actor_id=actor.id,
                worker_id=worker_id,
            )
            await session.commit()
            worker_name = job.worker.name if job.worker else "Assigned Artisan"
            await query.message.edit_text(
                f"✅ *Artisan Assigned Successfully!*\n\n"
                f"• *Job:* {job.description}\n"
                f"• *Artisan:* {worker_name}\n\n"
                f"They will receive daily release notifications once work begins.",
                parse_mode="Markdown",
            )
        except ValueError as e:
            await query.answer(f"Failed: {e}", show_alert=True)

    await query.answer()


@router.callback_query(F.data.startswith("assign_artisan_manual:"))
async def handle_assign_artisan_manual_button(query: CallbackQuery, state: FSMContext) -> None:
    """Switches to manual phone input mode from directory picker."""
    if not query.message:
        return
    job_id_str = query.data.split(":")[1]
    await state.set_state(AssignWorkerStates.waiting_for_phone)
    await state.update_data(assign_job_id=job_id_str)
    await query.message.answer(
        "📱 *Enter Artisan Phone Number*\n\n"
        "Please type the artisan's phone number (e.g. `08012345678` or `+2348012345678`):",
        parse_mode="Markdown",
    )
    await query.answer()


@router.message(AssignWorkerStates.waiting_for_phone)
async def handle_assign_artisan_phone(message: Message, state: FSMContext) -> None:
    """Handles phone number entry for assigning an artisan to an existing job."""
    raw_phone = message.text.strip() if message.text else ""
    tg_id = message.from_user.id if message.from_user else 0
    data = await state.get_data()
    job_id_str = data.get("assign_job_id", "")
    await state.clear()

    if not job_id_str:
        await message.answer("Something went wrong. Please try again from My Posted Jobs.")
        return

    async with async_session_factory() as session:
        user_stmt = select(User).where(User.telegram_user_id == tg_id)
        actor = (await session.execute(user_stmt)).scalar_one_or_none()
        if not actor:
            await message.answer("Please send /start to register.")
            return
        try:
            job = await job_service.assign_worker_to_job(
                db=session,
                job_id=uuid.UUID(job_id_str),
                actor_id=actor.id,
                worker_phone=raw_phone,
            )
            await session.commit()
            worker_name = job.worker.name if job.worker else raw_phone
            await message.answer(
                f"✅ *Artisan assigned successfully!*\n\n"
                f"• *Artisan:* {worker_name}\n"
                f"• *Phone:* `{raw_phone}`\n\n"
                f"They will be notified when the job is funded.",
                parse_mode="Markdown",
            )
        except ValueError as e:
            await message.answer(f"❌ Error: {e}")


@router.callback_query(F.data.startswith("assign_supervisor:"))
async def handle_assign_supervisor_start(query: CallbackQuery, state: FSMContext) -> None:
    """Initiates assigning a site supervisor to a job."""
    if not query.message or not query.from_user:
        return
    job_id_str = query.data.split(":")[1]
    await state.set_state(AssignSupervisorStates.waiting_for_name)
    await state.update_data(supervisor_job_id=job_id_str)
    await query.message.answer(
        "🕵️ *Assign a Site Supervisor*\n\n"
        "Enter the supervisor's *Full Name* (e.g. `Engr. Babatunde`):",
        parse_mode="Markdown",
    )
    await query.answer()


@router.message(AssignSupervisorStates.waiting_for_name)
async def handle_assign_supervisor_name(message: Message, state: FSMContext) -> None:
    """Stores supervisor name and prompts for phone number."""
    name = message.text.strip() if message.text else ""
    if not name or len(name) < 2:
        await message.answer("Please enter a valid supervisor name (at least 2 characters):")
        return
    await state.update_data(supervisor_name=name)
    await state.set_state(AssignSupervisorStates.waiting_for_phone)
    await message.answer(
        f"Great! Now enter *{name}*'s Phone Number (e.g. `08012345678` or `+2348012345678`):",
        parse_mode="Markdown",
    )


@router.message(AssignSupervisorStates.waiting_for_phone)
async def handle_assign_supervisor_phone(message: Message, state: FSMContext) -> None:
    """Validates phone number and assigns supervisor via job_service."""
    raw_phone = message.text.strip() if message.text else ""
    tg_id = message.from_user.id if message.from_user else 0
    data = await state.get_data()
    job_id_str = data.get("supervisor_job_id")
    sup_name = data.get("supervisor_name")
    await state.clear()

    if not job_id_str or not sup_name:
        await message.answer("Something went wrong. Please try again from My Posted Jobs.")
        return

    async with async_session_factory() as session:
        user_stmt = select(User).where(User.telegram_user_id == tg_id)
        actor = (await session.execute(user_stmt)).scalar_one_or_none()
        if not actor:
            await message.answer("Please send /start to register.")
            return

        try:
            job = await job_service.assign_supervisor(
                db=session,
                job_id=uuid.UUID(job_id_str),
                actor_id=actor.id,
                supervisor_name=sup_name,
                supervisor_phone=raw_phone,
            )
            await session.commit()

            clean_phone = job.supervisor_phone
            sup_link = f"https://proofwork.app/supervisor/verify?job={job.id}&token={job.supervisor_token}"
            bot_link = f"https://t.me/provo_bot?start={job.supervisor_token}"

            await message.answer(
                f"✅ *Supervisor Assigned Successfully!*\n\n"
                f"• *Name:* {job.supervisor_name}\n"
                f"• *Phone:* `{clean_phone}`\n\n"
                f"🔗 *Supervisor Verification Link:*\n{sup_link}\n\n"
                f"🤖 *Telegram Bot Direct Link:*\n{bot_link}\n\n"
                f"_Share either link with {job.supervisor_name}. They can inspect and confirm work daily without needing a password!_",
                parse_mode="Markdown",
            )
        except ValueError as e:
            await message.answer(f"❌ Failed to assign supervisor: {e}")


@router.message(F.text == "💳 Fund a Job")
async def handle_fund_a_job(message: Message) -> None:
    """Lists client jobs awaiting funding with payment instructions and details."""
    tg_id = message.from_user.id if message.from_user else 0

    async with async_session_factory() as session:
        user_stmt = select(User).where(User.telegram_user_id == tg_id)
        user = (await session.execute(user_stmt)).scalar_one_or_none()
        if not user:
            await message.answer("Please send /start to register first.")
            return

        job_stmt = (
            select(Job)
            .where(
                Job.client_id == user.id,
                Job.status.in_([JobStatus.DRAFT.value, JobStatus.AWAITING_FUNDING.value, "PARTIALLY_FUNDED"]),
            )
            .options(selectinload(Job.worker), selectinload(Job.client))
            .order_by(Job.created_at.desc())
        )
        jobs = list((await session.execute(job_stmt)).scalars().all())

        if not jobs:
            await message.answer(
                "You have no jobs awaiting funding right now.\n\n"
                "All your posted jobs are either already funded or completed! 🛡️"
            )
            return

        for job in jobs:
            # If still DRAFT without virtual account, initialize escrow request
            if not job.paga_account_number and job.status == JobStatus.DRAFT.value:
                try:
                    funding_service = get_funding_service()
                    job = await funding_service.initiate_job_escrow(session, job.id)
                    await session.commit()
                except Exception as e:
                    logger.warning("Could not initiate escrow for job %s: %s", job.id, e)

            amount_naira = kobo_to_naira_str(job.total_kobo)
            worker_name = job.worker.name if job.worker else "Assigned Artisan"
            supervisor_name = job.supervisor_name if job.supervisor_name else "None assigned"
            account_num = job.paga_account_number or "Processing..."

            msg_text = (
                f"💳 *Escrow Payment Details for {job.trade}*\n\n"
                f"• *Job:* {job.description}\n"
                f"• *Artisan:* {worker_name}\n"
                f"• *Supervisor:* {supervisor_name}\n"
                f"• *Total Amount:* *{amount_naira}*\n"
                f"• *Status:* `{job.status}`\n\n"
                f"🏦 *Bank Transfer Instructions:*\n"
                f"• *Bank Name:* `Paga`\n"
                f"• *Account Number:* `{account_num}`\n\n"
                f"💡 _Transfer the exact amount to the account above. Or tap below to simulate deposit immediately!_"
            )
            await message.answer(
                msg_text,
                parse_mode="Markdown",
                reply_markup=fund_job_inline_keyboard(
                    str(job.id),
                    job.paga_account_number,
                    show_assign_worker=(job.worker_id is None),
                    show_assign_supervisor=(job.supervisor_name is None),
                    supervisor_token=job.supervisor_token,
                ),
            )


@router.message(F.text == "ℹ️ Help")
async def handle_help_menu(message: Message) -> None:
    """Provides general help and guide for both employers and artisans."""
    help_text = (
        "🛡️ *Welcome to Provo Help & Support*\n\n"
        "Provo is a daily milestone escrow platform designed for informal artisans and employers.\n\n"
        "• *For Employers:*\n"
        "  1. Tap *📋 My Posted Jobs* to see your jobs and status.\n"
        "  2. Tap *💳 Fund a Job* to get bank transfer details to lock funds in escrow.\n"
        "  3. Review and confirm daily work notifications to release artisan pay.\n\n"
        "• *For Artisans:*\n"
        "  1. Tap *💼 My Active Jobs* to view funded jobs.\n"
        "  2. Tap *✅ Done Today* to submit milestone completion.\n"
        "  3. Tap *📊 My Credit Record* to see your verified credit readiness rating.\n\n"
        "Need additional assistance? Contact support@provo.app"
    )
    await message.answer(help_text, parse_mode="Markdown")


@router.message(F.text == "✅ Done Today")
async def handle_done_today_menu(message: Message) -> None:
    """Allows the artisan to submit daily completion for their active job."""
    tg_id = message.from_user.id if message.from_user else 0

    async with async_session_factory() as session:
        user_stmt = select(User).where(User.telegram_user_id == tg_id)
        user = (await session.execute(user_stmt)).scalar_one_or_none()
        if not user:
            await message.answer("Please send /start to register.")
            return

        job_stmt = (
            select(Job)
            .where(
                Job.worker_id == user.id,
                Job.status.in_([JobStatus.FUNDED.value, JobStatus.IN_PROGRESS.value]),
            )
            .options(selectinload(Job.days_schedule))
        )
        jobs = list((await session.execute(job_stmt)).scalars().all())

        if not jobs:
            await message.answer("No active jobs available to report work for.")
            return

        for job in jobs:
            pending_day = next((d for d in job.days_schedule if d.status == DayStatus.PENDING.value), None)
            if pending_day:
                try:
                    await release_service.report_day_done(
                        db=session,
                        job_id=job.id,
                        day_number=pending_day.day_number,
                        worker_user_id=user.id,
                    )
                    await session.commit()

                    # Notify employer if on Telegram
                    stmt_client = select(User).where(User.id == job.client_id)
                    client_user = (await session.execute(stmt_client)).scalar_one_or_none()
                    if client_user and client_user.telegram_user_id:
                        try:
                            from app.integrations.telegram.bot import bot
                            await bot.send_message(
                                chat_id=client_user.telegram_user_id,
                                text=(
                                    f"🔔 *Milestone Done: Day {pending_day.day_number}*\n\n"
                                    f"• *Job:* {job.description}\n"
                                    f"• *Artisan:* {user.name}\n"
                                    f"• *Amount:* {kobo_to_naira_str(pending_day.amount_kobo)}\n\n"
                                    f"Please review and confirm to release funds, or dispute if incomplete."
                                ),
                                parse_mode="Markdown",
                                reply_markup=day_review_inline_keyboard(str(job.id), pending_day.day_number, supervisor_token=job.supervisor_token),
                            )
                        except Exception as e:
                            logger.warning("Could not send Telegram review alert to employer: %s", e)

                    # Also notify supervisor if registered on Telegram
                    if job.supervisor_phone:
                        stmt_sup = select(User).where(User.phone == job.supervisor_phone)
                        sup_user = (await session.execute(stmt_sup)).scalar_one_or_none()
                        if sup_user and sup_user.telegram_user_id:
                            try:
                                from app.integrations.telegram.bot import bot
                                await bot.send_message(
                                    chat_id=sup_user.telegram_user_id,
                                    text=(
                                        f"🕵️ *Supervisor Alert: Day {pending_day.day_number} Completed*\n\n"
                                        f"• *Job:* {job.description}\n"
                                        f"• *Artisan:* {user.name}\n"
                                        f"• *Amount:* {kobo_to_naira_str(pending_day.amount_kobo)}\n\n"
                                        f"Please verify site progress and confirm or dispute."
                                    ),
                                    parse_mode="Markdown",
                                    reply_markup=day_review_inline_keyboard(str(job.id), pending_day.day_number, supervisor_token=job.supervisor_token),
                                )
                            except Exception as e:
                                logger.warning("Could not send Telegram review alert to supervisor: %s", e)

                    await message.answer(
                        f"✅ *Day {pending_day.day_number} reported done!* ({kobo_to_naira_str(pending_day.amount_kobo)})\n\n"
                        "Your employer and supervisor have been notified to review and confirm. "
                        "The 24-hour countdown has started.",
                        parse_mode="Markdown",
                    )
                    return
                except ValueError as e:
                    await message.answer(f"Error: {e}")
                    return

        await message.answer("All days for your current jobs have already been reported or settled.")


@router.message(F.text == "📊 My Credit Record")
async def handle_my_record(message: Message) -> None:
    """Displays artisan's verified credit readiness summary."""
    tg_id = message.from_user.id if message.from_user else 0

    async with async_session_factory() as session:
        user_stmt = select(User).where(User.telegram_user_id == tg_id)
        user = (await session.execute(user_stmt)).scalar_one_or_none()
        if not user:
            await message.answer("Please send /start to register.")
            return

        day_stmt = (
            select(JobDay)
            .join(Job, JobDay.job_id == Job.id)
            .where(Job.worker_id == user.id)
            .options(selectinload(JobDay.job))
        )
        days = list((await session.execute(day_stmt)).scalars().all())

        history = [
            DayScoreInput(
                job_id=str(d.job_id),
                client_id=str(d.job.client_id),
                amount_kobo=d.amount_kobo,
                status=DayStatus(d.status),
                date=d.done_at or d.created_at,
            )
            for d in days
        ]

        score_res = calculate_worker_score(history)

        reply_text = (
            f"📊 *Provo Verified Record for {user.name}:*\n\n"
            f"• *Credit Score:* {score_res.score} / 100\n"
            f"• *Rating Tier:* `{score_res.band}`\n"
            f"• *Confirmed Days:* {score_res.confirmed_days}\n"
            f"• *Auto-Released Days:* {score_res.auto_released_days}\n"
            f"• *Unique Employers:* {score_res.unique_payers}\n"
            f"• *Repeat Employers:* {score_res.repeat_payers}\n"
            f"• *Active Earning Months:* {score_res.active_months}\n\n"
            "_Shareable with any lender for tool and equipment financing!_"
        )
        await message.answer(reply_text, parse_mode="Markdown")


@router.callback_query(F.data.startswith("review_confirm:"))
async def handle_inline_confirm(query: CallbackQuery) -> None:
    """Client or supervisor clicks 'Confirm Day Pay'."""
    if not query.message or not query.from_user:
        return

    parts = query.data.split(":")
    job_id = uuid.UUID(parts[1])
    day_number = int(parts[2])
    tg_id = query.from_user.id

    async with async_session_factory() as session:
        user_stmt = select(User).where(User.telegram_user_id == tg_id)
        actor = (await session.execute(user_stmt)).scalar_one_or_none()
        if not actor:
            await query.answer("You are not registered.", show_alert=True)
            return

        try:
            day, _payout = await release_service.confirm_day(
                db=session,
                job_id=job_id,
                day_number=day_number,
                actor_user_id=actor.id,
            )
            await session.commit()
            await query.message.edit_text(
                f"✅ *Day {day_number} Confirmed & Paid!*\n"
                f"Amount of {kobo_to_naira_str(day.amount_kobo)} released to artisan's Paga account.",
                parse_mode="Markdown",
            )
        except ValueError as e:
            await query.answer(f"Failed: {e}", show_alert=True)

    await query.answer()


@router.callback_query(F.data.startswith("review_dispute:"))
async def handle_inline_dispute(query: CallbackQuery) -> None:
    """Client or supervisor clicks 'Dispute Work'."""
    if not query.message or not query.from_user:
        return

    parts = query.data.split(":")
    job_id = uuid.UUID(parts[1])
    day_number = int(parts[2])
    tg_id = query.from_user.id

    async with async_session_factory() as session:
        user_stmt = select(User).where(User.telegram_user_id == tg_id)
        actor = (await session.execute(user_stmt)).scalar_one_or_none()
        if not actor:
            await query.answer("You are not registered.", show_alert=True)
            return

        try:
            _day, _disp = await release_service.dispute_day(
                db=session,
                job_id=job_id,
                day_number=day_number,
                actor_user_id=actor.id,
                reason="Disputed by reviewer on Telegram",
            )
            await session.commit()
            await query.message.edit_text(
                f"⚠️ *Day {day_number} Disputed!*\n"
                "The 24-hour release countdown has been frozen. An admin will review.",
                parse_mode="Markdown",
            )
        except ValueError as e:
            await query.answer(f"Failed: {e}", show_alert=True)

    await query.answer()


@router.callback_query(F.data.startswith("review_photo:"))
async def handle_inline_review_photo_start(query: CallbackQuery, state: FSMContext) -> None:
    """Prompts reviewer to take or upload a site photo to confirm milestone."""
    if not query.message or not query.from_user:
        return
    parts = query.data.split(":")
    job_id = parts[1]
    day_number = int(parts[2])
    await state.set_state(ReviewPhotoStates.waiting_for_photo)
    await state.update_data(photo_job_id=job_id, photo_day_number=day_number)
    await query.message.answer(
        f"📷 *Upload Proof Photo for Day {day_number}*\n\n"
        "Please take or upload a photo showing the completed work on site:",
        parse_mode="Markdown",
    )
    await query.answer()


@router.message(ReviewPhotoStates.waiting_for_photo, F.photo)
async def handle_inline_review_photo_received(message: Message, state: FSMContext) -> None:
    """Receives site photo, confirms day, and attaches proof photo."""
    tg_id = message.from_user.id if message.from_user else 0
    data = await state.get_data()
    job_id_str = data.get("photo_job_id")
    day_number = data.get("photo_day_number")
    await state.clear()

    if not job_id_str or day_number is None:
        await message.answer("Session expired. Please try confirming again from the review card.")
        return

    photo = message.photo[-1]
    proof_photo_ref = f"tg_file:{photo.file_id}"

    async with async_session_factory() as session:
        user_stmt = select(User).where(User.telegram_user_id == tg_id)
        actor = (await session.execute(user_stmt)).scalar_one_or_none()

        job_stmt = select(Job).where(Job.id == uuid.UUID(job_id_str))
        job = (await session.execute(job_stmt)).scalar_one_or_none()

        if not job:
            await message.answer("Job not found.")
            return

        actor_id = actor.id if actor else job.client_id
        try:
            day, _payout = await release_service.confirm_day(
                db=session,
                job_id=uuid.UUID(job_id_str),
                day_number=day_number,
                actor_user_id=actor_id,
                proof_photo=proof_photo_ref,
            )
            await session.commit()
            await message.answer(
                f"✅ *Day {day_number} Confirmed with Proof Photo!* 📷\n\n"
                f"Amount of {kobo_to_naira_str(day.amount_kobo)} released to artisan.\n"
                f"Proof photo saved to the permanent milestone credit record.",
                parse_mode="Markdown",
            )
        except ValueError as e:
            await message.answer(f"❌ Failed to confirm day: {e}")


@router.callback_query(F.data.startswith("sim_fund:"))
async def handle_inline_simulate_funding(query: CallbackQuery) -> None:
    """Client clicks '🚀 Simulate Escrow Deposit' in Telegram."""
    if not query.message or not query.from_user:
        return

    job_id_str = query.data.split(":")[1]
    job_id = uuid.UUID(job_id_str)

    async with async_session_factory() as session:
        stmt = (
            select(Job)
            .where(Job.id == job_id)
            .options(selectinload(Job.worker), selectinload(Job.client), selectinload(Job.days_schedule))
        )
        job = (await session.execute(stmt)).scalar_one_or_none()
        if not job:
            await query.answer("Job not found.", show_alert=True)
            return

        funding_service = get_funding_service()
        amount_naira_float = float(job.total_kobo / 100.0)
        simulated_tx_id = f"SIM-TG-{uuid.uuid4().hex[:10].upper()}"

        payload = {
            "transactionId": simulated_tx_id,
            "referenceNumber": job.funding_reference,
            "accountNumber": job.paga_account_number or "3360702838",
            "amount": amount_naira_float,
            "currency": "NGN",
            "payerName": query.from_user.full_name or "Telegram Client",
            "payerPhoneNumber": job.client.phone if job.client else "+2348000000000",
            "payerBank": "Access Bank",
            "status": "SUCCESSFUL",
            "channel": "BANK_TRANSFER",
        }

        try:
            updated_job, _payment_in = await funding_service.process_paga_webhook(
                db=session,
                payload=payload,
                raw_body=b"",
                signature=None,
            )
            await session.commit()
            await query.message.edit_text(
                f"🎉 *Escrow Successfully Funded!*\n\n"
                f"• *Job:* {updated_job.description}\n"
                f"• *Amount:* ₦{amount_naira_float:,.2f}\n"
                f"• *Status:* `{updated_job.status}`\n\n"
                f"✅ Funds are now locked in escrow. The artisan has been notified to begin Day 1 work!",
                parse_mode="Markdown",
            )
            await query.answer("Escrow payment simulated successfully! 🎉", show_alert=True)
        except Exception as e:
            logger.error("Simulation from Telegram failed: %s", e)
            await query.answer(f"Simulation failed: {e}", show_alert=True)



# --- Create Job FSM Wizard ---

@router.callback_query(F.data == "create_job_cancel")
async def cancel_job_creation(query: CallbackQuery, state: FSMContext) -> None:
    """Cancels active job creation and resets state."""
    await state.clear()
    if query.message:
        await query.message.edit_text("❌ Job creation cancelled.")
    await query.answer("Cancelled")


@router.message(F.text == "➕ Create New Job")
async def start_job_creation(message: Message, state: FSMContext) -> None:
    """Step 1: Begins job creation by prompting client to pick a trade."""
    tg_id = message.from_user.id if message.from_user else 0

    async with async_session_factory() as session:
        user_stmt = select(User).where(User.telegram_user_id == tg_id)
        user = (await session.execute(user_stmt)).scalar_one_or_none()
        if not user or not user.is_client:
            await message.answer("Please register as an Employer to create jobs.")
            return

    await state.clear()
    await state.set_state(CreateJobStates.trade)
    await message.answer(
        "🛠 *Create a New Escrow Job*\n\n"
        "Step 1 of 5: Select the trade or skill required:",
        parse_mode="Markdown",
        reply_markup=job_trade_picker_keyboard(),
    )


@router.callback_query(CreateJobStates.trade, F.data.startswith("create_job_trade:"))
async def process_job_trade(query: CallbackQuery, state: FSMContext) -> None:
    """Step 2: Saves trade and prompts for job description."""
    trade = query.data.split(":")[1]
    await state.update_data(trade=trade)
    await state.set_state(CreateJobStates.description)

    if query.message:
        await query.message.edit_text(
            f"Selected Trade: *{trade}* ✅\n\n"
            "Step 2 of 5: Briefly describe the work to be done\n"
            "_(e.g. 'Fix leaking kitchen sink drainage and replace pipe')_:",
            parse_mode="Markdown",
        )
    await query.answer()


@router.message(CreateJobStates.description)
async def process_job_description(message: Message, state: FSMContext) -> None:
    """Step 3: Saves description and prompts for duration (number of days)."""
    desc = message.text.strip() if message.text else ""
    if len(desc) < 3:
        await message.answer("Please provide a clearer description (at least 3 characters).")
        return

    await state.update_data(description=desc)
    await state.set_state(CreateJobStates.days)

    await message.answer(
        "Step 3 of 5: How many days will this job take?\n"
        "_(Select quick buttons below or type a number)_:",
        reply_markup=job_days_picker_keyboard(),
    )


@router.callback_query(CreateJobStates.days, F.data.startswith("create_job_days:"))
async def process_job_days_button(query: CallbackQuery, state: FSMContext) -> None:
    """Handles quick duration button press."""
    days = int(query.data.split(":")[1])
    await state.update_data(days=days)
    await state.set_state(CreateJobStates.total_amount)

    if query.message:
        await query.message.edit_text(
            f"Duration: *{days} Day(s)* ✅\n\n"
            "Step 4 of 5: Enter the *total pay in Naira* for the entire job\n"
            "_(e.g. 15000 or 50000)_:",
            parse_mode="Markdown",
        )
    await query.answer()


@router.message(CreateJobStates.days)
async def process_job_days_text(message: Message, state: FSMContext) -> None:
    """Handles manual number of days input."""
    text = message.text.strip() if message.text else ""
    if not text.isdigit() or int(text) < 1 or int(text) > 365:
        await message.answer("Please enter a valid number of days between 1 and 365.")
        return

    days = int(text)
    await state.update_data(days=days)
    await state.set_state(CreateJobStates.total_amount)

    await message.answer(
        f"Duration: *{days} Day(s)* ✅\n\n"
        "Step 4 of 5: Enter the *total pay in Naira* for the entire job\n"
        "_(e.g. 15000 or 50000)_:",
        parse_mode="Markdown",
    )


@router.message(CreateJobStates.total_amount)
async def process_job_total_amount(message: Message, state: FSMContext) -> None:
    """Step 5: Validates total amount and fetches trade-matched artisans from directory."""
    raw = message.text.strip().replace(",", "") if message.text else ""
    try:
        amount_naira = float(raw)
        if amount_naira < 100:
            raise ValueError()
    except ValueError:
        await message.answer("Please enter a valid amount in Naira (minimum ₦100, e.g. 15000).")
        return

    total_kobo = round(amount_naira * 100)
    await state.update_data(total_kobo=total_kobo, total_naira=amount_naira)
    await state.set_state(CreateJobStates.artisan_selection)

    data = await state.get_data()
    trade = data.get("trade", "OTHER")

    # Query artisans in directory whose trade matches
    async with async_session_factory() as session:
        stmt = (
            select(WorkerProfile)
            .where(WorkerProfile.trade == trade, WorkerProfile.is_available.is_(True))
            .options(selectinload(WorkerProfile.user))
            .limit(6)
        )
        profiles = list((await session.execute(stmt)).scalars().all())

        artisan_list = []
        for prof in profiles:
            u = prof.user
            if not u:
                continue

            # Count paid days
            count_stmt = (
                select(func.count(JobDay.id))
                .join(Job, JobDay.job_id == Job.id)
                .where(Job.worker_id == u.id, JobDay.status == DayStatus.PAID.value)
            )
            v_days = (await session.execute(count_stmt)).scalar() or 0
            badge = "⭐ Gold" if v_days >= 30 else ("⭐ Silver" if v_days >= 10 else ("⭐ Bronze" if v_days >= 1 else "Unverified"))

            artisan_list.append({
                "user_id": str(u.id),
                "name": u.name,
                "area": prof.area,
                "badge": badge,
            })

    if artisan_list:
        text = (
            f"Step 5 of 5: *Choose an Artisan for {trade}*\n\n"
            f"Found {len(artisan_list)} registered artisan(s) in this trade.\n"
            "Tap to assign directly, or enter a custom phone number:"
        )
    else:
        text = (
            f"Step 5 of 5: *Assign Artisan for {trade}*\n\n"
            f"No artisans currently listed under {trade} in the public directory.\n"
            "You can enter their phone number or skip for now:"
        )

    await message.answer(
        text,
        parse_mode="Markdown",
        reply_markup=artisan_picker_keyboard(artisan_list),
    )


async def _finalize_job_creation(
    client_tg_id: int,
    worker_id: uuid.UUID | None,
    state_data: dict[str, Any],
) -> tuple[Job, str]:
    """Helper to commit new job, initiate Paga escrow, and build response card."""
    trade = state_data["trade"]
    desc = state_data["description"]
    days = state_data["days"]
    total_kobo = state_data["total_kobo"]

    async with async_session_factory() as session:
        user_stmt = select(User).where(User.telegram_user_id == client_tg_id)
        client = (await session.execute(user_stmt)).scalar_one()

        job = await job_service.create_job(
            db=session,
            client_id=client.id,
            trade=trade,
            description=desc,
            total_kobo=total_kobo,
            days=days,
            worker_id=worker_id,
        )
        await session.commit()

        # Generate Paga virtual escrow account
        funding_service = get_funding_service()
        try:
            job = await funding_service.initiate_job_escrow(session, job.id)
            await session.commit()
        except Exception as e:
            logger.warning("Could not auto-request escrow on creation: %s", e)

        # Reload job with worker
        stmt = (
            select(Job)
            .where(Job.id == job.id)
            .options(selectinload(Job.worker), selectinload(Job.days_schedule))
        )
        job = (await session.execute(stmt)).scalar_one()

        worker_display = f"{job.worker.name} ({job.worker.phone})" if job.worker else "Not yet assigned (Assign later)"
        supervisor_display = job.supervisor_name or "None assigned (Assign later)"
        amount_naira = kobo_to_naira_str(job.total_kobo)
        account_num = job.paga_account_number or "Pending..."

        summary_text = (
            f"🎉 *Escrow Job Created Successfully!*\n\n"
            f"• *Trade:* {job.trade}\n"
            f"• *Description:* {job.description}\n"
            f"• *Duration:* {job.days} Day(s)\n"
            f"• *Total Escrow:* *{amount_naira}*\n"
            f"• *Artisan:* {worker_display}\n"
            f"• *Supervisor:* {supervisor_display}\n"
            f"• *Status:* `{job.status}`\n\n"
            f"🏦 *Bank Transfer Escrow Details:*\n"
            f"• *Bank:* `Paga`\n"
            f"• *Account Number:* `{account_num}`\n\n"
            f"💡 _Transfer to the account above or tap 'Simulate Escrow Deposit' below to lock funds instantly!_"
        )
        return job, summary_text


@router.callback_query(CreateJobStates.artisan_selection, F.data.startswith("choose_artisan:"))
async def process_artisan_choice(query: CallbackQuery, state: FSMContext) -> None:
    """Handles artisan selection from directory, manual option, or skip."""
    choice = query.data.split(":")[1]
    tg_id = query.from_user.id if query.from_user else 0

    if choice == "manual":
        await state.set_state(CreateJobStates.manual_artisan_phone)
        if query.message:
            await query.message.edit_text(
                "📱 *Enter Artisan Phone Number*\n\n"
                "Please type the artisan's phone number (e.g. `08012345678` or `+2348012345678`):",
                parse_mode="Markdown",
            )
        await query.answer()
        return

    data = await state.get_data()
    await state.clear()

    worker_id = uuid.UUID(choice) if choice != "skip" else None
    job, summary_text = await _finalize_job_creation(tg_id, worker_id, data)

    if query.message:
        await query.message.edit_text(
            summary_text,
            parse_mode="Markdown",
            reply_markup=fund_job_inline_keyboard(
                str(job.id),
                job.paga_account_number,
                show_assign_worker=(job.worker_id is None),
                show_assign_supervisor=(job.supervisor_name is None),
                supervisor_token=job.supervisor_token,
            ),
        )
    await query.answer("Job created!")


@router.message(CreateJobStates.manual_artisan_phone)
async def process_manual_artisan_phone(message: Message, state: FSMContext) -> None:
    """Handles manual artisan phone number entry, looking up or creating worker."""
    raw_phone = message.text.strip() if message.text else ""
    clean_phone = normalize_phone_number(raw_phone)
    tg_id = message.from_user.id if message.from_user else 0
    data = await state.get_data()
    trade = data.get("trade", "OTHER")

    async with async_session_factory() as session:
        # Check if worker exists
        stmt = select(User).where(User.phone == clean_phone).options(selectinload(User.worker_profile))
        worker = (await session.execute(stmt)).scalar_one_or_none()

        if not worker:
            # Create placeholder worker so they can be assigned and receive job invite SMS
            worker = User(
                name=f"Artisan ({clean_phone[-4:]})",
                phone=clean_phone,
                is_phone_verified=False,
                is_worker=True,
            )
            session.add(worker)
            await session.flush()
            prof = WorkerProfile(user_id=worker.id, trade=trade)
            session.add(prof)
            await session.commit()
        else:
            if not worker.is_worker:
                worker.is_worker = True
                if not worker.worker_profile:
                    prof = WorkerProfile(user_id=worker.id, trade=trade)
                    session.add(prof)
                await session.commit()

        worker_id = worker.id

    await state.clear()
    job, summary_text = await _finalize_job_creation(tg_id, worker_id, data)

    await message.answer(
        summary_text,
        parse_mode="Markdown",
        reply_markup=fund_job_inline_keyboard(
            str(job.id),
            job.paga_account_number,
            show_assign_worker=(job.worker_id is None),
            show_assign_supervisor=(job.supervisor_name is None),
            supervisor_token=job.supervisor_token,
        ),
    )
