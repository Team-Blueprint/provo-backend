from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from app.core.enums import DayStatus


@dataclass(frozen=True)
class DayScoreInput:
    """Input representation of a settled or reported work day."""
    job_id: str
    client_id: str
    amount_kobo: int
    status: DayStatus
    date: datetime
    client_is_new_to_system: bool = False


@dataclass(frozen=True)
class WorkerScoreResult:
    score: int
    band: str
    reasons: list[str]
    total_days: int
    confirmed_days: int
    auto_released_days: int
    disputed_days: int
    unique_payers: int
    repeat_payers: int
    active_months: int
    auto_release_ratio: float


def calculate_worker_score(history: list[DayScoreInput]) -> WorkerScoreResult:
    """
    Pure scoring function evaluating an artisan's credit readiness.
    Rules (from Proofwork 16 Nov 2026 spec):
    1. Base verification: 15 points if at least one confirmed/auto-released day exists.
    2. Confirmed days count in full (2 pts each, up to 25 pts).
    3. Auto-released days count at lower weight (1 pt each, up to 10 pts).
    4. Disputed days count for zero.
    5. Unique verified payers: +8 pts per unique payer (up to 24 pts).
    6. Repeat clients (>= 2 jobs): +12 pts per repeat client (up to 24 pts).
    7. Active earning months: +5 pts per month (up to 15 pts).
    8. Single-payer cap: No single client can supply more than 35% of the points.
    9. Down-weight new payers with no prior system history.
    """
    if not history:
        return WorkerScoreResult(
            score=0,
            band="TIER_4_UNVERIFIED",
            reasons=["No work history recorded."],
            total_days=0,
            confirmed_days=0,
            auto_released_days=0,
            disputed_days=0,
            unique_payers=0,
            repeat_payers=0,
            active_months=0,
            auto_release_ratio=0.0,
        )

    reasons: list[str] = []
    confirmed_days = 0
    auto_released_days = 0
    disputed_days = 0

    payer_job_map: dict[str, set[str]] = defaultdict(set)
    payer_points: dict[str, float] = defaultdict(float)
    active_months_set: set[str] = set()

    for item in history:
        # Check valid day status
        if item.status in (DayStatus.CONFIRMED, DayStatus.PAID):
            confirmed_days += 1
            pts = 2.0
            if item.client_is_new_to_system:
                pts *= 0.8  # Down-weight new single-job payers
            payer_points[item.client_id] += pts
            payer_job_map[item.client_id].add(item.job_id)
            active_months_set.add(item.date.strftime("%Y-%m"))
        elif item.status == DayStatus.AUTO_RELEASED:
            auto_released_days += 1
            pts = 1.0  # Silence is not confirmation (discounted)
            payer_points[item.client_id] += pts
            payer_job_map[item.client_id].add(item.job_id)
            active_months_set.add(item.date.strftime("%Y-%m"))
        elif item.status == DayStatus.DISPUTED:
            disputed_days += 1
            # Disputed days count for zero

    total_valid_days = confirmed_days + auto_released_days
    if total_valid_days == 0:
        return WorkerScoreResult(
            score=0,
            band="TIER_4_UNVERIFIED",
            reasons=["All recorded days are currently disputed or unconfirmed."],
            total_days=len(history),
            confirmed_days=0,
            auto_released_days=0,
            disputed_days=disputed_days,
            unique_payers=0,
            repeat_payers=0,
            active_months=0,
            auto_release_ratio=0.0,
        )

    base_score = 15.0
    reasons.append("Base verified escrow score: +15 pts")

    # Enforce single payer cap (max 35% of total points from any single payer)
    raw_payer_sum = sum(payer_points.values())
    max_single_allowed = raw_payer_sum * 0.35 if raw_payer_sum > 0 else 0
    capped_day_points = 0.0

    for client_id, pts in payer_points.items():
        if raw_payer_sum > 25 and pts > max_single_allowed:
            capped_day_points += max_single_allowed
            reasons.append(f"Client {client_id[:8]} contributions capped at 35% limit.")
        else:
            capped_day_points += pts

    day_score = min(capped_day_points, 35.0)
    reasons.append(
        f"Work days score: +{round(day_score, 1)} pts ({confirmed_days} confirmed, {auto_released_days} auto-released)"
    )

    # Unique payers score (up to 24 pts)
    unique_payers = len(payer_job_map)
    payer_score = min(unique_payers * 8.0, 24.0)
    reasons.append(f"{unique_payers} unique verified payer(s): +{payer_score} pts")

    # Repeat clients score (up to 24 pts)
    repeat_payers = sum(1 for jobs in payer_job_map.values() if len(jobs) >= 2)
    repeat_score = min(repeat_payers * 12.0, 24.0)
    if repeat_payers > 0:
        reasons.append(f"{repeat_payers} repeat client(s) with multiple jobs: +{repeat_score} pts")

    # Active months score (up to 15 pts)
    active_months = len(active_months_set)
    months_score = min(active_months * 5.0, 15.0)
    reasons.append(f"{active_months} active earning month(s): +{months_score} pts")

    # Total score calculation
    total_score = round(base_score + day_score + payer_score + repeat_score + months_score)
    total_score = max(0, min(total_score, 100))

    # Auto-release proportion alert
    auto_release_ratio = (auto_released_days / total_valid_days) if total_valid_days > 0 else 0.0
    if auto_release_ratio > 0.5:
        reasons.append(
            f"High auto-release proportion ({auto_release_ratio:.0%}): Most work days released due to client silence."
        )

    # Band assignment
    if total_score >= 80:
        band = "TIER_1_EXCELLENT"
    elif total_score >= 60:
        band = "TIER_2_SOLID"
    elif total_score >= 40:
        band = "TIER_3_DEVELOPING"
    else:
        band = "TIER_4_UNVERIFIED"

    return WorkerScoreResult(
        score=total_score,
        band=band,
        reasons=reasons,
        total_days=len(history),
        confirmed_days=confirmed_days,
        auto_released_days=auto_released_days,
        disputed_days=disputed_days,
        unique_payers=unique_payers,
        repeat_payers=repeat_payers,
        active_months=active_months,
        auto_release_ratio=round(auto_release_ratio, 2),
    )
