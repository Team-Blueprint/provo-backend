import hashlib
import hmac
import uuid

from app.core.config import settings
from app.integrations.paga.base import (
    PagaAdapter,
    PagaEscrowRequestResult,
    PagaPayoutResult,
)


class MockPagaAdapter(PagaAdapter):
    """
    Mock implementation simulating Paga Sandbox Collect and Payout responses.
    Used for local testing, demo mode, and offline development.
    """

    async def create_escrow_payment_request(
        self,
        reference: str,
        amount_kobo: int,
        client_phone: str,
        client_name: str,
        description: str,
        callback_url: str,
    ) -> PagaEscrowRequestResult:
        # Deterministic or realistic mock virtual NUBAN
        suffix = reference.replace("-", "")[:6].upper()
        account_number = f"9900{suffix[:6].zfill(6)}"
        payment_url = f"https://beta.mypaga.com/pay/{reference}"

        raw_response = {
            "responseCode": "0",
            "message": "Payment request created successfully",
            "referenceNumber": reference,
            "accountNumber": account_number,
            "bankName": "Paga MFB",
            "paymentUrl": payment_url,
            "amount": amount_kobo / 100.0,
            "currency": "NGN",
        }

        return PagaEscrowRequestResult(
            reference=reference,
            payment_url=payment_url,
            account_number=account_number,
            bank_name="Paga MFB",
            amount_kobo=amount_kobo,
            raw_response=raw_response,
        )

    async def disburse_payout(
        self,
        payout_ref: str,
        destination_account: str,
        bank_code: str,
        amount_kobo: int,
        recipient_name: str,
    ) -> PagaPayoutResult:
        transaction_id = f"PAGA-TX-{uuid.uuid4().hex[:12].upper()}"

        raw_response = {
            "responseCode": "0",
            "message": "Payout successful",
            "transactionId": transaction_id,
            "referenceNumber": payout_ref,
            "destinationAccount": destination_account,
            "destinationBank": bank_code,
            "amount": amount_kobo / 100.0,
            "status": "SUCCESS",
        }

        return PagaPayoutResult(
            payout_ref=payout_ref,
            transaction_id=transaction_id,
            destination_account=destination_account,
            amount_kobo=amount_kobo,
            status="SUCCESS",
            raw_response=raw_response,
        )

    def verify_webhook_signature(
        self,
        raw_body: bytes,
        signature: str,
    ) -> bool:
        """Validates signature using configured PAGA_HMAC_KEY."""
        key = settings.PAGA_HMAC_KEY or "mock-hmac-key-test"
        computed = hmac.new(key.encode("utf-8"), raw_body, hashlib.sha512).hexdigest()
        return hmac.compare_digest(computed.lower(), signature.strip().lower())
