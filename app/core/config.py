from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    PROJECT_NAME: str = "Proofwork Backend"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True

    @field_validator("DEBUG", mode="before")
    @classmethod
    def parse_debug(cls, v: Any) -> bool:
        if isinstance(v, str):
            return v.lower() in ("true", "1", "yes", "development", "dev")
        return bool(v)

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./proofwork.db"

    PROOFWORK_HMAC_SECRET: str = "b8c8d8b9f074a382e753f20d91d60ea54e3cb7a8b417df8b53298a4421b93f21"
    JWT_SECRET_KEY: str = "7d44933a39e80cbb0c741e3d06283b928f6e4a2c0f2095f70b7410c572b8344e"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 43200  # 30 days
    OTP_EXPIRE_MINUTES: int = 5
    OTP_MAX_ATTEMPTS: int = 5

    BASE_URL: str = "http://localhost:8000"

    # Auto-release window for daily escrow
    # 24 hours in production; can be set e.g. 0.033 (2 minutes) for demos
    AUTO_RELEASE_WINDOW_HOURS: float = 24.0

    # Telegram Bot Configuration
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_WEBHOOK_SECRET: str = ""
    TELEGRAM_BOT_USERNAME: str = "proofwork_bot"

    # Paga Configuration
    PAGA_USE_MOCK: bool = True
    PAGA_SANDBOX_BASE_URL: str = "https://beta.mypaga.com/paga-webservices/business-rest"
    PAGA_PRINCIPAL: str = ""
    PAGA_CREDENTIALS: str = ""
    PAGA_API_KEY: str = ""
    PAGA_HMAC_KEY: str = ""

    # httpSMS Configuration
    HTTPSMS_API_KEY: str = ""
    HTTPSMS_SENDER_PHONE: str = ""
    HTTPSMS_SIGNING_KEY: str = ""

    WHATSAPP_TOKEN: str = ""
    WHATSAPP_PHONE_NUMBER_ID: str = ""
    WHATSAPP_VERIFY_TOKEN: str = ""
    WHATSAPP_APP_SECRET: str = ""


settings = Settings()
