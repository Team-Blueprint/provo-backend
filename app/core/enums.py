from enum import Enum


class TradeType(str, Enum):
    BRICKLAYING = "BRICKLAYING"
    PAINTING = "PAINTING"
    PLUMBING = "PLUMBING"
    CARPENTRY = "CARPENTRY"
    ELECTRICAL = "ELECTRICAL"
    WELDING = "WELDING"
    TAILORING = "TAILORING"
    MECHANIC = "MECHANIC"
    OTHER = "OTHER"


# Backwards compatibility alias
JobType = TradeType


class JobStatus(str, Enum):
    DRAFT = "DRAFT"
    AWAITING_FUNDING = "AWAITING_FUNDING"
    FUNDED = "FUNDED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    DISPUTED = "DISPUTED"


class DayStatus(str, Enum):
    PENDING = "PENDING"
    DONE_REPORTED = "DONE_REPORTED"
    CONFIRMED = "CONFIRMED"
    AUTO_RELEASED = "AUTO_RELEASED"
    DISPUTED = "DISPUTED"
    PAID = "PAID"


class LedgerKind(str, Enum):
    FUNDED = "FUNDED"
    RELEASED = "RELEASED"
    REFUNDED = "REFUNDED"


class PayoutStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class DisputeStatus(str, Enum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"


class OTPPurpose(str, Enum):
    SIGNUP = "SIGNUP"
    LOGIN = "LOGIN"
    PHONE_VERIFY = "PHONE_VERIFY"


class MessageChannel(str, Enum):
    TELEGRAM = "TELEGRAM"
    HTTPSMS = "HTTPSMS"
    WHATSAPP = "WHATSAPP"
    USSD = "USSD"


class MessageDirection(str, Enum):
    INBOUND = "INBOUND"
    OUTBOUND = "OUTBOUND"


class MessageStatus(str, Enum):
    QUEUED = "QUEUED"
    SENT = "SENT"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    RECEIVED = "RECEIVED"
