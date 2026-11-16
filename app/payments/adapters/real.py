import hashlib
import hmac
import logging
from typing import Any

import httpx

from app.core.config import settings
from app.payments.adapters.base import (
    PagaAdapter,
    PaymentRequestResult,
    PersistentAccountResult,
)

logger = logging.getLogger(__name__)


class RealPagaAdapter(PagaAdapter):
    """
    Real implementation connecting to Paga Sandbox Business REST API.
    Handles HMAC-SHA512 signature computation and secure HTTP requests.
    """

    def __init__(
        self,
        base_url: str | None = None,
        principal: str | None = None,
        credentials: str | None = None,
        hmac_key: str | None = None,
    ):
        self.base_url = (base_url or settings.PAGA_SANDBOX_BASE_URL).rstrip("/")
        self.principal = principal or settings.PAGA_PRINCIPAL
        self.credentials = credentials or settings.PAGA_CREDENTIALS
        self.hmac_key = hmac_key or settings.PAGA_HMAC_KEY

    def _generate_paga_hash(self, *args: str) -> str:
        """
        Calculates Paga HMAC-SHA512 hash by concatenating parameters
        and signing with Paga HMAC key.
        """
        payload_string = "".join(str(a) for a in args)
        return hmac.new(
            self.hmac_key.encode("utf-8"),
            payload_string.encode("utf-8"),
            hashlib.sha512,
        ).hexdigest()

    async def create_persistent_account(
        self,
        worker_id: str,
        name: str,
        phone: str,
        account_reference: str,
    ) -> PersistentAccountResult:
        """
        Calls Paga /registerPersistentPaymentAccount to create a dedicated NUBAN.
        """
        endpoint = f"{self.base_url}/registerPersistentPaymentAccount"
        hash_val = self._generate_paga_hash(account_reference, phone)

        headers = {
            "principal": self.principal,
            "credentials": self.credentials,
            "hash": hash_val,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        names = name.strip().split(" ", 1)
        first_name = names[0]
        last_name = names[1] if len(names) > 1 else "Worker"

        body: dict[str, Any] = {
            "referenceNumber": account_reference,
            "phoneNumber": phone,
            "firstName": first_name,
            "lastName": last_name,
            "accountName": f"Proofwork / {name}",
            "creditBankTransfer": True,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(endpoint, json=body, headers=headers)
            response.raise_for_status()
            data = response.json()

        logger.info("Paga persistent account response for worker %s: %s", worker_id, data)

        return PersistentAccountResult(
            account_number=str(data.get("accountNumber") or ""),
            account_reference=account_reference,
            bank_name=str(data.get("bankName") or "Paga"),
            account_name=str(data.get("accountName") or f"Proofwork / {name}"),
            raw_response=data,
        )

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
        """
        Calls Paga /paymentRequest to initiate a Grade 1 payment link.
        """
        endpoint = f"{self.base_url}/paymentRequest"
        amount_naira = amount_kobo / 100
        hash_val = self._generate_paga_hash(reference, f"{amount_naira:.2f}", currency)

        headers = {
            "principal": self.principal,
            "credentials": self.credentials,
            "hash": hash_val,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        body: dict[str, Any] = {
            "referenceNumber": reference,
            "amount": amount_naira,
            "currency": currency,
            "payerPhoneNumber": payer_phone,
            "payerName": payer_name,
            "description": description,
            "callBackUrl": callback_url,
            "isAllowPartialPayments": False,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(endpoint, json=body, headers=headers)
            response.raise_for_status()
            data = response.json()

        logger.info("Paga payment request response: %s", data)

        return PaymentRequestResult(
            payment_reference=reference,
            payment_url=str(data.get("paymentUrl") or data.get("checkoutUrl") or ""),
            amount_kobo=amount_kobo,
            raw_response=data,
        )

    def verify_webhook_signature(
        self,
        raw_body: bytes,
        signature: str,
    ) -> bool:
        """
        Verifies Paga webhook callback signature.
        Paga passes the HMAC-SHA512 in the request header or payload hash field.
        """
        if not self.hmac_key:
            logger.warning("PAGA_HMAC_KEY is empty; cannot verify webhook signature.")
            return False

        computed = hmac.new(
            self.hmac_key.encode("utf-8"),
            raw_body,
            hashlib.sha512,
        ).hexdigest()

        return hmac.compare_digest(computed.lower(), signature.lower())
