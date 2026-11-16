import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import MessageChannel, MessageDirection, MessageStatus


class SendSMSRequest(BaseModel):
    to_phone: str = Field(description="Recipient E.164 phone number")
    content: str = Field(min_length=1, max_length=1000)


class HttpSMSWebhookPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    type: str  # e.g., "message.phone.received", "message.phone.delivered"
    data: dict[str, Any]
    datacontenttype: str | None = "application/json"
    time: str | None = None


class MessageLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    channel: MessageChannel
    message_id: str
    direction: MessageDirection
    to_phone: str
    from_phone: str | None
    content: str
    status: MessageStatus
    created_at: datetime
