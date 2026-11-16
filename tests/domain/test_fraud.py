from app.domain.fraud import (
    check_burst_velocity,
    check_circular_flow,
    check_identity_collision,
    evaluate_fraud_risk,
)


def test_identity_collision_phone_match():
    flags = check_identity_collision(
        client_phone="+2348011112222",
        worker_phone="+2348011112222",
    )
    assert len(flags) == 1
    assert "Client and worker share the same phone number" in flags[0]


def test_identity_collision_telegram_match():
    flags = check_identity_collision(
        client_phone="+2348011111111",
        worker_phone="+2348022222222",
        client_telegram_id=999888,
        worker_telegram_id=999888,
    )
    assert len(flags) == 1
    assert "Client and worker share the same Telegram ID" in flags[0]


def test_circular_flow_same_account():
    flags = check_circular_flow(
        funding_account_number="0123456789",
        worker_payout_account_number="0123456789",
    )
    assert len(flags) == 1
    assert "Funding source account matches" in flags[0]


def test_circular_flow_past_beneficiary():
    flags = check_circular_flow(
        funding_account_number="9988776655",
        worker_payout_account_number="1122334455",
        past_beneficiary_accounts={"9988776655", "5544332211"},
    )
    assert len(flags) == 1
    assert "Payer previously received funds from worker" in flags[0]


def test_burst_velocity():
    flags = check_burst_velocity(recent_jobs_count_by_payer=5, threshold=4)
    assert len(flags) == 1
    assert "VELOCITY_BURST" in flags[0]


def test_clean_risk_profile():
    res = evaluate_fraud_risk(
        client_phone="+2348011111111",
        worker_phone="+2348022222222",
        funding_account_number="1111222233",
        worker_payout_account_number="4444555566",
        recent_jobs_count_by_payer=1,
    )
    assert not res.is_flagged
    assert res.severity == "CLEAN"
    assert len(res.flags) == 0
