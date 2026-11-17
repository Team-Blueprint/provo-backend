
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    LoginRequest,
    OTPResponse,
    RefreshTokenRequest,
    ResendOTPRequest,
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
    Unified Passwordless Signup:
    Registers a new worker or client profile and dispatches a 6-digit OTP via SMS.
    Returns the otp_code in response for immediate testing convenience.
    """
    try:
        _otp, otp_code = await auth_service.request_signup(
            db=db,
            name=payload.name,
            phone=payload.phone,
            role=payload.role,
            trade=payload.trade.value if payload.trade else None,
        )
        return OTPResponse(
            phone=payload.phone,
            message="Verification code sent via SMS.",
            expires_in_minutes=settings.OTP_EXPIRE_MINUTES,
            otp_code=otp_code,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/login", response_model=OTPResponse)
async def login(
    payload: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Passwordless Login:
    Checks user registration and dispatches a fresh 6-digit OTP via SMS.
    Returns the otp_code in response for immediate testing convenience.
    """
    try:
        _otp, otp_code = await auth_service.request_login(db=db, phone=payload.phone)
        return OTPResponse(
            phone=payload.phone,
            message="Login verification code sent via SMS.",
            expires_in_minutes=settings.OTP_EXPIRE_MINUTES,
            otp_code=otp_code,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


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
        _otp, otp_code = await auth_service.resend_otp(db=db, phone=payload.phone)
        return OTPResponse(
            phone=payload.phone,
            message="Fresh verification code dispatched via SMS.",
            expires_in_minutes=settings.OTP_EXPIRE_MINUTES,
            otp_code=otp_code,
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
    return current_user
