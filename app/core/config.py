from typing import Any

from dotenv import load_dotenv
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Explicitly load .env into environment
load_dotenv()


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

    # Core Security & JWT Auth (loaded from .env)
    PROOFWORK_HMAC_SECRET: str = ""
    JWT_SECRET_KEY: str = ""
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 43200
    OTP_EXPIRE_MINUTES: int = 5
    OTP_MAX_ATTEMPTS: int = 5

    BASE_URL: str = "http://localhost:8000"

    # Auto-release window for daily escrow
    AUTO_RELEASE_WINDOW_HOURS: float = 24.0

    # Telegram Bot Configuration
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_WEBHOOK_SECRET: str = ""
    TELEGRAM_BOT_USERNAME: str = "proofwork_bot"

    # Paga Configuration (supports PAGA_PUBLIC_KEY or PAGA_PRINCIPAL, PAGA_SECRET_KEY or PAGA_CREDENTIALS, PAGA_HASH_KEY or PAGA_HMAC_KEY)
    PAGA_USE_MOCK: bool = True
    PAGA_SANDBOX_BASE_URL: str = "https://beta.mypaga.com/paga-webservices/business-rest"
    PAGA_PUBLIC_KEY: str = ""
    PAGA_SECRET_KEY: str = ""
    PAGA_HASH_KEY: str = ""
    PAGA_PRINCIPAL: str = ""
    PAGA_CREDENTIALS: str = ""
    PAGA_API_KEY: str = ""
    PAGA_HMAC_KEY: str = ""

    @property
    def paga_principal(self) -> str:
        return self.PAGA_PRINCIPAL or self.PAGA_PUBLIC_KEY or self.PAGA_API_KEY

    @property
    def paga_credentials(self) -> str:
        return self.PAGA_CREDENTIALS or self.PAGA_SECRET_KEY

    @property
    def paga_hash_key(self) -> str:
        return self.PAGA_HMAC_KEY or self.PAGA_HASH_KEY

    # httpSMS Configuration
    HTTPSMS_API_KEY: str = ""
    HTTPSMS_SENDER_PHONE: str = ""
    HTTPSMS_SIGNING_KEY: str = ""

    # WhatsApp Configuration (Simulated / Secondary)
    WHATSAPP_TOKEN: str = ""
    WHATSAPP_PHONE_NUMBER_ID: str = ""
    WHATSAPP_VERIFY_TOKEN: str = ""
    WHATSAPP_APP_SECRET: str = ""


settings = Settings()
