import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import PaymentEventStatus, VerificationGrade


class CreatePaymentRequestSchema(BaseModel):
    worker_id: uuid.UUID
    amount_kobo: int = Field(gt=0, description="Amount in integer kobo")
    payer_phone: str = Field(description="Normalized E.164 phone number of payer")
    payer_name: str = Field(default="Client")
    job_type: str = Field(description="JobType enum value")
    description: str = Field(default="Payment for services")


class PaymentLinkResponse(BaseModel):
    reference: str
    payment_url: str
    amount_kobo: int
    worker_id: uuid.UUID


class PagaCallbackPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    transaction_id: str = Field(alias="transactionId")
    reference_number: str = Field(alias="referenceNumber")
    amount: float
    currency: str = "NGN"
    fee: float = 0.0
    datetime: str | None = None
    status: str
    payer_name: str | None = Field(default=None, alias="payerName")
    payer_phone: str | None = Field(default=None, alias="payerPhone")
    account_reference: str | None = Field(default=None, alias="accountReference")
    account_number: str | None = Field(default=None, alias="accountNumber")
    channel: str = "UNKNOWN"
    hash: str | None = None


class PaymentEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    paga_transaction_id: str
    reference: str
    amount_kobo: int
    worker_id: uuid.UUID
    payer_id: uuid.UUID | None
    channel: str
    grade: VerificationGrade
    status: PaymentEventStatus
    created_at: datetime
