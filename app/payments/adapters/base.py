from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class PersistentAccountResult:
    account_number: str
    account_reference: str
    bank_name: str
    account_name: str
    raw_response: dict[str, Any]


@dataclass
class PaymentRequestResult:
    payment_reference: str
    payment_url: str
    amount_kobo: int
    raw_response: dict[str, Any]


class PagaAdapter(ABC):
    """
    Abstract interface for Paga payment operations.
    Decouples core business logic from Paga's raw HTTP endpoints.
    """

    @abstractmethod
    async def create_persistent_account(
        self,
        worker_id: str,
        name: str,
        phone: str,
        account_reference: str,
    ) -> PersistentAccountResult:
        """Creates a dedicated virtual NIP account number for a worker."""

    @abstractmethod
    async def create_payment_request(
        self,
        reference: str,
        amount_kobo: int,
        currency: str,
        payer_phone: str,
        payer_name: str,
        description: str,
        callback_url: str,
    ) -> PaymentRequestResult:
        """Generates a Paga payment link for Grade 1 jobs."""

    @abstractmethod
    def verify_webhook_signature(
        self,
        raw_body: bytes,
        signature: str,
    ) -> bool:
        """Validates incoming webhook HMAC signature."""
