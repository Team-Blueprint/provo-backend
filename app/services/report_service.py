import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.enums import DayStatus, JobStatus
from app.core.security import compute_hmac_sha256
from app.db.models import Consent, Job, JobDay, PayerToken, User
from app.domain.scoring import DayScoreInput, calculate_worker_score


def mask_employer_name(full_name: str | None, hash_suffix: str) -> str:
    """
    Masks employer full name to protect consumer PII while preserving recognition:
    e.g. 'Chief Babatunde Adeleke' -> 'C***f B*******e (#81C0)'
    """
    if not full_name or not full_name.strip():
        return f"Client (#{hash_suffix})"

    words = full_name.strip().split()
    masked_words = []
    for w in words:
        if len(w) <= 2:
            masked_words.append(w[0] + "*")
        else:
            masked_words.append(w[0] + ("*" * (len(w) - 2)) + w[-1])

    masked_str = " ".join(masked_words)
    return f"{masked_str} (#{hash_suffix})"


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

        # Fetch all funded, in-progress, or completed jobs for this worker (including multi-worker JobDay rows)
        jobs_stmt = (
            select(Job)
            .outerjoin(JobDay, JobDay.job_id == Job.id)
            .where(
                (Job.worker_id == worker.id) | (JobDay.worker_id == worker.id),
                Job.status.in_([
                    JobStatus.FUNDED.value,
                    JobStatus.IN_PROGRESS.value,
                    JobStatus.COMPLETED.value,
                ]),
            )
            .distinct()
            .options(
                selectinload(Job.days_schedule),
                selectinload(Job.client),
                selectinload(Job.supervisor),
            )
            .order_by(Job.created_at.desc())
        )
        jobs = list((await db.execute(jobs_stmt)).scalars().all())

        # Pre-calculate client job counts to identify new payers across system
        client_ids = {j.client_id for j in jobs}
        client_job_counts: dict[uuid.UUID, int] = {}
        for c_id in client_ids:
            count = (
                await db.execute(select(func.count(Job.id)).where(Job.client_id == c_id))
            ).scalar() or 0
            client_job_counts[c_id] = count

        # Build scoring inputs, itemized jobs list, and payer statistics
        day_inputs: list[DayScoreInput] = []
        payer_map: dict[str, dict[str, Any]] = {}
        jobs_list: list[dict[str, Any]] = []
        total_earned_kobo = 0
        total_settled_days = 0

        for job in jobs:
            payer_token = await self.get_or_create_payer_token(db, job.client_id, worker.id)
            seed = f"{job.client_id}-{worker.id}"
            hash_suffix = hashlib.sha256(seed.encode()).hexdigest()[:4].upper()
            masked_client_name = mask_employer_name(job.client.name if job.client else None, hash_suffix)

            if payer_token not in payer_map:
                payer_map[payer_token] = {
                    "token": payer_token,
                    "masked_name": masked_client_name,
                    "jobs_count": 0,
                    "days_settled": 0,
                    "amount_kobo": 0,
                    "confirmed_days": 0,
                    "auto_released_days": 0,
                }
            payer_map[payer_token]["jobs_count"] += 1

            client_is_new = client_job_counts.get(job.client_id, 0) <= 1
            is_cash_job = (
                job.funding_reference.startswith("CASH")
                or job.funding_reference.startswith("PW-CASH")
            )

            # Filter milestones relevant to this specific worker
            worker_days = [
                d for d in job.days_schedule
                if (d.worker_id == worker.id) or (d.worker_id is None and job.worker_id == worker.id)
            ]

            formatted_days = []
            for day in sorted(worker_days, key=lambda x: x.day_number):
                status_enum = DayStatus(day.status)
                # Resolve outcome: client | supervisor | auto | pending | disputed
                is_auto = (
                    status_enum in (DayStatus.PAID, DayStatus.CONFIRMED, DayStatus.AUTO_RELEASED)
                    and day.release_at is not None
                    and day.decided_by is None
                )
                if is_auto:
                    outcome = "auto"
                elif day.decided_by is not None:
                    if job.supervisor_id is not None and day.decided_by == job.supervisor_id:
                        outcome = "supervisor"
                    else:
                        outcome = "client"
                elif status_enum in (DayStatus.PAID, DayStatus.CONFIRMED):
                    outcome = "client"
                elif status_enum == DayStatus.DISPUTED:
                    outcome = "disputed"
                else:
                    outcome = "pending"

                formatted_days.append({
                    "day_number": day.day_number,
                    "amount_kobo": day.amount_kobo,
                    "status": day.status,
                    "outcome": outcome,
                    "done_at": day.done_at.isoformat() if day.done_at else None,
                    "release_at": day.release_at.isoformat() if day.release_at else None,
                })

                # Only count paid/disputed/active settled days in score
                if status_enum in (DayStatus.PAID, DayStatus.DISPUTED):
                    day_inputs.append(
                        DayScoreInput(
                            job_id=str(job.id),
                            client_id=payer_token,
                            amount_kobo=day.amount_kobo,
                            status=status_enum,
                            date=day.done_at or day.created_at,
                            is_auto_released=is_auto,
                            is_cash=is_cash_job,
                            client_is_new_to_system=client_is_new,
                        )
                    )
                    if status_enum == DayStatus.PAID:
                        total_earned_kobo += day.amount_kobo
                        total_settled_days += 1
                        payer_map[payer_token]["days_settled"] += 1
                        payer_map[payer_token]["amount_kobo"] += day.amount_kobo
                        if is_auto:
                            payer_map[payer_token]["auto_released_days"] += 1
                        else:
                            payer_map[payer_token]["confirmed_days"] += 1

            worker_total_kobo = sum(d.amount_kobo for d in worker_days) if worker_days else job.total_kobo
            jobs_list.append({
                "job_id": str(job.id),
                "client_token": masked_client_name,
                "trade": job.trade,
                "description": job.description,
                "status": job.status,
                "total_kobo": worker_total_kobo,
                "days_count": len(worker_days),
                "created_at": job.created_at.isoformat() if job.created_at else now.isoformat(),
                "days": formatted_days,
            })

        # Execute pure credit scoring engine
        score_result = calculate_worker_score(day_inputs)

        # Single payer concentration check (cap is 35%)
        max_payer_amount = max((p["amount_kobo"] for p in payer_map.values()), default=0)
        concentration_ratio = (
            round(max_payer_amount / total_earned_kobo, 3) if total_earned_kobo > 0 else 0.0
        )
        concentration_flag = concentration_ratio > 0.35

        # Monthly chronological earnings timeline
        monthly_map: dict[str, dict[str, Any]] = {}
        for d_in in day_inputs:
            if d_in.status == DayStatus.PAID:
                ym = d_in.date.strftime("%Y-%m")
                if ym not in monthly_map:
                    monthly_map[ym] = {
                        "month": ym,
                        "label": d_in.date.strftime("%b %Y"),
                        "confirmed_days": 0,
                        "total_kobo": 0,
                    }
                monthly_map[ym]["confirmed_days"] += 1
                monthly_map[ym]["total_kobo"] += d_in.amount_kobo

        active_months = [monthly_map[k] for k in sorted(monthly_map.keys())]

        # Cryptographically signed snapshot
        signed_at = now.isoformat()
        snapshot_payload = f"{worker.id}:{score_result.band}:{score_result.score}:{signed_at}"
        secret = settings.PROOFWORK_HMAC_SECRET or "proofwork-default-hmac-secret"
        signature = compute_hmac_sha256(secret, snapshot_payload)
        signed_snapshot = {
            "worker_id": str(worker.id),
            "standing_tier": score_result.band,
            "standing_score": score_result.score,
            "signals": score_result.reasons,
            "signed_at": signed_at,
            "signature": signature,
        }

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
            "jobs": jobs_list,
            "active_months": active_months,
            "signed_snapshot": signed_snapshot,
        }
