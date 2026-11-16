import json
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import (
    JobStatus,
    PaymentEventStatus,
    VerificationGrade,
)
from app.jobs.models import Confirmation, Job
from app.messaging.models import MessageLog
from app.messaging.service import MessagingService
from app.payments.adapters.mock import MockPagaAdapter
from app.payments.models import PaymentEvent
from app.payments.service import PaymentService
from app.workers.models import Worker


@pytest.mark.asyncio
async def test_paga_webhook_grade1_link_flow(db_session: AsyncSession):
    # 1. Setup Worker
    worker = Worker(
        name="Emem Okon",
        phone="+2348012345678",
        trade="Bricklayer",
        paga_account_number="9876543210",
        paga_account_reference="PW-ACC-EMEM-001",
    )
    db_session.add(worker)
    await db_session.flush()

    messaging_service = MessagingService()
    payment_service = PaymentService(adapter=MockPagaAdapter(), messaging_service=messaging_service)

    # 2. Ingest Grade 1 Callback
    fixtures_dir = Path(__file__).parent / "fixtures" / "paga"
    fixture_path = fixtures_dir / "deposit_callback_grade1.json"
    payload = json.loads(fixture_path.read_text())
    raw_body = fixture_path.read_bytes()

    event = await payment_service.handle_paga_callback(
        db=db_session,
        raw_body=raw_body,
        signature="mocked_paga_hash_grade1",
        payload=payload,
    )

    # 3. Assertions on PaymentEvent
    assert event is not None
    assert event.paga_transaction_id == "PAGA-TXN-10001"
    assert event.amount_kobo == 2500000
    assert event.grade == VerificationGrade.GRADE_1_LINK
    assert event.status == PaymentEventStatus.PROCESSED

    # 4. Assert Job is in AWAITING_CONFIRMATION
    job_stmt = select(Job).where(Job.worker_id == worker.id)
    job = (await db_session.execute(job_stmt)).scalar_one_or_none()
    assert job is not None
    assert job.status == JobStatus.AWAITING_CONFIRMATION
    assert job.client_phone == "+2348031234567"

    # 5. Assert Confirmation Token exists and prompt was queued
    conf_stmt = select(Confirmation).where(Confirmation.job_id == job.id)
    conf = (await db_session.execute(conf_stmt)).scalar_one_or_none()
    assert conf is not None
    assert len(conf.confirmation_token) > 10

    # 6. Assert MessageLog has confirmation prompt sent to client
    msg_stmt = select(MessageLog).where(MessageLog.to_phone == "+2348031234567")
    client_msg = (await db_session.execute(msg_stmt)).scalars().first()
    assert client_msg is not None
    assert "Did Emem Okon do" in client_msg.content


@pytest.mark.asyncio
async def test_paga_webhook_grade2_bank_transfer_flow(db_session: AsyncSession):
    # 1. Setup Worker
    worker = Worker(
        name="Emem Okon",
        phone="+2348012345678",
        trade="Bricklayer",
        paga_account_number="9876543210",
        paga_account_reference="PW-ACC-EMEM-001",
    )
    db_session.add(worker)
    await db_session.flush()

    messaging_service = MessagingService()
    payment_service = PaymentService(adapter=MockPagaAdapter(), messaging_service=messaging_service)

    # 2. Ingest Grade 2 Direct Transfer Callback
    fixtures_dir = Path(__file__).parent / "fixtures" / "paga"
    fixture_path = fixtures_dir / "deposit_callback_grade2.json"
    payload = json.loads(fixture_path.read_text())
    raw_body = fixture_path.read_bytes()

    event = await payment_service.handle_paga_callback(
        db=db_session,
        raw_body=raw_body,
        signature="mocked_paga_hash_grade2",
        payload=payload,
    )

    # 3. Assertions
    assert event.grade == VerificationGrade.GRADE_2_TRANSFER
    assert event.amount_kobo == 4000000

    job_stmt = select(Job).where(Job.payment_event_id == event.id)
    job = (await db_session.execute(job_stmt)).scalar_one_or_none()
    assert job is not None
    assert job.grade == VerificationGrade.GRADE_2_TRANSFER
    assert job.client_phone == "+2348099887766"


@pytest.mark.asyncio
async def test_paga_webhook_duplicate_replay_is_idempotent(db_session: AsyncSession):
    worker = Worker(
        name="Emem Okon",
        phone="+2348012345678",
        trade="Bricklayer",
        paga_account_number="9876543210",
        paga_account_reference="PW-ACC-EMEM-001",
    )
    db_session.add(worker)
    await db_session.flush()

    payment_service = PaymentService(adapter=MockPagaAdapter())
    fixtures_dir = Path(__file__).parent / "fixtures" / "paga"
    fixture_path = fixtures_dir / "deposit_callback_grade1.json"
    payload = json.loads(fixture_path.read_text())
    raw_body = fixture_path.read_bytes()

    # Call 1
    event1 = await payment_service.handle_paga_callback(
        db=db_session,
        raw_body=raw_body,
        signature="mocked_paga_hash_grade1",
        payload=payload,
    )

    # Call 2 with identical transactionId (simulating retry from Paga)
    event2 = await payment_service.handle_paga_callback(
        db=db_session,
        raw_body=raw_body,
        signature="mocked_paga_hash_grade1",
        payload=payload,
    )

    assert event1.id == event2.id
    # Assert total events stored is exactly 1
    count_stmt = select(PaymentEvent).where(PaymentEvent.paga_transaction_id == "PAGA-TXN-10001")
    events = (await db_session.execute(count_stmt)).scalars().all()
    assert len(events) == 1
