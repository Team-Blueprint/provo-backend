from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import (
    JobStatus,
    JobType,
    MessageChannel,
    MessageDirection,
    MessageStatus,
    VerificationGrade,
)
from app.core.security import generate_secure_token
from app.jobs.models import Confirmation, Job
from app.messaging.models import MessageLog
from app.messaging.service import MessagingService
from app.workers.models import Worker


@pytest.mark.asyncio
async def test_httpsms_send_sms_and_logging(db_session: AsyncSession):
    service = MessagingService()

    log_entry = await service.send_sms(
        db=db_session,
        to_phone="08031234567",
        content="Hello from Proofwork",
    )

    assert log_entry.id is not None
    assert log_entry.channel == MessageChannel.HTTPSMS
    assert log_entry.direction == MessageDirection.OUTBOUND
    assert log_entry.to_phone == "+2348031234567"
    assert log_entry.status == MessageStatus.SENT


@pytest.mark.asyncio
async def test_httpsms_two_way_inbound_confirm_flow(db_session: AsyncSession):
    # 1. Setup Worker & Pending Job
    worker = Worker(name="Emem Okon", phone="+2348012345678", trade="Bricklayer")
    db_session.add(worker)
    await db_session.flush()

    job = Job(
        worker_id=worker.id,
        amount_kobo=2000000,
        job_type=JobType.BRICKLAYING,
        status=JobStatus.AWAITING_CONFIRMATION,
        grade=VerificationGrade.GRADE_1_LINK,
        client_phone="+2348031234567",
    )
    db_session.add(job)
    await db_session.flush()

    conf = Confirmation(
        job_id=job.id,
        confirmation_token=generate_secure_token(24),
        channel=MessageChannel.HTTPSMS,
        is_confirmed=False,
        expires_at=datetime.now(UTC) + timedelta(hours=48),
    )
    db_session.add(conf)
    await db_session.flush()

    # 2. Simulate httpSMS inbound webhook: Client replies "1" to confirm
    service = MessagingService()
    webhook_payload = {
        "id": "evt-123456",
        "type": "message.phone.received",
        "data": {
            "contact": "+2348031234567",
            "owner": "+2348000000000",
            "content": "1",
            "message_id": "msg-inbound-001",
        },
    }

    log_entry = await service.handle_httpsms_webhook(db=db_session, payload=webhook_payload)
    assert log_entry is not None
    assert log_entry.direction == MessageDirection.INBOUND

    # 3. Verify Job transitioned to CONFIRMED
    job_stmt = select(Job).where(Job.id == job.id)
    updated_job = (await db_session.execute(job_stmt)).scalar_one()
    assert updated_job.status == JobStatus.CONFIRMED

    # 4. Verify confirmation record marked confirmed
    conf_stmt = select(Confirmation).where(Confirmation.job_id == job.id)
    updated_conf = (await db_session.execute(conf_stmt)).scalar_one()
    assert updated_conf.is_confirmed is True


@pytest.mark.asyncio
async def test_messaging_deduplication(db_session: AsyncSession):
    service = MessagingService()
    payload = {
        "id": "evt-999",
        "type": "message.phone.received",
        "data": {
            "contact": "+2348031234567",
            "content": "YES",
            "message_id": "duplicate-msg-id",
        },
    }

    # Ingest once
    log1 = await service.handle_httpsms_webhook(db=db_session, payload=payload)
    # Ingest duplicate
    log2 = await service.handle_httpsms_webhook(db=db_session, payload=payload)

    assert log1.id == log2.id

    stmt = select(MessageLog).where(MessageLog.message_id == "duplicate-msg-id")
    records = (await db_session.execute(stmt)).scalars().all()
    assert len(records) == 1
