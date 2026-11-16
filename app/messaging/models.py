import uuid
from typing import Any

from sqlalchemy import JSON, String, Text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import GUID, Base, TimestampMixin
from app.core.enums import MessageChannel, MessageDirection, MessageStatus


class MessageLog(Base, TimestampMixin):
    __tablename__ = "messages_log"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        primary_key=True,
        default=uuid.uuid4,
    )
    channel: Mapped[MessageChannel] = mapped_column(
        SQLEnum(MessageChannel),
        nullable=False,
    )
    message_id: Mapped[str] = mapped_column(
        String(128),
        unique=True,
        index=True,
        nullable=False,
    )
    direction: Mapped[MessageDirection] = mapped_column(
        SQLEnum(MessageDirection),
        nullable=False,
    )
    to_phone: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    from_phone: Mapped[str | None] = mapped_column(String(20), index=True, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[MessageStatus] = mapped_column(
        SQLEnum(MessageStatus),
        default=MessageStatus.QUEUED,
        nullable=False,
    )
    raw_data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
