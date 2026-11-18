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
    is_cash: bool = False
    is_auto_released: bool = False


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
    circular_flow_detected: bool = False
    cash_days: int = 0


def calculate_worker_score(
    history: list[DayScoreInput],
    circular_payer_ids: set[str] | None = None,
) -> WorkerScoreResult:
    """
    Pure scoring function evaluating an artisan's credit readiness.
    Rules:
    1. Base verification: 15 points if at least one confirmed/auto-released day exists.
    2. Count unique confirmed payers instead of raw payments (8 pts per unique payer, max 24 pts).
    3. Weight repeat clients with multiple distinct completed jobs (12 pts per repeat client, max 24 pts).
    4. Flag payments that circle between the same few accounts (-20 pts penalty and warning).
    5. Give less weight (30% discount / 0.7x) to payers with no other history in the system.
    6. Cap the share of cash jobs at 20% of total work milestone points.
    7. Single-payer cap: No single client can supply more than 35% of work points.
    8. Auto-released days count at discounted weight (1 pt vs 2 pts for active confirmation).
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
            circular_flow_detected=False,
            cash_days=0,
        )

    reasons: list[str] = []
    confirmed_days = 0
    auto_released_days = 0
    disputed_days = 0
    cash_days_count = 0

    payer_job_map: dict[str, set[str]] = defaultdict(set)
    payer_escrow_points: dict[str, float] = defaultdict(float)
    payer_cash_points: dict[str, float] = defaultdict(float)
    active_months_set: set[str] = set()

    circular_set = circular_payer_ids or set()
    circular_detected = False
    new_payer_discount_applied = False

    for item in history:
        if item.is_cash:
            cash_days_count += 1

        if item.client_id in circular_set:
            circular_detected = True

        is_auto = (item.status == DayStatus.AUTO_RELEASED) or item.is_auto_released

        if is_auto:
            auto_released_days += 1
            pts = 1.0  # Silence is not confirmation (discounted)
            if item.client_is_new_to_system:
                pts *= 0.7
                new_payer_discount_applied = True

            if item.is_cash:
                payer_cash_points[item.client_id] += pts
            else:
                payer_escrow_points[item.client_id] += pts

            payer_job_map[item.client_id].add(item.job_id)
            active_months_set.add(item.date.strftime("%Y-%m"))

        elif item.status in (DayStatus.CONFIRMED, DayStatus.PAID):
            confirmed_days += 1
            pts = 2.0
            if item.client_is_new_to_system:
                pts *= 0.7  # 30% discount for payers with no other system history
                new_payer_discount_applied = True

            if item.is_cash:
                payer_cash_points[item.client_id] += pts
            else:
                payer_escrow_points[item.client_id] += pts

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
            circular_flow_detected=circular_detected,
            cash_days=cash_days_count,
        )

    base_score = 15.0
    reasons.append("Base verified escrow score: +15 pts")

    if new_payer_discount_applied:
        reasons.append("Milestone points for new/unproven employer(s) discounted by 30%.")

    # Combine payer points and enforce single-payer cap (max 35% from any single client)
    all_client_ids = set(payer_escrow_points.keys()) | set(payer_cash_points.keys())
    raw_escrow_total = sum(payer_escrow_points.values())
    raw_cash_total = sum(payer_cash_points.values())
    raw_total = raw_escrow_total + raw_cash_total

    max_single_allowed = raw_total * 0.35 if raw_total > 0 else 0
    capped_escrow_points = 0.0
    capped_cash_points = 0.0

    for client_id in all_client_ids:
        client_escrow = payer_escrow_points.get(client_id, 0.0)
        client_cash = payer_cash_points.get(client_id, 0.0)
        client_pts = client_escrow + client_cash

        if raw_total > 25 and client_pts > max_single_allowed:
            ratio = max_single_allowed / client_pts
            capped_escrow_points += client_escrow * ratio
            capped_cash_points += client_cash * ratio
            reasons.append(f"Client {client_id[:8]} contributions capped at 35% limit.")
        else:
            capped_escrow_points += client_escrow
            capped_cash_points += client_cash

    # Cap the share of cash jobs at 20% of work milestone score
    # Cash points cannot exceed 25% of escrow points (so cash is at most 20% of total)
    max_cash_allowed = capped_escrow_points * 0.25 if capped_escrow_points > 0 else 0.0
    if capped_cash_points > max_cash_allowed:
        reasons.append(f"Cash job contribution capped at 20% limit (excess {round(capped_cash_points - max_cash_allowed, 1)} pts excluded).")
        effective_cash_points = max_cash_allowed
    else:
        effective_cash_points = capped_cash_points

    day_score = min(capped_escrow_points + effective_cash_points, 35.0)
    reasons.append(
        f"Work days score: +{round(day_score, 1)} pts ({confirmed_days} confirmed, {auto_released_days} auto-released)"
    )

    # 1. Unique confirmed payers score (count unique confirmed payers instead of raw payments, max 24 pts)
    unique_payers = len(payer_job_map)
    payer_score = min(unique_payers * 8.0, 24.0)
    reasons.append(f"{unique_payers} unique verified payer(s): +{payer_score} pts")

    # 2. Repeat clients score (clients with >= 2 distinct completed jobs, max 24 pts)
    repeat_payers = sum(1 for jobs in payer_job_map.values() if len(jobs) >= 2)
    repeat_score = min(repeat_payers * 12.0, 24.0)
    if repeat_payers > 0:
        reasons.append(f"{repeat_payers} repeat client(s) with multiple jobs: +{repeat_score} pts")

    # 3. Active months score (up to 15 pts)
    active_months = len(active_months_set)
    months_score = min(active_months * 5.0, 15.0)
    reasons.append(f"{active_months} active earning month(s): +{months_score} pts")

    # 4. Circular payment deduction
    circular_penalty = 0.0
    if circular_detected:
        circular_penalty = 20.0
        reasons.append("⚠️ CIRCULAR_FLOW_DETECTED: Payments circling between related accounts detected (-20 pts penalty).")

    # Total score calculation
    raw_score = base_score + day_score + payer_score + repeat_score + months_score - circular_penalty
    total_score = round(max(0, min(raw_score, 100)))

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
        circular_flow_detected=circular_detected,
        cash_days=cash_days_count,
    )
