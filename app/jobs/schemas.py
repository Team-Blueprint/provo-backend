import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import JobStatus, JobType, VerificationGrade


class CashJobDraftCreate(BaseModel):
    worker_id: uuid.UUID
    amount_kobo: int = Field(gt=0, description="Amount in integer kobo")
    client_phone: str = Field(description="Client's normalized E.164 phone number")
    job_type: JobType
    description: str | None = None


class JobConfirmRequest(BaseModel):
    confirmation_token: str
    job_type: JobType | None = None
    confirmed: bool = True  # True = confirmed, False = disputed


class ReceiptResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_id: uuid.UUID
    receipt_number: str
    issued_at: datetime
    receipt_data: dict[str, Any]


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    worker_id: uuid.UUID
    payer_id: uuid.UUID | None
    amount_kobo: int
    job_type: JobType
    status: JobStatus
    grade: VerificationGrade
    client_phone: str
    created_at: datetime
    receipt: ReceiptResponse | None = None
