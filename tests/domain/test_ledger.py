import pytest

from app.domain.ledger import (
    LedgerInvariantViolationError,
    partition_daily_pay,
    validate_ledger_invariant,
)


def test_partition_daily_pay_clean_division():
    # 30,000 across 3 days -> [10000, 10000, 10000]
    schedule = partition_daily_pay(total_kobo=3000000, days=3)
    assert schedule == [1000000, 1000000, 1000000]
    assert sum(schedule) == 3000000


def test_partition_daily_pay_odd_division_puts_remainder_on_last_day():
    # 10,000 kobo across 3 days -> 3333, 3333, 3334
    schedule = partition_daily_pay(total_kobo=10000, days=3)
    assert schedule == [3333, 3333, 3334]
    assert sum(schedule) == 10000


def test_partition_daily_pay_single_day():
    schedule = partition_daily_pay(total_kobo=500000, days=1)
    assert schedule == [500000]


def test_partition_daily_pay_invalid_args():
    with pytest.raises(ValueError):
        partition_daily_pay(total_kobo=0, days=3)

    with pytest.raises(ValueError):
        partition_daily_pay(total_kobo=5000, days=0)


def test_validate_ledger_invariant_valid_cases():
    # Released + refunded <= funded
    assert validate_ledger_invariant(funded_kobo=100000, released_kobo=30000, refunded_kobo=0)
    assert validate_ledger_invariant(funded_kobo=100000, released_kobo=70000, refunded_kobo=30000)

    # When completed, released + refunded == funded
    assert validate_ledger_invariant(
        funded_kobo=100000, released_kobo=80000, refunded_kobo=20000, is_completed=True
    )


def test_validate_ledger_invariant_violations():
    # Released + refunded > funded
    with pytest.raises(LedgerInvariantViolationError):
        validate_ledger_invariant(funded_kobo=100000, released_kobo=80000, refunded_kobo=30000)

    # Negative values
    with pytest.raises(LedgerInvariantViolationError):
        validate_ledger_invariant(funded_kobo=100000, released_kobo=-10, refunded_kobo=0)

    # Completed job mismatch
    with pytest.raises(LedgerInvariantViolationError):
        validate_ledger_invariant(
            funded_kobo=100000, released_kobo=70000, refunded_kobo=0, is_completed=True
        )
