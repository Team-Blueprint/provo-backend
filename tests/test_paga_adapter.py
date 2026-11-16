import json
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import PaymentEventStatus, VerificationGrade
from app.payments.adapters.mock import MockPagaAdapter
from app.payments.adapters.real import RealPagaAdapter
from app.payments.service import PaymentService
from app.workers.models import Worker


@pytest.mark.asyncio
async def test_mock_paga_adapter_create_account():
    adapter = MockPagaAdapter()
    res = await adapter.create_persistent_account(
        worker_id="test-worker-1",
        name="Emem Okon",
        phone="+2348012345678",
        account_reference="PW-ACC-EMEM-001",
    )
    assert res.account_number is not None
    assert len(res.account_number) >= 10
    assert res.account_reference == "PW-ACC-EMEM-001"
    assert "Emem Okon" in res.account_name


@pytest.mark.asyncio
async def test_mock_paga_adapter_create_payment_request():
    adapter = MockPagaAdapter()
    res = await adapter.create_payment_request(
        reference="PW-REQ-001",
        amount_kobo=2500000,
        currency="NGN",
        payer_phone="+2348031234567",
        payer_name="Chief Bassey",
        description="Plumbing services",
        callback_url="http://localhost:8000/payments/paga/callback",
    )
    assert res.payment_reference == "PW-REQ-001"
    assert "pay" in res.payment_url.lower()


def test_real_paga_adapter_signature_verification():
    adapter = RealPagaAdapter(
        principal="test_principal",
        credentials="test_credentials",
        hmac_key="my_secret_paga_key",
    )

    raw_body = b'{"transactionId": "12345", "amount": 5000}'
    import hashlib
    import hmac

    valid_sig = hmac.new(b"my_secret_paga_key", raw_body, hashlib.sha512).hexdigest()

    assert adapter.verify_webhook_signature(raw_body, valid_sig) is True
    assert adapter.verify_webhook_signature(raw_body, "tampered_signature") is False


@pytest.mark.asyncio
async def test_paga_callback_idempotency_and_grade_classification(db_session: AsyncSession):
    # 1. Seed worker
    worker = Worker(
        name="Emem Okon",
        phone="+2348012345678",
        trade="Bricklayer",
        paga_account_number="9876543210",
        paga_account_reference="PW-ACC-EMEM-001",
    )
    db_session.add(worker)
    await db_session.flush()

    service = PaymentService(adapter=MockPagaAdapter())

    fixtures_dir = Path(__file__).parent / "fixtures" / "paga"
    fixture_path = fixtures_dir / "deposit_callback_grade1.json"
    payload = json.loads(fixture_path.read_text())
    raw_body = fixture_path.read_bytes()

    # 2. First callback execution: should create PaymentEvent
    event1 = await service.handle_paga_callback(
        db=db_session,
        raw_body=raw_body,
        signature="mocked_paga_hash_grade1",
        payload=payload,
    )
    assert event1 is not None
    assert event1.paga_transaction_id == "PAGA-TXN-10001"
    assert event1.grade == VerificationGrade.GRADE_1_LINK
    assert event1.status == PaymentEventStatus.PROCESSED

    # 3. Duplicate callback execution: should detect duplicate and return existing event
    event2 = await service.handle_paga_callback(
        db=db_session,
        raw_body=raw_body,
        signature="mocked_paga_hash_grade1",
        payload=payload,
    )
    assert event2.id == event1.id
    assert event2.paga_transaction_id == event1.paga_transaction_id
