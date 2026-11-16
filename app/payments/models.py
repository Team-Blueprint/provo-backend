import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, BigInteger, ForeignKey, String
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import GUID, Base, TimestampMixin
from app.core.enums import PaymentEventStatus, VerificationGrade

if TYPE_CHECKING:
    from app.jobs.models import Job
    from app.workers.models import Worker


class Payer(Base, TimestampMixin):
    __tablename__ = "payers"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        primary_key=True,
        default=uuid.uuid4,
    )
    phone: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(150), nullable=True)

    # Relationships
    payment_events: Mapped[list[PaymentEvent]] = relationship("PaymentEvent", back_populates="payer")
    jobs: Mapped[list[Job]] = relationship("Job", back_populates="payer")


class PaymentEvent(Base, TimestampMixin):
    __tablename__ = "payment_events"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        primary_key=True,
        default=uuid.uuid4,
    )
    paga_transaction_id: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        index=True,
        nullable=False,
    )
    reference: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        index=True,
        nullable=False,
    )
    amount_kobo: Mapped[int] = mapped_column(BigInteger, nullable=False)
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
    channel: Mapped[str] = mapped_column(String(50), nullable=False)
    grade: Mapped[VerificationGrade] = mapped_column(
        SQLEnum(VerificationGrade),
        nullable=False,
    )
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[PaymentEventStatus] = mapped_column(
        SQLEnum(PaymentEventStatus),
        default=PaymentEventStatus.PENDING,
        nullable=False,
    )

    # Relationships
    worker: Mapped[Worker] = relationship("Worker", back_populates="payment_events")
    payer: Mapped[Payer | None] = relationship("Payer", back_populates="payment_events")
    job: Mapped[Job | None] = relationship("Job", back_populates="payment_event", uselist=False)
