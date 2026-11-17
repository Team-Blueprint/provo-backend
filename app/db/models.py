import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import GUID, Base, TimestampMixin
from app.core.enums import (
    DayStatus,
    DisputeStatus,
    JobStatus,
    OTPPurpose,
    PayoutStatus,
    TradeType,
)


class AuthOTP(Base, TimestampMixin):
    """Stores passwordless SMS OTP challenges with rate limiting."""
    __tablename__ = "auth_otps"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    phone: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    code_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    purpose: Mapped[str] = mapped_column(String(30), default=OTPPurpose.LOGIN.value, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class User(Base, TimestampMixin):
    """Core user entity representing clients, workers, supervisors and admins."""
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, index=True, nullable=True)
    phone: Mapped[str] = mapped_column(String(20), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    is_phone_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Role flags
    is_client: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_worker: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_supervisor: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Worker destination payout credentials
    payout_account_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    payout_bank_code: Mapped[str | None] = mapped_column(String(10), nullable=True)

    # Profile relationships
    worker_profile: Mapped[WorkerProfile | None] = relationship(
        "WorkerProfile", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    client_profile: Mapped[ClientProfile | None] = relationship(
        "ClientProfile", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )

    # Job relationships
    jobs_as_client: Mapped[list[Job]] = relationship(
        "Job", back_populates="client", foreign_keys="Job.client_id"
    )
    jobs_as_worker: Mapped[list[Job]] = relationship(
        "Job", back_populates="worker", foreign_keys="Job.worker_id"
    )
    jobs_as_supervisor: Mapped[list[Job]] = relationship(
        "Job", back_populates="supervisor", foreign_keys="Job.supervisor_id"
    )


class WorkerProfile(Base, TimestampMixin):
    """Artisan directory listing and trade-specific profile."""
    __tablename__ = "worker_profiles"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("users.id"), unique=True, index=True, nullable=False)
    trade: Mapped[str] = mapped_column(String(50), default=TradeType.OTHER.value, index=True, nullable=False)
    area: Mapped[str] = mapped_column(String(100), default="Uyo", index=True, nullable=False)
    is_available: Mapped[bool] = mapped_column(Boolean, default=True, index=True, nullable=False)
    extra_data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    # Personal Paga Collect virtual account for direct payments to this worker
    paga_account_number: Mapped[str | None] = mapped_column(String(30), nullable=True, unique=True, index=True)
    paga_payment_url: Mapped[str | None] = mapped_column(String(512), nullable=True)

    user: Mapped[User] = relationship("User", back_populates="worker_profile")


class ClientProfile(Base, TimestampMixin):
    """Client profile with extension point for custom onboarding data."""
    __tablename__ = "client_profiles"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("users.id"), unique=True, index=True, nullable=False)
    extra_data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    user: Mapped[User] = relationship("User", back_populates="client_profile")


class Job(Base, TimestampMixin):
    """Escrow job holding total pay locked upfront before work commences."""
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("users.id"), index=True, nullable=False)
    worker_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("users.id"), index=True, nullable=True)
    supervisor_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("users.id"), index=True, nullable=True)

    trade: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    total_kobo: Mapped[int] = mapped_column(Integer, nullable=False)
    days: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(30), default=JobStatus.DRAFT.value, index=True, nullable=False)

    funding_reference: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    paga_payment_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    paga_account_number: Mapped[str | None] = mapped_column(String(30), nullable=True)

    # Relationships
    client: Mapped[User] = relationship("User", foreign_keys=[client_id], back_populates="jobs_as_client")
    worker: Mapped[User | None] = relationship("User", foreign_keys=[worker_id], back_populates="jobs_as_worker")
    supervisor: Mapped[User | None] = relationship("User", foreign_keys=[supervisor_id], back_populates="jobs_as_supervisor")

    days_schedule: Mapped[list[JobDay]] = relationship(
        "JobDay", back_populates="job", cascade="all, delete-orphan", order_by="JobDay.day_number"
    )
    payments_in: Mapped[list[PaymentIn]] = relationship("PaymentIn", back_populates="job")
    ledger_entries: Mapped[list[LedgerEntry]] = relationship("LedgerEntry", back_populates="job")
    receipt: Mapped[Receipt | None] = relationship("Receipt", back_populates="job", uselist=False)

    @property
    def days_count(self) -> int:
        return self.days

    @property
    def days_list(self) -> list[JobDay]:
        return self.days_schedule


class JobDay(Base, TimestampMixin):
    """Daily work slice governed by the 24-hour confirm/auto-release decision window."""
    __tablename__ = "job_days"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("jobs.id"), index=True, nullable=False)
    worker_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("users.id"), index=True, nullable=True)
    day_number: Mapped[int] = mapped_column(Integer, nullable=False)
    amount_kobo: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default=DayStatus.PENDING.value, index=True, nullable=False)

    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    release_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True, nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("users.id"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    job: Mapped[Job] = relationship("Job", back_populates="days_schedule")
    worker: Mapped[User | None] = relationship("User", foreign_keys=[worker_id])
    decided_by_user: Mapped[User | None] = relationship("User", foreign_keys=[decided_by])
    payout: Mapped[Payout | None] = relationship("Payout", back_populates="day", uselist=False)
    dispute: Mapped[Dispute | None] = relationship("Dispute", back_populates="day", uselist=False)


class PaymentIn(Base, TimestampMixin):
    """Inbound Paga Collect payment locked into escrow."""
    __tablename__ = "payments_in"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    paga_event_id: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("jobs.id"), index=True, nullable=False)
    amount_kobo: Mapped[int] = mapped_column(Integer, nullable=False)
    payer_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    payer_phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    payer_account_number: Mapped[str | None] = mapped_column(String(30), nullable=True)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    job: Mapped[Job] = relationship("Job", back_populates="payments_in")


class LedgerEntry(Base, TimestampMixin):
    """Double-entry style financial movement record for escrow auditing."""
    __tablename__ = "ledger_entries"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("jobs.id"), index=True, nullable=False)
    day_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("job_days.id"), index=True, nullable=True)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)  # FUNDED, RELEASED, REFUNDED
    amount_kobo: Mapped[int] = mapped_column(Integer, nullable=False)

    job: Mapped[Job] = relationship("Job", back_populates="ledger_entries")
    day: Mapped[JobDay | None] = relationship("JobDay")


class Payout(Base, TimestampMixin):
    """Outbox record for disbursing daily pay to a worker's Paga account."""
    __tablename__ = "payouts"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    day_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("job_days.id"), unique=True, index=True, nullable=False)
    amount_kobo: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_ref: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default=PayoutStatus.PENDING.value, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    paga_response: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    day: Mapped[JobDay] = relationship("JobDay", back_populates="payout")


class Dispute(Base, TimestampMixin):
    """Day dispute freezing payout and stopping auto-release."""
    __tablename__ = "disputes"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    day_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("job_days.id"), unique=True, index=True, nullable=False)
    opened_by: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("users.id"), index=True, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default=DisputeStatus.OPEN.value, nullable=False)
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("users.id"), nullable=True)

    day: Mapped[JobDay] = relationship("JobDay", back_populates="dispute")


class Receipt(Base, TimestampMixin):
    """Immutable Proofwork receipt minted upon final job settlement."""
    __tablename__ = "receipts"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    receipt_number: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("jobs.id"), unique=True, index=True, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)
    receipt_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    job: Mapped[Job] = relationship("Job", back_populates="receipt")


class PayerToken(Base, TimestampMixin):
    """Deterministic token mapping clients to anonymous tokens per worker for lenders."""
    __tablename__ = "payer_tokens"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("users.id"), index=True, nullable=False)
    worker_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("users.id"), index=True, nullable=False)
    payer_token: Mapped[str] = mapped_column(String(64), index=True, nullable=False)


class Consent(Base, TimestampMixin):
    """NDPA-compliant worker consent record for sharing verified reports with lenders."""
    __tablename__ = "consents"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    worker_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("users.id"), index=True, nullable=False)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    purpose: Mapped[str] = mapped_column(String(255), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    access_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ClientNotice(Base, TimestampMixin):
    """Audit log verifying client was informed that their payment creates an official work record."""
    __tablename__ = "client_notices"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("jobs.id"), index=True, nullable=False)
    client_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("users.id"), index=True, nullable=False)
    notice_type: Mapped[str] = mapped_column(String(50), nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False)


class AuditEvent(Base, TimestampMixin):
    """Append-only audit trail for all state machine transitions and privileged actions."""
    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    entity_name: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    action: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    actor: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
