import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ConsentCreate(BaseModel):
    worker_id: uuid.UUID
    purpose: str = Field(default="Lender income verification")
    expiry_days: int = Field(default=90, ge=1, le=365)


class ShareLinkCreate(BaseModel):
    worker_id: uuid.UUID
    expiry_days: int = Field(default=7, ge=1, le=30)


class ShareLinkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    worker_id: uuid.UUID
    token: str
    share_url: str
    expires_at: datetime
    is_revoked: bool


class TokenizedPayerSummary(BaseModel):
    payer_token: str
    job_count: int
    first_payment_date: str
    last_payment_date: str
    phone_verified: bool


class LenderReportResponse(BaseModel):
    worker_id: uuid.UUID
    worker_name: str
    trade: str
    score: int
    score_reasons: list[str]
    total_confirmed_jobs: int
    total_confirmed_kobo: int
    total_confirmed_naira: str
    unique_payers_count: int
    repeat_clients_count: int
    active_months_count: int
    cash_percentage: float
    tokenized_payers: list[TokenizedPayerSummary]
    generated_at: str
