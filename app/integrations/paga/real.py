import base64
import hashlib
import hmac
import logging
from typing import Any

import httpx

from app.core.config import settings
from app.core.utils import normalize_phone_number
from app.integrations.paga.base import (
    PagaAdapter,
    PagaEscrowRequestResult,
    PagaPayoutResult,
)

logger = logging.getLogger(__name__)


class RealPagaAdapter(PagaAdapter):
    """
    Live/Sandbox client communicating with Paga APIs.
    - Uses Paga Collect API (https://beta-collect.paga.com / https://collect.paga.com) for escrow payment requests
      with Basic Auth (publicKey:secretKey) and SHA-512 hash header.
    - Uses Paga Business REST API for money transfers/payouts.
    """

    def __init__(self) -> None:
        self.collect_base_url = settings.PAGA_COLLECT_BASE_URL.rstrip("/")
        self.business_base_url = settings.PAGA_SANDBOX_BASE_URL.rstrip("/")
        self.public_key = settings.paga_principal
        self.secret_key = settings.paga_credentials
        self.hash_key = settings.paga_hash_key

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
        """
        Creates an escrow payment request using official Paga Collect API.
        Returns dedicated NUBAN account number and payment URL.
        """
        url = f"{self.collect_base_url}/paymentRequest"
        amount_naira = amount_kobo / 100
        # Format integer if whole, or float
        amount_val = int(amount_naira) if amount_naira.is_integer() else amount_naira
        currency = "NGN"

        # National format or international phone
        clean_phone = normalize_phone_number(client_phone) if client_phone else ""
        if clean_phone.startswith("+234"):
            display_phone = "0" + clean_phone[4:]
        else:
            display_phone = clean_phone

        # Paga Collect hash: referenceNumber + amount + currency + payer.phoneNumber + payer.email + hashKey
        auth_hash = self._compute_hash(reference, amount_val, currency, display_phone, "")

        auth_bytes = f"{self.public_key}:{self.secret_key}".encode()
        basic_token = base64.b64encode(auth_bytes).decode("utf-8")

        payload = {
            "referenceNumber": reference,
            "amount": amount_val,
            "currency": currency,
            "payer": {
                "name": client_name or "Proofwork Client",
                "phoneNumber": display_phone,
            },
            "payee": {
                "name": settings.PROJECT_NAME,
            },
            "payerCollectionFeeShare": 1.0,
            "payeeCollectionFeeShare": 0.0,
            "isSuppressMessages": True,
            "callBackUrl": callback_url,
            "paymentMethods": ["BANK_TRANSFER"],
        }

        headers = {
            "Authorization": f"Basic {basic_token}",
            "hash": auth_hash,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPStatusError as exc:
                err_body = exc.response.text
                logger.error("Paga Collect API Error [%s]: %s", exc.response.status_code, err_body)
                raise ValueError(
                    f"Paga API returned {exc.response.status_code}: {err_body}"
                ) from exc
            except httpx.RequestError as exc:
                logger.error("Paga network error: %s", str(exc))
                raise ValueError(f"Unable to reach Paga service: {exc}") from exc

        # Extract dedicated NUBAN account number from payment methods
        account_number = ""
        bank_name = data.get("bankName") or "Paga"
        payment_methods = data.get("paymentMethods", [])
        for pm in payment_methods:
            props = pm.get("properties", {})
            if "AccountNumber" in props:
                account_number = str(props["AccountNumber"])
                bank_name = props.get("bankName", bank_name)
                break

        payment_url = data.get("paymentUrl") or f"{settings.BASE_URL}/checkout/{reference}"

        return PagaEscrowRequestResult(
            reference=reference,
            payment_url=payment_url,
            account_number=account_number,
            bank_name=bank_name,
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
        url = f"{self.business_base_url}/moneyTransfer"
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
            try:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPStatusError as exc:
                err_body = exc.response.text
                logger.error("Paga API Error [%s]: %s", exc.response.status_code, err_body)
                raise ValueError(
                    f"Paga API returned {exc.response.status_code}: {err_body}"
                ) from exc
            except httpx.RequestError as exc:
                logger.error("Paga network error: %s", str(exc))
                raise ValueError(f"Unable to reach Paga service: {exc}") from exc

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
