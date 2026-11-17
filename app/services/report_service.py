import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.enums import DayStatus, JobStatus
from app.db.models import Consent, Job, PayerToken, User
from app.domain.scoring import DayScoreInput, calculate_worker_score


class ReportService:
    """Service for generating NDPA-compliant consented lender verification reports."""

    async def create_worker_consent(
        self,
        db: AsyncSession,
        worker_id: uuid.UUID,
        purpose: str = "Lender Credit Evaluation",
        valid_days: int = 7,
    ) -> Consent:
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(UTC) + timedelta(days=valid_days)

        consent = Consent(
            worker_id=worker_id,
            token=token,
            purpose=purpose,
            expires_at=expires_at,
            is_revoked=False,
            access_count=0,
            granted_at=datetime.now(UTC),
        )
        db.add(consent)
        await db.commit()
        await db.refresh(consent)
        return consent

    async def get_or_create_payer_token(
        self,
        db: AsyncSession,
        client_id: uuid.UUID,
        worker_id: uuid.UUID,
    ) -> str:
        """Deterministic pseudonym for client to protect PII while proving payer diversity."""
        stmt = select(PayerToken).where(
            PayerToken.client_id == client_id,
            PayerToken.worker_id == worker_id,
        )
        existing = (await db.execute(stmt)).scalar_one_or_none()
        if existing:
            return existing.payer_token

        # Deterministic short token e.g. "Client-A8F4"
        seed = f"{client_id}-{worker_id}"
        hash_suffix = hashlib.sha256(seed.encode()).hexdigest()[:4].upper()
        token_str = f"Client-{hash_suffix}"

        token_record = PayerToken(
            client_id=client_id,
            worker_id=worker_id,
            payer_token=token_str,
        )
        db.add(token_record)
        await db.flush()
        return token_str

    async def generate_lender_report(
        self,
        db: AsyncSession,
        token: str,
    ) -> dict[str, Any] | None:
        """
        Validates the consent token, gathers verified work history,
        tokenizes payers, and computes the pure credit-readiness score.
        """
        stmt = select(Consent).where(Consent.token == token)
        consent = (await db.execute(stmt)).scalar_one_or_none()

        if not consent:
            return None

        now = datetime.now(UTC)
        expires_at = consent.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)

        if consent.is_revoked or expires_at < now:
            return {
                "error": "CONSENT_EXPIRED_OR_REVOKED",
                "is_expired": expires_at < now,
                "is_revoked": consent.is_revoked,
            }

        # Increment access count
        consent.access_count += 1
        await db.commit()

        # Fetch worker with profile
        worker_stmt = (
            select(User)
            .where(User.id == consent.worker_id)
            .options(selectinload(User.worker_profile))
        )
        worker = (await db.execute(worker_stmt)).scalar_one_or_none()
        if not worker:
            return None

        # Fetch all funded/completed jobs for this worker with their days
        jobs_stmt = (
            select(Job)
            .where(
                Job.worker_id == worker.id,
                Job.status.in_([JobStatus.FUNDED.value, JobStatus.COMPLETED.value]),
            )
            .options(selectinload(Job.days_schedule))
            .order_by(Job.created_at.desc())
        )
        jobs = (await db.execute(jobs_stmt)).scalars().all()

        # Build scoring inputs and payer statistics
        day_inputs: list[DayScoreInput] = []
        payer_map: dict[str, dict[str, Any]] = {}
        total_earned_kobo = 0
        total_settled_days = 0

        for job in jobs:
            payer_token = await self.get_or_create_payer_token(db, job.client_id, worker.id)
            if payer_token not in payer_map:
                payer_map[payer_token] = {
                    "token": payer_token,
                    "jobs_count": 0,
                    "days_settled": 0,
                    "amount_kobo": 0,
                }
            payer_map[payer_token]["jobs_count"] += 1

            for day in job.days_schedule:
                status_enum = DayStatus(day.status)
                # Only count paid/disputed/active settled days in score
                if status_enum in (DayStatus.PAID, DayStatus.DISPUTED):
                    day_inputs.append(
                        DayScoreInput(
                            job_id=str(job.id),
                            client_id=payer_token,
                            amount_kobo=day.amount_kobo,
                            status=status_enum,
                            date=day.done_at or day.created_at,
                        )
                    )
                    if status_enum == DayStatus.PAID:
                        total_earned_kobo += day.amount_kobo
                        total_settled_days += 1
                        payer_map[payer_token]["days_settled"] += 1
                        payer_map[payer_token]["amount_kobo"] += day.amount_kobo

        # Execute pure credit scoring engine
        score_result = calculate_worker_score(day_inputs)

        # Single payer concentration check (cap is 35%)
        max_payer_amount = max((p["amount_kobo"] for p in payer_map.values()), default=0)
        concentration_ratio = (
            round(max_payer_amount / total_earned_kobo, 3) if total_earned_kobo > 0 else 0.0
        )
        concentration_flag = concentration_ratio > 0.35

        profile = worker.worker_profile
        trade = profile.trade if profile else "Artisan"
        area = profile.area if profile else "Nigeria"

        return {
            "status": "VALID",
            "consent": {
                "token": consent.token,
                "purpose": consent.purpose,
                "granted_at": consent.granted_at.isoformat(),
                "expires_at": consent.expires_at.isoformat(),
                "access_count": consent.access_count,
            },
            "artisan": {
                "id": str(worker.id),
                "name": worker.name,
                "trade": trade,
                "area": area,
            },
            "credit_score": {
                "score": score_result.score,
                "band": score_result.band,
                "reasons": score_result.reasons,
                "auto_release_ratio": score_result.auto_release_ratio,
            },
            "earnings_summary": {
                "total_earned_kobo": total_earned_kobo,
                "total_earned_naira": f"{total_earned_kobo / 100:,.2f}",
                "total_settled_days": total_settled_days,
                "confirmed_days": score_result.confirmed_days,
                "auto_released_days": score_result.auto_released_days,
                "disputed_days": score_result.disputed_days,
            },
            "payer_diversity": {
                "unique_payers_count": len(payer_map),
                "repeat_payers_count": score_result.repeat_payers,
                "max_single_payer_concentration": concentration_ratio,
                "exceeds_35_percent_concentration": concentration_flag,
                "payers": list(payer_map.values()),
            },
        }
