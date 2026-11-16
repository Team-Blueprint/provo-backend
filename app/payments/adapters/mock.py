import json
import random
from pathlib import Path

from app.core.config import settings
from app.core.security import verify_hmac_signature
from app.payments.adapters.base import (
    PagaAdapter,
    PaymentRequestResult,
    PersistentAccountResult,
)


class MockPagaAdapter(PagaAdapter):
    """
    Mock implementation of Paga adapter for local development, CI, and test replay.
    Reads canned fixtures when available or falls back to synthetic deterministic data.
    """

    def __init__(self, fixtures_dir: Path | None = None):
        self.fixtures_dir = fixtures_dir or (
            Path(__file__).parent.parent.parent.parent / "tests" / "fixtures" / "paga"
        )

    async def create_persistent_account(
        self,
        worker_id: str,
        name: str,
        phone: str,
        account_reference: str,
    ) -> PersistentAccountResult:
        fixture_file = self.fixtures_dir / "persistent_account_response.json"
        if fixture_file.exists():
            data = json.loads(fixture_file.read_text())
        else:
            # Deterministic mock generation
            seed_num = abs(hash(account_reference)) % 1000000000
            mock_nuban = f"9{seed_num:09d}"[:10]
            data = {
                "responseCode": 0,
                "message": "Persistent payment account successfully created (MOCK)",
                "accountNumber": mock_nuban,
                "accountReference": account_reference,
                "bankName": "Paga / Parkway MFB",
                "accountName": f"Proofwork / {name}",
                "statusCode": 200,
            }

        return PersistentAccountResult(
            account_number=data.get("accountNumber", f"987{random.randint(1000000, 9999999)}"),
            account_reference=account_reference,
            bank_name=data.get("bankName", "Paga Parkway MFB"),
            account_name=data.get("accountName", f"Proofwork / {name}"),
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
        fixture_file = self.fixtures_dir / "payment_request_response.json"
        if fixture_file.exists():
            data = json.loads(fixture_file.read_text())
        else:
            data = {
                "responseCode": 0,
                "message": "Payment request initiated successfully (MOCK)",
                "referenceNumber": reference,
                "paymentUrl": f"https://mock-paga.proofwork.local/pay/{reference}",
                "amount": amount_kobo / 100,
                "currency": currency,
                "statusCode": 200,
            }

        return PaymentRequestResult(
            payment_reference=reference,
            payment_url=data.get("paymentUrl", f"{settings.BASE_URL}/mock-paga/pay/{reference}"),
            amount_kobo=amount_kobo,
            raw_response=data,
        )

    def verify_webhook_signature(
        self,
        raw_body: bytes,
        signature: str,
    ) -> bool:
        # In mock mode, allow "mock_valid_signature" or real HMAC check with configured secret
        if signature in ("mock_valid_signature", "mocked_paga_hash_grade1", "mocked_paga_hash_grade2"):
            return True

        secret = settings.PAGA_HMAC_KEY or settings.PROOFWORK_HMAC_SECRET
        return verify_hmac_signature(secret, raw_body, signature)
