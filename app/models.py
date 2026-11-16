"""
Central metadata aggregator for Proofwork Backend.
Re-exports declarative models from app.db.models on Base.metadata.
"""

from app.core.database import Base
from app.db.models import (
    AuditEvent,
    AuthOTP,
    ClientNotice,
    ClientProfile,
    Consent,
    Dispute,
    Job,
    JobDay,
    LedgerEntry,
    PayerToken,
    PaymentIn,
    Payout,
    Receipt,
    User,
    WorkerProfile,
)

__all__ = [
    "AuditEvent",
    "AuthOTP",
    "Base",
    "ClientNotice",
    "ClientProfile",
    "Consent",
    "Dispute",
    "Job",
    "JobDay",
    "LedgerEntry",
    "PayerToken",
    "PaymentIn",
    "Payout",
    "Receipt",
    "User",
    "WorkerProfile",
]
