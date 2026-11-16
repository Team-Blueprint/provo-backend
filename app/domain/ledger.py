class LedgerInvariantViolationError(ValueError):
    """Raised when internal ledger movements violate balance integrity."""


def partition_daily_pay(total_kobo: int, days: int) -> list[int]:
    """
    Pure function partitioning total job kobo evenly across days.
    Any fractional kobo remainder from integer division is placed on the final day,
    guaranteeing sum(schedule) == total_kobo exactly.
    """
    if total_kobo <= 0:
        raise ValueError("Total kobo must be greater than zero")
    if days < 1:
        raise ValueError("Job must have at least 1 day")

    base = total_kobo // days
    remainder = total_kobo % days

    schedule = [base] * days
    schedule[-1] += remainder

    assert sum(schedule) == total_kobo, "Integrity failure: partitioned sum must equal total_kobo"
    return schedule


def validate_ledger_invariant(
    funded_kobo: int,
    released_kobo: int,
    refunded_kobo: int,
    is_completed: bool = False,
) -> bool:
    """
    Enforces the Proofwork escrow ledger invariant:
    1. released + refunded must NEVER exceed funded.
    2. When a job is COMPLETED, released + refunded must equal funded exactly.
    """
    if funded_kobo < 0 or released_kobo < 0 or refunded_kobo < 0:
        raise LedgerInvariantViolationError("Ledger figures cannot be negative")

    disbursed = released_kobo + refunded_kobo
    if disbursed > funded_kobo:
        raise LedgerInvariantViolationError(
            f"Ledger invariant violated: Disbursed ({disbursed}) exceeds funded ({funded_kobo})"
        )

    if is_completed and disbursed != funded_kobo:
        raise LedgerInvariantViolationError(
            f"Completed job ledger mismatch: Total disbursed ({disbursed}) does not equal funded ({funded_kobo})"
        )

    return True
