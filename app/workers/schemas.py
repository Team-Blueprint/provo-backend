import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class WorkerCreate(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    phone: str = Field(description="Normalized E.164 phone number, e.g. +2348012345678")
    trade: str = Field(min_length=2, max_length=100)


class WorkerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    phone: str
    trade: str
    paga_account_number: str | None = None
    paga_account_reference: str | None = None
    is_active: bool
    created_at: datetime
