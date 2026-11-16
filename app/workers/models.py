import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import GUID, Base, TimestampMixin

if TYPE_CHECKING:
    from app.jobs.models import Job
    from app.payments.models import PaymentEvent
    from app.reports.models import Consent, ShareLink


class Worker(Base, TimestampMixin):
    __tablename__ = "workers"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    phone: Mapped[str] = mapped_column(String(20), unique=True, index=True, nullable=False)
    trade: Mapped[str] = mapped_column(String(100), nullable=False)
    paga_account_number: Mapped[str | None] = mapped_column(String(20), index=True, nullable=True)
    paga_account_reference: Mapped[str | None] = mapped_column(String(64), unique=True, index=True, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Relationships
    jobs: Mapped[list[Job]] = relationship("Job", back_populates="worker", cascade="all, delete-orphan")
    payment_events: Mapped[list[PaymentEvent]] = relationship("PaymentEvent", back_populates="worker")
    consents: Mapped[list[Consent]] = relationship("Consent", back_populates="worker", cascade="all, delete-orphan")
    share_links: Mapped[list[ShareLink]] = relationship("ShareLink", back_populates="worker", cascade="all, delete-orphan")
