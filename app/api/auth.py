import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    LoginRequest,
    OTPResponse,
    SignupRequest,
    TokenResponse,
    UserProfileResponse,
    VerifyOTPRequest,
)
from app.core.config import settings
from app.core.database import get_db
from app.core.security import decode_access_token
from app.db.models import User
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["Authentication"])
auth_service = AuthService()


async def get_current_user(
    authorization: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
) -> User:
    """FastAPI dependency resolving the authenticated User from the Bearer token."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
        )

    token = authorization.split(" ")[1]
    try:
        payload = decode_access_token(token)
        user_id_str = payload.get("sub")
        if not user_id_str:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token payload missing subject identifier",
            )
        user = await auth_service.get_user_by_id(db, uuid.UUID(user_id_str))
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
            )
        return user
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired token: {e}",
        )


@router.post("/signup", response_model=OTPResponse, status_code=status.HTTP_201_CREATED)
async def signup(
    payload: SignupRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Passwordless Signup:
    Registers a new worker or client profile and dispatches a 6-digit OTP via SMS.
    """
    try:
        _otp = await auth_service.request_signup(
            db=db,
            name=payload.name,
            phone=payload.phone,
            role=payload.role,
        )
        return OTPResponse(
            phone=payload.phone,
            message="Verification code sent via SMS.",
            expires_in_minutes=settings.OTP_EXPIRE_MINUTES,
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
    """
    try:
        _otp = await auth_service.request_login(db=db, phone=payload.phone)
        return OTPResponse(
            phone=payload.phone,
            message="Login verification code sent via SMS.",
            expires_in_minutes=settings.OTP_EXPIRE_MINUTES,
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
    Validates submitted SMS code and returns signed JWT access token.
    """
    try:
        user, token = await auth_service.verify_otp(
            db=db,
            phone=payload.phone,
            code=payload.code,
        )
        return TokenResponse(
            access_token=token,
            user_id=user.id,
            name=user.name,
            phone=user.phone,
            is_worker=user.is_worker,
            is_client=user.is_client,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/me", response_model=UserProfileResponse)
async def get_me(
    current_user: User = Depends(get_current_user),
):
    """Returns currently authenticated user profile."""
    return current_user
