import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.enums import JobStatus, VerificationGrade
from app.core.security import generate_payer_token, generate_secure_token
from app.core.utils import kobo_to_naira_str
from app.jobs.models import Job
from app.reports.models import AuditLog, Consent, ShareLink
from app.reports.schemas import (
    ConsentCreate,
    LenderReportResponse,
    ShareLinkCreate,
    TokenizedPayerSummary,
)

logger = logging.getLogger(__name__)


def calculate_credit_score(confirmed_jobs: list[Job]) -> tuple[int, list[str]]:
    """
    Pure function: takes confirmed jobs and returns (score, reasons).
    Rules:
    1. Base score = 20 (if at least one confirmed job exists)
    2. Unique payers: +10 pts per unique payer (up to 30 pts)
    3. Repeat clients: +15 pts per repeat client with >= 2 confirmed jobs (up to 30 pts)
    4. Active months: +10 pts per active month (up to 20 pts)
    5. Cash cap: Cash jobs cannot contribute more than 30% of total score
    """
    if not confirmed_jobs:
        return 0, ["No confirmed job history available."]

    reasons: list[str] = []
    base_score = 20
    reasons.append("Base verification score: +20 pts")

    # 1. Payer analysis
    payer_job_counts: dict[str, int] = defaultdict(int)
    payer_amounts: dict[str, int] = defaultdict(int)
    active_months_set: set[str] = set()
    total_kobo = 0
    cash_kobo = 0

    for job in confirmed_jobs:
        phone = job.client_phone
        payer_job_counts[phone] += 1
        payer_amounts[phone] += job.amount_kobo
        total_kobo += job.amount_kobo
        if job.grade == VerificationGrade.GRADE_3_CASH:
            cash_kobo += job.amount_kobo

        job_month = job.created_at.strftime("%Y-%m")
        active_months_set.add(job_month)

    unique_payers = len(payer_job_counts)
    repeat_clients = sum(1 for count in payer_job_counts.values() if count >= 2)
    active_months = len(active_months_set)

    # Unique payers score (max 30)
    payer_score = min(unique_payers * 10, 30)
    reasons.append(f"{unique_payers} unique verified payer(s): +{payer_score} pts")

    # Repeat clients score (max 30)
    repeat_score = min(repeat_clients * 15, 30)
    if repeat_clients > 0:
        reasons.append(f"{repeat_clients} repeat client(s) with multiple confirmed jobs: +{repeat_score} pts")

    # Active months score (max 20)
    months_score = min(active_months * 10, 20)
    reasons.append(f"{active_months} active earning month(s): +{months_score} pts")

    subtotal = base_score + payer_score + repeat_score + months_score

    # Cash share cap check
    cash_ratio = (cash_kobo / total_kobo) if total_kobo > 0 else 0.0
    if cash_ratio > 0.5:
        reasons.append(f"High cash proportion ({cash_ratio:.0%}); score adjusted to prevent over-reliance on cash.")
        subtotal = min(subtotal, 70)

    final_score = min(max(subtotal, 0), 100)
    return final_score, reasons


class ReportService:
    async def create_share_link(
        self,
        db: AsyncSession,
        data: ShareLinkCreate,
    ) -> ShareLink:
        """Generates an expiring share link for lenders."""
        token = generate_secure_token(24)
        expires_at = datetime.now(UTC) + timedelta(days=data.expiry_days)

        share_link = ShareLink(
            worker_id=data.worker_id,
            token=token,
            is_revoked=False,
            expires_at=expires_at,
        )
        db.add(share_link)
        await db.flush()

        logger.info("Created ShareLink for worker %s, expires %s", data.worker_id, expires_at)
        return share_link

    async def create_consent(
        self,
        db: AsyncSession,
        data: ConsentCreate,
    ) -> Consent:
        """Records explicit worker consent under NDPA guidelines."""
        expires_at = datetime.now(UTC) + timedelta(days=data.expiry_days)
        consent = Consent(
            worker_id=data.worker_id,
            purpose=data.purpose,
            expires_at=expires_at,
        )
        db.add(consent)
        await db.flush()
        return consent

    async def get_lender_report_by_token(
        self,
        db: AsyncSession,
        share_token: str,
    ) -> LenderReportResponse:
        """
        Retrieves a verified lender report using a shared token.
        Tokenizes client phone numbers via HMAC to protect privacy.
        """
        stmt = (
            select(ShareLink)
            .where(ShareLink.token == share_token)
            .options(selectinload(ShareLink.worker))
        )
        share_link = (await db.execute(stmt)).scalar_one_or_none()

        if not share_link or share_link.is_revoked:
            raise ValueError("Invalid or revoked share link")

        now = datetime.now(UTC)
        if share_link.expires_at < now:
            raise ValueError("Share link has expired")

        # Increment access count & log audit
        share_link.access_count += 1
        audit = AuditLog(
            entity_name="share_links",
            entity_id=str(share_link.id),
            action="VIEW_LENDER_REPORT",
            actor="LENDER",
        )
        db.add(audit)

        worker = share_link.worker

        # Fetch only CONFIRMED jobs (Strict rule: drafts/disputed excluded)
        job_stmt = (
            select(Job)
            .where(Job.worker_id == worker.id, Job.status == JobStatus.CONFIRMED)
            .order_by(Job.created_at.asc())
        )
        confirmed_jobs = list((await db.execute(job_stmt)).scalars().all())

        score, reasons = calculate_credit_score(confirmed_jobs)

        # Aggregate statistics and tokenized payers
        payer_map: dict[str, list[Job]] = defaultdict(list)
        total_kobo = 0
        cash_kobo = 0
        active_months = set()

        for j in confirmed_jobs:
            payer_map[j.client_phone].append(j)
            total_kobo += j.amount_kobo
            if j.grade == VerificationGrade.GRADE_3_CASH:
                cash_kobo += j.amount_kobo
            active_months.add(j.created_at.strftime("%Y-%m"))

        tokenized_payers: list[TokenizedPayerSummary] = []
        for raw_phone, jobs in payer_map.items():
            payer_token = generate_payer_token(
                worker_id=str(worker.id),
                payer_phone=raw_phone,
                hmac_secret=settings.PROOFWORK_HMAC_SECRET,
            )
            tokenized_payers.append(
                TokenizedPayerSummary(
                    payer_token=payer_token,
                    job_count=len(jobs),
                    first_payment_date=jobs[0].created_at.strftime("%Y-%m-%d"),
                    last_payment_date=jobs[-1].created_at.strftime("%Y-%m-%d"),
                    phone_verified=any(job.grade != VerificationGrade.GRADE_3_CASH for job in jobs),
                )
            )

        unique_payers_count = len(payer_map)
        repeat_clients_count = sum(1 for jobs in payer_map.values() if len(jobs) >= 2)
        cash_percentage = (cash_kobo / total_kobo * 100) if total_kobo > 0 else 0.0

        await db.flush()

        return LenderReportResponse(
            worker_id=worker.id,
            worker_name=worker.name,
            trade=worker.trade,
            score=score,
            score_reasons=reasons,
            total_confirmed_jobs=len(confirmed_jobs),
            total_confirmed_kobo=total_kobo,
            total_confirmed_naira=kobo_to_naira_str(total_kobo),
            unique_payers_count=unique_payers_count,
            repeat_clients_count=repeat_clients_count,
            active_months_count=len(active_months),
            cash_percentage=round(cash_percentage, 1),
            tokenized_payers=tokenized_payers,
            generated_at=now.isoformat(),
        )
