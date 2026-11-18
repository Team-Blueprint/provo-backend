
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    LoginRequest,
    OTPResponse,
    RefreshTokenRequest,
    RequestSetPinRequest,
    ResendOTPRequest,
    SetPinRequest,
    SignupRequest,
    TokenResponse,
    UserProfileResponse,
    VerifyOTPRequest,
)
from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.db.models import User
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["Authentication"])
auth_service = AuthService()


@router.post("/signup", response_model=OTPResponse, status_code=status.HTTP_201_CREATED)
async def signup(
    payload: SignupRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Unified Signup with 6-Digit PIN:
    Registers a new worker or employer profile with a security PIN and dispatches a 6-digit OTP via SMS.
    """
    try:
        _otp, _otp_code = await auth_service.request_signup(
            db=db,
            name=payload.name,
            phone=payload.phone,
            pin=payload.pin,
            role=payload.role,
            trade=payload.trade.value if payload.trade else None,
        )
        return OTPResponse(
            phone=payload.phone,
            message="Verification code sent via SMS.",
            expires_in_minutes=settings.OTP_EXPIRE_MINUTES,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Direct PIN-based Login:
    Authenticates with registered phone number and 6-digit PIN.
    Immediately returns JWT access and refresh tokens without requiring OTP verification.
    """
    try:
        user, access_token, refresh_token = await auth_service.login_with_pin(
            db=db,
            phone=payload.phone,
            pin=payload.pin,
        )
        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            user_id=user.id,
            name=user.name,
            phone=user.phone,
            is_worker=user.is_worker,
            is_client=user.is_client,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))


@router.post("/resend-otp", response_model=OTPResponse)
async def resend_otp(
    payload: ResendOTPRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Resend OTP:
    Dispatches a fresh OTP if the previous code expired before verification.
    """
    try:
        _otp, _otp_code = await auth_service.resend_otp(db=db, phone=payload.phone)
        return OTPResponse(
            phone=payload.phone,
            message="Fresh verification code dispatched via SMS.",
            expires_in_minutes=settings.OTP_EXPIRE_MINUTES,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/request-set-pin", response_model=OTPResponse)
async def request_set_pin(
    payload: RequestSetPinRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    For Telegram-registered accounts that want to set a web login PIN.
    Sends an OTP to the account's verified phone number.
    Only works if the account was created via Telegram and has no PIN yet.
    """
    try:
        _otp, _otp_code = await auth_service.request_set_pin_otp(db=db, phone=payload.phone)
        return OTPResponse(
            phone=payload.phone,
            message="PIN setup OTP sent via SMS.",
            expires_in_minutes=settings.OTP_EXPIRE_MINUTES,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/set-pin", response_model=TokenResponse)
async def set_pin(
    payload: SetPinRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Completes PIN setup for a Telegram-registered account.
    Validates OTP, sets the PIN hash, and returns JWT tokens for immediate login.
    In demo mode, OTP code '123456' is accepted as a universal bypass.
    """
    try:
        user, access_token, refresh_token = await auth_service.set_pin_for_telegram_user(
            db=db,
            phone=payload.phone,
            otp_code=payload.otp_code,
            new_pin=payload.new_pin,
        )
        await db.commit()
        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            user_id=user.id,
            name=user.name,
            phone=user.phone,
            is_worker=user.is_worker,
            is_client=user.is_client,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/verify-otp", response_model=TokenResponse)
async def verify_otp(
    payload: VerifyOTPRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    OTP Verification:
    Validates submitted SMS code and returns signed JWT access token and refresh token.
    """
    try:
        user, access_token, refresh_token = await auth_service.verify_otp(
            db=db,
            phone=payload.phone,
            code=payload.code,
        )
        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            user_id=user.id,
            name=user.name,
            phone=user.phone,
            is_worker=user.is_worker,
            is_client=user.is_client,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token_endpoint(
    payload: RefreshTokenRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Token Refresh:
    Exchanges a valid refresh token for a fresh access token and refresh token pair.
    """
    try:
        user, access_token, refresh_token = await auth_service.refresh_access_token(
            db=db,
            refresh_token=payload.refresh_token,
        )
        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            user_id=user.id,
            name=user.name,
            phone=user.phone,
            is_worker=user.is_worker,
            is_client=user.is_client,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))


@router.get("/me", response_model=UserProfileResponse)
async def get_me(
    current_user: User = Depends(get_current_user),
):
    """Returns currently authenticated user profile."""
    # Build response manually so we can add computed fields
    return UserProfileResponse(
        id=current_user.id,
        name=current_user.name,
        phone=current_user.phone,
        is_worker=current_user.is_worker,
        is_client=current_user.is_client,
        is_supervisor=current_user.is_supervisor,
        payout_account_number=current_user.payout_account_number,
        payout_bank_code=current_user.payout_bank_code,
        needs_pin_setup=(current_user.telegram_user_id is not None and current_user.pin_hash is None),
    )
