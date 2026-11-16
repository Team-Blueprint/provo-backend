import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.enums import OTPPurpose
from app.core.security import (
    create_access_token,
    generate_otp_code,
    hash_otp_code,
    verify_otp_code,
)
from app.core.utils import normalize_phone_number
from app.db.models import AuthOTP, ClientProfile, User, WorkerProfile
from app.integrations.sms.client import SMSClient

logger = logging.getLogger(__name__)


class AuthService:
    def __init__(self, sms_client: SMSClient | None = None) -> None:
        self.sms_client = sms_client or SMSClient()

    async def request_signup(
        self,
        db: AsyncSession,
        name: str,
        phone: str,
        role: str = "worker",  # "worker" or "client"
        trade: str | None = None,
    ) -> AuthOTP:
        """
        Initiates passwordless registration:
        Creates an unverified user record and sends a 6-digit OTP via SMS.
        """
        clean_phone = normalize_phone_number(phone)
        clean_name = name.strip()

        # Check existing user
        stmt = select(User).where(User.phone == clean_phone)
        user = (await db.execute(stmt)).scalar_one_or_none()

        if user and user.is_phone_verified:
            raise ValueError("An account with this phone number already exists. Please log in.")

        is_worker = role.lower() == "worker"
        is_client = role.lower() == "client"

        if not user:
            user = User(
                name=clean_name,
                phone=clean_phone,
                is_phone_verified=False,
                is_worker=is_worker,
                is_client=is_client,
            )
            db.add(user)
            await db.flush()

            # Attach initial profile with trade for workers
            if is_worker:
                profile = WorkerProfile(
                    user_id=user.id,
                    trade=trade.strip() if trade else "OTHER",
                )
                db.add(profile)
            elif is_client:
                c_profile = ClientProfile(user_id=user.id)
                db.add(c_profile)
            await db.flush()
        else:
            user.name = clean_name
            if is_worker and trade and user.worker_profile:
                user.worker_profile.trade = trade.strip()
            await db.flush()

        return await self._create_and_send_otp(
            db=db,
            phone=clean_phone,
            purpose=OTPPurpose.SIGNUP.value,
        )

    async def request_login(
        self,
        db: AsyncSession,
        phone: str,
    ) -> AuthOTP:
        """
        Initiates passwordless login:
        Checks that user exists and dispatches an OTP via SMS.
        """
        clean_phone = normalize_phone_number(phone)
        stmt = select(User).where(User.phone == clean_phone)
        user = (await db.execute(stmt)).scalar_one_or_none()

        if not user:
            raise ValueError("Phone number not registered. Please sign up first.")

        return await self._create_and_send_otp(
            db=db,
            phone=clean_phone,
            purpose=OTPPurpose.LOGIN.value,
        )

    async def _create_and_send_otp(
        self,
        db: AsyncSession,
        phone: str,
        purpose: str,
    ) -> AuthOTP:
        """Generates, records, and sends an SMS OTP."""
        raw_code = generate_otp_code(digits=6)
        hashed_code = hash_otp_code(raw_code)
        expires_at = datetime.now(UTC) + timedelta(minutes=settings.OTP_EXPIRE_MINUTES)

        otp_record = AuthOTP(
            phone=phone,
            code_hash=hashed_code,
            purpose=purpose,
            attempts=0,
            is_used=False,
            expires_at=expires_at,
        )
        db.add(otp_record)
        await db.flush()

        # Send via SMS
        msg = f"Your Proofwork verification code is {raw_code}. Valid for {settings.OTP_EXPIRE_MINUTES} minutes."
        await self.sms_client.send_sms(to_phone=phone, message=msg)
        return otp_record

    async def verify_otp(
        self,
        db: AsyncSession,
        phone: str,
        code: str,
    ) -> tuple[User, str]:
        """
        Validates the submitted OTP.
        On success, activates phone verification and returns (User, JWT_Token).
        """
        clean_phone = normalize_phone_number(phone)
        clean_code = code.strip()
        now = datetime.now(UTC)

        stmt = (
            select(AuthOTP)
            .where(
                AuthOTP.phone == clean_phone,
                AuthOTP.is_used == False,
            )
            .order_by(AuthOTP.created_at.desc())
        )
        otp_record = (await db.execute(stmt)).scalars().first()

        expires_at = otp_record.expires_at if otp_record else None
        if expires_at and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)

        if not otp_record or (expires_at and expires_at < now):
            raise ValueError("OTP is invalid or has expired. Please request a new one.")

        if otp_record.attempts >= settings.OTP_MAX_ATTEMPTS:
            raise ValueError("Maximum verification attempts reached. Please request a new OTP.")

        if not verify_otp_code(clean_code, otp_record.code_hash):
            otp_record.attempts += 1
            await db.flush()
            remaining = settings.OTP_MAX_ATTEMPTS - otp_record.attempts
            raise ValueError(f"Incorrect OTP code. {remaining} attempt(s) remaining.")

        # Mark OTP as consumed
        otp_record.is_used = True

        # Load user
        user_stmt = (
            select(User)
            .where(User.phone == clean_phone)
            .options(
                selectinload(User.worker_profile),
                selectinload(User.client_profile),
            )
        )
        user = (await db.execute(user_stmt)).scalar_one()
        user.is_phone_verified = True
        await db.flush()

        token = create_access_token(
            data={"sub": str(user.id), "phone": user.phone, "name": user.name}
        )
        return user, token

    async def get_user_by_id(self, db: AsyncSession, user_id: uuid.UUID) -> User | None:
        stmt = (
            select(User)
            .where(User.id == user_id)
            .options(
                selectinload(User.worker_profile),
                selectinload(User.client_profile),
            )
        )
        return (await db.execute(stmt)).scalar_one_or_none()
