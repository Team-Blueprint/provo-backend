import logging
import uuid

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.database import async_session_factory
from app.core.enums import DayStatus, JobStatus
from app.core.utils import kobo_to_naira_str, normalize_phone_number
from app.db.models import ClientProfile, Job, JobDay, User, WorkerProfile
from app.domain.scoring import DayScoreInput, calculate_worker_score
from app.integrations.telegram.keyboards import (
    client_main_menu,
    contact_keyboard,
    role_selection_keyboard,
    trade_selection_keyboard,
    worker_main_menu,
)
from app.services.release_service import ReleaseService

logger = logging.getLogger(__name__)
router = Router()
release_service = ReleaseService()


@router.message(CommandStart())
async def handle_start(message: Message) -> None:
    """Welcomes the user and initializes onboarding or main dashboard."""
    tg_id = message.from_user.id if message.from_user else 0

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
                    f"Welcome back, {user.name}! 🔨\nHere is your artisan dashboard.",
                    reply_markup=worker_main_menu(),
                )
            else:
                await message.answer(
                    f"Welcome back, {user.name}! 💼\nHere is your client dashboard.",
                    reply_markup=client_main_menu(),
                )
            return

    # User not yet registered on Telegram
    await message.answer(
        "Welcome to *Proofwork*! 🛡️\n\n"
        "Proofwork holds client payments in escrow, releases funds daily as work is confirmed, "
        "and creates verified records that lenders can trust.\n\n"
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
                f"Welcome to your Proofwork Artisan dashboard.",
                reply_markup=worker_main_menu(),
            )
        else:
            await message.answer(
                f"Account linked successfully, {user.name}! 💼\n"
                f"Welcome to your Proofwork Client dashboard.",
                reply_markup=client_main_menu(),
            )
        return

    await message.answer(
        f"Phone number verified: *{clean_phone}* ✅\n\n"
        "Are you using Proofwork as an Artisan (to do work and earn) or as a Client (to hire artisans)?",
        parse_mode="Markdown",
        reply_markup=role_selection_keyboard(),
    )


@router.callback_query(F.data.startswith("role:"))
async def handle_role_selection(query: CallbackQuery) -> None:
    """Routes user into either the Worker track or Client track."""
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
                "Welcome as a Proofwork Client! 💼\n"
                "You can now create escrow-backed jobs for artisans.",
                reply_markup=None,
            )
            await query.message.answer("Dashboard ready:", reply_markup=client_main_menu())

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
        "Your profile is active! When clients hire you through Proofwork, you will see your jobs here.",
        parse_mode="Markdown",
    )
    await query.message.answer("Artisan Dashboard:", reply_markup=worker_main_menu())
    await query.answer()


@router.message(F.text == "💼 My Active Jobs")
async def handle_my_active_jobs(message: Message) -> None:
    """Lists current active escrow jobs for the artisan."""
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
                "Tell your client to hire you on Proofwork or enter your phone number when creating a job!"
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
                    await message.answer(
                        f"✅ *Day {pending_day.day_number} reported done!* ({kobo_to_naira_str(pending_day.amount_kobo)})\n\n"
                        "Your client and supervisor have been notified to review and confirm. "
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
            f"📊 *Proofwork Verified Record for {user.name}:*\n\n"
            f"• *Credit Score:* {score_res.score} / 100\n"
            f"• *Rating Tier:* `{score_res.band}`\n"
            f"• *Confirmed Days:* {score_res.confirmed_days}\n"
            f"• *Auto-Released Days:* {score_res.auto_released_days}\n"
            f"• *Unique Clients:* {score_res.unique_payers}\n"
            f"• *Repeat Clients:* {score_res.repeat_payers}\n"
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
