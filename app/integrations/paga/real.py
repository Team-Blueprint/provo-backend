import hashlib
import hmac
import logging
from typing import Any

import httpx

from app.core.config import settings
from app.integrations.paga.base import (
    PagaAdapter,
    PagaEscrowRequestResult,
    PagaPayoutResult,
)

logger = logging.getLogger(__name__)


class RealPagaAdapter(PagaAdapter):
    """
    Live/Sandbox client communicating with Paga APIs using SHA-512 signatures.
    """

    def __init__(self) -> None:
        self.base_url = settings.PAGA_SANDBOX_BASE_URL.rstrip("/")
        self.public_key = settings.PAGA_PUBLIC_KEY
        self.secret_key = settings.PAGA_SECRET_KEY
        self.hash_key = settings.PAGA_HASH_KEY

    def _compute_hash(self, *values: Any) -> str:
        """Concatenates parameters with Hash Key and returns SHA-512 hex digest."""
        raw_concat = "".join(str(v) for v in values) + self.hash_key
        return hashlib.sha512(raw_concat.encode("utf-8")).hexdigest()

    async def create_escrow_payment_request(
        self,
        reference: str,
        amount_kobo: int,
        client_phone: str,
        client_name: str,
        description: str,
        callback_url: str,
    ) -> PagaEscrowRequestResult:
        url = f"{self.base_url}/paymentRequest"
        amount_naira = f"{amount_kobo / 100:.2f}"
        currency = "NGN"

        auth_hash = self._compute_hash(reference, amount_naira, currency)

        payload = {
            "referenceNumber": reference,
            "amount": amount_naira,
            "currency": currency,
            "payer": {
                "name": client_name,
                "phoneNumber": client_phone,
            },
            "payee": {
                "name": settings.PROJECT_NAME,
            },
            "description": description,
            "expiryDate": None,
            "callBackUrl": callback_url,
        }

        headers = {
            "principal": self.public_key,
            "credentials": self.secret_key,
            "hash": auth_hash,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        payment_url = data.get("paymentUrl") or f"{self.base_url}/pay/{reference}"
        account_number = data.get("accountNumber") or ""

        return PagaEscrowRequestResult(
            reference=reference,
            payment_url=payment_url,
            account_number=account_number,
            bank_name=data.get("bankName", "Paga MFB"),
            amount_kobo=amount_kobo,
            raw_response=data,
        )

    async def disburse_payout(
        self,
        payout_ref: str,
        destination_account: str,
        bank_code: str,
        amount_kobo: int,
        recipient_name: str,
    ) -> PagaPayoutResult:
        url = f"{self.base_url}/moneyTransfer"
        amount_naira = f"{amount_kobo / 100:.2f}"
        currency = "NGN"

        auth_hash = self._compute_hash(payout_ref, amount_naira, destination_account)

        payload = {
            "referenceNumber": payout_ref,
            "amount": amount_naira,
            "currency": currency,
            "destinationAccount": destination_account,
            "destinationBank": bank_code,
            "recipient": {
                "name": recipient_name,
            },
        }

        headers = {
            "principal": self.public_key,
            "credentials": self.secret_key,
            "hash": auth_hash,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        return PagaPayoutResult(
            payout_ref=payout_ref,
            transaction_id=str(data.get("transactionId", "")),
            destination_account=destination_account,
            amount_kobo=amount_kobo,
            status=str(data.get("status", "SUCCESS")),
            raw_response=data,
        )

    def verify_webhook_signature(
        self,
        raw_body: bytes,
        signature: str,
    ) -> bool:
        if not self.hash_key:
            logger.warning("PAGA_HASH_KEY not set; webhook signature verification failed.")
            return False

        computed = hmac.new(self.hash_key.encode("utf-8"), raw_body, hashlib.sha512).hexdigest()
        return hmac.compare_digest(computed.lower(), signature.strip().lower())
