import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import DayStatus, JobStatus, TradeType


# --- Auth Schemas ---
class SignupRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = Field(..., min_length=2, max_length=150)
    phone: str = Field(..., description="Phone number in national or E.164 format")
    role: str = Field("worker", description="'worker' or 'client'")
    trade: TradeType | None = Field(None, description="Artisan track if role is worker")


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    phone: str = Field(..., description="Phone number in national or E.164 format")


class ResendOTPRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    phone: str = Field(..., description="Phone number in national or E.164 format")


class VerifyOTPRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    phone: str
    code: str = Field(..., min_length=4, max_length=10)


class RefreshTokenRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    refresh_token: str


class OTPResponse(BaseModel):
    phone: str
    message: str
    expires_in_minutes: int
    otp_code: str | None = Field(None, description="OTP code included for convenience during development/testing")


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user_id: uuid.UUID
    name: str
    phone: str
    is_worker: bool
    is_client: bool


class UserProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    phone: str
    is_worker: bool
    is_client: bool
    is_supervisor: bool
    payout_account_number: str | None = None
    payout_bank_code: str | None = None


# --- Job Schemas ---
class DayScheduleInput(BaseModel):
    model_config = ConfigDict(extra="ignore")
    day_number: int = Field(..., ge=1, le=90)
    amount_kobo: int | None = Field(None, gt=0, description="Amount for this day; if omitted, total_kobo is split equally")
    worker_id: uuid.UUID | None = Field(None, description="Specific artisan assigned to this day/task")


class JobCreateRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    trade: TradeType
    description: str = Field(..., min_length=5)
    total_kobo: int = Field(..., gt=0, description="Total job amount in integer kobo")
    days: int = Field(1, ge=1, le=90, description="Number of working days for escrow release")
    worker_id: uuid.UUID | None = Field(None, description="Default artisan for the job (if not specified per day)")
    supervisor_id: uuid.UUID | None = None
    auto_fund_request: bool = Field(
        True,
        description="If True (default), immediately calls Paga to generate the dedicated NUBAN and checkout link upon creation.",
    )
    days_breakdown: list[DayScheduleInput] | None = Field(
        None, description="Optional custom breakdown assigning specific days to specific artisans or amounts"
    )


class JobDayResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    day_number: int
    worker_id: uuid.UUID | None = None
    amount_kobo: int
    status: DayStatus
    done_at: Any = None
    release_at: Any = None


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    client_id: uuid.UUID
    worker_id: uuid.UUID | None
    supervisor_id: uuid.UUID | None
    trade: str
    description: str
    total_kobo: int
    days: int
    status: JobStatus
    funding_reference: str
    paga_payment_url: str | None = None
    paga_account_number: str | None = None
    days_schedule: list[JobDayResponse] = []


class DisputeRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    reason: str = Field(..., min_length=3, max_length=500)


class ReceiptResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    receipt_number: str
    job_id: uuid.UUID
    issued_at: Any
    receipt_data: dict[str, Any]


# --- Worker Directory & Sharing Schemas ---
class WorkerDirectoryItem(BaseModel):
    id: uuid.UUID
    name: str
    trade: str
    area: str
    verified_days_count: int
    badge_grade: str


class CreateShareRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    purpose: str = Field("Lender Credit Evaluation", min_length=3, max_length=150)
    valid_days: int = Field(7, ge=1, le=30)


class ShareResponse(BaseModel):
    token: str
    report_url: str
    expires_at: str
    purpose: str


# --- Admin Dispute Resolution Schemas ---
class DisputeResolveRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    action: str = Field(..., description="'RELEASE_TO_WORKER', 'REFUND_TO_CLIENT', or 'SPLIT'")
    resolution_note: str = Field(..., min_length=5, max_length=500)
    worker_amount_kobo: int | None = None
    client_amount_kobo: int | None = None

