import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, String, func
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import GUID, Base, TimestampMixin
from app.core.enums import JobStatus, JobType, MessageChannel, VerificationGrade

if TYPE_CHECKING:
    from app.payments.models import Payer, PaymentEvent
    from app.workers.models import Worker


class Job(Base, TimestampMixin):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        primary_key=True,
        default=uuid.uuid4,
    )
    worker_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("workers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    payer_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(),
        ForeignKey("payers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    payment_event_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(),
        ForeignKey("payment_events.id", ondelete="SET NULL"),
        unique=True,
        nullable=True,
        index=True,
    )
    amount_kobo: Mapped[int] = mapped_column(BigInteger, nullable=False)
    job_type: Mapped[JobType] = mapped_column(
        SQLEnum(JobType),
        nullable=False,
    )
    status: Mapped[JobStatus] = mapped_column(
        SQLEnum(JobStatus),
        default=JobStatus.DRAFT,
        nullable=False,
        index=True,
    )
    grade: Mapped[VerificationGrade] = mapped_column(
        SQLEnum(VerificationGrade),
        nullable=False,
    )
    client_phone: Mapped[str] = mapped_column(String(20), index=True, nullable=False)

    # Relationships
    worker: Mapped[Worker] = relationship("Worker", back_populates="jobs")
    payer: Mapped[Payer | None] = relationship("Payer", back_populates="jobs")
    payment_event: Mapped[PaymentEvent | None] = relationship("PaymentEvent", back_populates="job")
    confirmation: Mapped[Confirmation | None] = relationship(
        "Confirmation", back_populates="job", uselist=False, cascade="all, delete-orphan"
    )
    receipt: Mapped[Receipt | None] = relationship(
        "Receipt", back_populates="job", uselist=False, cascade="all, delete-orphan"
    )
    disputes: Mapped[list[Dispute]] = relationship(
        "Dispute", back_populates="job", cascade="all, delete-orphan"
    )


class Confirmation(Base, TimestampMixin):
    __tablename__ = "confirmations"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        primary_key=True,
        default=uuid.uuid4,
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )
    confirmation_token: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        index=True,
        nullable=False,
    )
    channel: Mapped[MessageChannel] = mapped_column(
        SQLEnum(MessageChannel),
        nullable=False,
    )
    is_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Relationships
    job: Mapped[Job] = relationship("Job", back_populates="confirmation")


class Receipt(Base, TimestampMixin):
    __tablename__ = "receipts"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        primary_key=True,
        default=uuid.uuid4,
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )
    receipt_number: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        index=True,
        nullable=False,
    )
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    receipt_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    # Relationships
    job: Mapped[Job] = relationship("Job", back_populates="receipt")


class Dispute(Base, TimestampMixin):
    __tablename__ = "disputes"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        primary_key=True,
        default=uuid.uuid4,
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="OPEN", nullable=False)

    # Relationships
    job: Mapped[Job] = relationship("Job", back_populates="disputes")
