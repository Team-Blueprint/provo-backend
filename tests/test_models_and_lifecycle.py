import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import JobStatus, JobType, VerificationGrade
from app.jobs.service import JobService
from app.payments.adapters.mock import MockPagaAdapter
from app.payments.service import PaymentService
from app.reports.schemas import ShareLinkCreate
from app.reports.service import ReportService
from app.workers.schemas import WorkerCreate
from app.workers.service import WorkerService


@pytest.mark.asyncio
async def test_worker_registration_and_paga_account(db_session: AsyncSession):
    payment_service = PaymentService(adapter=MockPagaAdapter())
    worker_service = WorkerService(payment_service=payment_service)

    worker = await worker_service.register_worker(
        db=db_session,
        data=WorkerCreate(
            name="Emem Okon",
            phone="08012345678",  # Should be normalized to +2348012345678
            trade="Bricklayer",
        ),
    )

    assert worker.id is not None
    assert worker.phone == "+2348012345678"
    assert worker.paga_account_number is not None
    assert len(worker.paga_account_number) == 10


@pytest.mark.asyncio
async def test_job_lifecycle_draft_to_confirmation_and_receipt(db_session: AsyncSession):
    # 1. Register worker
    payment_service = PaymentService(adapter=MockPagaAdapter())
    worker_service = WorkerService(payment_service=payment_service)
    worker = await worker_service.register_worker(
        db=db_session,
        data=WorkerCreate(name="Emem Okon", phone="+2348012345678", trade="Bricklayer"),
    )

    # 2. Worker logs cash job -> Must be DRAFT, Grade 3
    job_service = JobService()
    job, conf = await job_service.create_cash_job_draft(
        db=db_session,
        worker_id=worker.id,
        amount_kobo=3000000,  # ₦30,000.00
        client_phone="08098765432",
        job_type=JobType.BRICKLAYING,
    )
    assert job.status == JobStatus.DRAFT
    assert job.grade == VerificationGrade.GRADE_3_CASH
    assert job.client_phone == "+2348098765432"
    assert conf.is_confirmed is False

    # 3. Client confirms job -> status becomes CONFIRMED, receipt minted
    confirmed_job, receipt = await job_service.confirm_job(
        db=db_session,
        confirmation_token=conf.confirmation_token,
        job_type=JobType.BRICKLAYING,
    )
    assert confirmed_job.status == JobStatus.CONFIRMED
    assert receipt.receipt_number.startswith("PW-")
    assert receipt.receipt_data["amount_formatted"] == "₦30,000.00"
    assert receipt.receipt_data["worker_name"] == "Emem Okon"


@pytest.mark.asyncio
async def test_lender_report_tokenization_and_scoring(db_session: AsyncSession):
    # 1. Setup worker & confirmed jobs
    payment_service = PaymentService(adapter=MockPagaAdapter())
    worker_service = WorkerService(payment_service=payment_service)
    worker = await worker_service.register_worker(
        db=db_session,
        data=WorkerCreate(name="Emem Okon", phone="+2348012345678", trade="Bricklayer"),
    )

    job_service = JobService()

    # Confirmed Job 1 from Client A
    _job1, conf1 = await job_service.create_cash_job_draft(
        db=db_session,
        worker_id=worker.id,
        amount_kobo=2000000,
        client_phone="+2348033333333",
        job_type=JobType.BRICKLAYING,
    )
    await job_service.confirm_job(db=db_session, confirmation_token=conf1.confirmation_token)

    # Confirmed Job 2 from Client A (Repeat client)
    _job2, conf2 = await job_service.create_cash_job_draft(
        db=db_session,
        worker_id=worker.id,
        amount_kobo=2500000,
        client_phone="+2348033333333",
        job_type=JobType.BRICKLAYING,
    )
    await job_service.confirm_job(db=db_session, confirmation_token=conf2.confirmation_token)

    # Unconfirmed Draft from Client B (Must NOT count)
    await job_service.create_cash_job_draft(
        db=db_session,
        worker_id=worker.id,
        amount_kobo=5000000,
        client_phone="+2348044444444",
        job_type=JobType.BRICKLAYING,
    )

    # 2. Generate ShareLink
    report_service = ReportService()
    share_link = await report_service.create_share_link(
        db=db_session,
        data=ShareLinkCreate(worker_id=worker.id, expiry_days=7),
    )

    # 3. View Lender Report
    report = await report_service.get_lender_report_by_token(
        db=db_session,
        share_token=share_link.token,
    )

    # Total confirmed jobs should be 2 (draft ignored)
    assert report.total_confirmed_jobs == 2
    assert report.unique_payers_count == 1
    assert report.repeat_clients_count == 1
    assert report.total_confirmed_kobo == 4500000

    # Ensure phone number is HMAC tokenized (NOT plain text)
    assert len(report.tokenized_payers) == 1
    payer_summary = report.tokenized_payers[0]
    assert payer_summary.payer_token.startswith("Client-")
    assert "+2348033333333" not in payer_summary.payer_token
