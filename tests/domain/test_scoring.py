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


def test_unique_confirmed_payers_not_raw_payments():
    now = datetime(2026, 6, 15, tzinfo=UTC)
    # 5 confirmed payments from the same client
    history = [
        DayScoreInput(f"job-{i}", "client-single", 50000, DayStatus.CONFIRMED, now)
        for i in range(1, 6)
    ]
    res = calculate_worker_score(history)
    assert res.confirmed_days == 5
    # Unique payers must be exactly 1, not 5
    assert res.unique_payers == 1
    # Payer score is 8 pts (not 5 * 8 = 40 pts)
    assert any("1 unique verified payer" in r for r in res.reasons)


def test_circular_payment_detection_and_penalty():
    now = datetime(2026, 6, 15, tzinfo=UTC)
    history = [
        DayScoreInput("job-1", "client-circular", 100000, DayStatus.CONFIRMED, now),
        DayScoreInput("job-2", "client-normal", 100000, DayStatus.CONFIRMED, now),
    ]
    # Without circular flag
    clean_res = calculate_worker_score(history)
    assert clean_res.circular_flow_detected is False

    # With circular flag
    flagged_res = calculate_worker_score(history, circular_payer_ids={"client-circular"})
    assert flagged_res.circular_flow_detected is True
    assert flagged_res.score == clean_res.score - 20
    assert any("CIRCULAR_FLOW_DETECTED" in r for r in flagged_res.reasons)


def test_new_payer_down_weighting():
    now = datetime(2026, 6, 15, tzinfo=UTC)
    # Established payer
    est_history = [
        DayScoreInput("job-1", "client-1", 100000, DayStatus.CONFIRMED, now, client_is_new_to_system=False),
    ]
    est_res = calculate_worker_score(est_history)

    # New unproven payer with no prior system history
    new_history = [
        DayScoreInput("job-1", "client-1", 100000, DayStatus.CONFIRMED, now, client_is_new_to_system=True),
    ]
    new_res = calculate_worker_score(new_history)

    # Points for new payer should be lower due to 0.7x discount
    assert any("discounted by 30%" in r for r in new_res.reasons)
    assert new_res.score < est_res.score


def test_cash_job_cap_at_twenty_percent():
    now = datetime(2026, 6, 15, tzinfo=UTC)
    # 2 escrow days (+4 pts) + 10 cash days (+20 raw pts, which would exceed 20% cap)
    history = [
        DayScoreInput("job-escrow-1", "client-1", 100000, DayStatus.CONFIRMED, now, is_cash=False),
        DayScoreInput("job-escrow-2", "client-2", 100000, DayStatus.CONFIRMED, now, is_cash=False),
    ]
    for i in range(10):
        history.append(
            DayScoreInput(f"job-cash-{i}", f"client-cash-{i}", 50000, DayStatus.CONFIRMED, now, is_cash=True)
        )

    res = calculate_worker_score(history)
    assert res.cash_days == 10
    # Cash points must be capped and reason logged
    assert any("Cash job contribution capped at 20%" in r for r in res.reasons)

