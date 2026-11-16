from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PagaEscrowRequestResult:
    reference: str
    payment_url: str
    account_number: str
    bank_name: str
    amount_kobo: int
    raw_response: dict[str, Any]


@dataclass(frozen=True)
class PagaPayoutResult:
    payout_ref: str
    transaction_id: str
    destination_account: str
    amount_kobo: int
    status: str
    raw_response: dict[str, Any]


class PagaAdapter(ABC):
    """
    Abstract interface for Paga Escrow Collect and Payout operations.
    """

    @abstractmethod
    async def create_escrow_payment_request(
        self,
        reference: str,
        amount_kobo: int,
        client_phone: str,
        client_name: str,
        description: str,
        callback_url: str,
    ) -> PagaEscrowRequestResult:
        """Generates a one-time payment request and dedicated NUBAN account for job funding."""

    @abstractmethod
    async def disburse_payout(
        self,
        payout_ref: str,
        destination_account: str,
        bank_code: str,
        amount_kobo: int,
        recipient_name: str,
    ) -> PagaPayoutResult:
        """Disburses a confirmed/auto-released day's pay to the worker's Paga/bank account."""

    @abstractmethod
    def verify_webhook_signature(
        self,
        raw_body: bytes,
        signature: str,
    ) -> bool:
        """Validates incoming webhook HMAC-SHA512 signature against raw body."""
