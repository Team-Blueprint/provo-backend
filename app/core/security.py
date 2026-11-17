import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from app.core.config import settings


def generate_secure_token(nbytes: int = 32) -> str:
    """Generates a cryptographically secure URL-safe token."""
    return secrets.token_urlsafe(nbytes)


def generate_otp_code(digits: int = 6) -> str:
    """Generates a cryptographically secure numeric OTP."""
    range_start = 10 ** (digits - 1)
    range_end = (10**digits) - 1
    return str(secrets.randbelow(range_end - range_start + 1) + range_start)


def hash_otp_code(code: str) -> str:
    """Hashes an OTP code using SHA-256 for secure storage."""
    return hashlib.sha256(code.strip().encode("utf-8")).hexdigest()


def verify_otp_code(code: str, hashed_code: str) -> bool:
    """Constant-time verification of an OTP against its stored hash."""
    candidate_hash = hash_otp_code(code)
    return hmac.compare_digest(candidate_hash.lower(), hashed_code.lower())


def create_access_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    """Creates a signed JWT access token for authenticated sessions."""
    to_encode = data.copy()
    now = datetime.now(UTC)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire, "iat": now, "type": "access"})
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    """Creates a signed JWT refresh token for renewing sessions."""
    to_encode = data.copy()
    now = datetime.now(UTC)
    expire = now + (expires_delta or timedelta(days=60))
    to_encode.update({"exp": expire, "iat": now, "type": "refresh"})
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    """Decodes and validates a signed JWT access token."""
    return jwt.decode(
        token,
        settings.JWT_SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
        options={"require": ["exp", "iat"]},
    )


def compute_hmac_sha256(secret: str, data: str) -> str:
    """Computes HMAC-SHA256 digest in hex."""
    return hmac.new(secret.encode("utf-8"), data.encode("utf-8"), hashlib.sha256).hexdigest()


def compute_hmac_sha512(secret: str, data: str) -> str:
    """Computes HMAC-SHA512 digest in hex."""
    return hmac.new(secret.encode("utf-8"), data.encode("utf-8"), hashlib.sha512).hexdigest()


def generate_payer_token(worker_id: str, payer_phone: str, hmac_secret: str) -> str:
    """
    Creates an anonymized, deterministic payer token scoped per worker.
    E.g., Client-A3F (derived from HMAC).
    Ensures repeat clients are identifiable to lenders without leaking real phone numbers.
    """
    scoped_data = f"{worker_id}:{payer_phone}"
    digest = compute_hmac_sha256(hmac_secret, scoped_data)
    short_hash = digest[:6].upper()
    return f"Client-{short_hash}"


def verify_hmac_signature(secret: str, raw_body: bytes, expected_signature: str) -> bool:
    """Constant-time verification of HMAC-SHA512 / SHA256 signatures."""
    computed_512 = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha512).hexdigest()
    if hmac.compare_digest(computed_512.lower(), expected_signature.lower()):
        return True

    computed_256 = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed_256.lower(), expected_signature.lower())
