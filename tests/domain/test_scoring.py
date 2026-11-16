from datetime import UTC, datetime, timedelta

from app.core.enums import DayStatus
from app.domain.scoring import DayScoreInput, calculate_worker_score


def test_empty_history_yields_zero():
    res = calculate_worker_score([])
    assert res.score == 0
    assert res.band == "TIER_4_UNVERIFIED"
    assert res.unique_payers == 0


def test_disputed_days_only_yields_zero():
    d = datetime.now(UTC)
    history = [
        DayScoreInput("j1", "c1", 100000, DayStatus.DISPUTED, d),
        DayScoreInput("j2", "c2", 150000, DayStatus.DISPUTED, d),
    ]
    res = calculate_worker_score(history)
    assert res.score == 0
    assert res.disputed_days == 2


def test_confirmed_days_with_repeat_clients_and_active_months():
    now = datetime(2026, 6, 15, tzinfo=UTC)
    last_month = now - timedelta(days=35)

    history = [
        # Client 1: 2 jobs (repeat client), month 1 & month 2
        DayScoreInput("job-1", "client-1", 100000, DayStatus.CONFIRMED, last_month),
        DayScoreInput("job-2", "client-1", 150000, DayStatus.CONFIRMED, now),
        # Client 2: 1 job
        DayScoreInput("job-3", "client-2", 200000, DayStatus.CONFIRMED, now),
        # Client 3: 1 job
        DayScoreInput("job-4", "client-3", 120000, DayStatus.CONFIRMED, now),
    ]

    res = calculate_worker_score(history)
    assert res.confirmed_days == 4
    assert res.unique_payers == 3
    assert res.repeat_payers == 1
    assert res.active_months == 2
    assert res.score >= 50
    assert res.band in ("TIER_2_SOLID", "TIER_1_EXCELLENT")


def test_auto_released_discount_and_alert():
    now = datetime(2026, 6, 15, tzinfo=UTC)
    history = [
        DayScoreInput("job-1", "client-1", 100000, DayStatus.AUTO_RELEASED, now),
        DayScoreInput("job-2", "client-2", 100000, DayStatus.AUTO_RELEASED, now),
        DayScoreInput("job-3", "client-3", 100000, DayStatus.AUTO_RELEASED, now),
    ]
    res = calculate_worker_score(history)
    assert res.auto_released_days == 3
    assert res.auto_release_ratio == 1.0
    # High auto-release warning should be in reasons
    assert any("High auto-release proportion" in r for r in res.reasons)
