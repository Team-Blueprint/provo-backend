from dataclasses import dataclass


@dataclass(frozen=True)
class FraudEvaluationResult:
    is_flagged: bool
    flags: list[str]
    severity: str  # "CLEAN", "MEDIUM", "HIGH"


def check_identity_collision(
    client_phone: str,
    worker_phone: str,
    supervisor_phone: str | None = None,
    client_telegram_id: int | None = None,
    worker_telegram_id: int | None = None,
    supervisor_telegram_id: int | None = None,
) -> list[str]:
    """Detects if client, worker, or supervisor share the same phone or Telegram identity."""
    flags: list[str] = []

    clean_client_phone = client_phone.strip()
    clean_worker_phone = worker_phone.strip()

    if clean_client_phone == clean_worker_phone:
        flags.append("COLLISION: Client and worker share the same phone number.")

    if supervisor_phone:
        clean_sup = supervisor_phone.strip()
        if clean_sup == clean_client_phone:
            flags.append("COLLISION: Client and supervisor share the same phone number.")
        if clean_sup == clean_worker_phone:
            flags.append("COLLISION: Worker and supervisor share the same phone number.")

    if client_telegram_id is not None and worker_telegram_id is not None and client_telegram_id == worker_telegram_id:
        flags.append("COLLISION: Client and worker share the same Telegram ID.")

    if supervisor_telegram_id is not None:
        if client_telegram_id is not None and supervisor_telegram_id == client_telegram_id:
            flags.append("COLLISION: Client and supervisor share the same Telegram ID.")
        if worker_telegram_id is not None and supervisor_telegram_id == worker_telegram_id:
            flags.append("COLLISION: Worker and supervisor share the same Telegram ID.")

    return flags


def check_circular_flow(
    funding_account_number: str | None,
    worker_payout_account_number: str | None,
    past_beneficiary_accounts: set[str] | None = None,
) -> list[str]:
    """
    Detects circular money movement:
    1. Funding account matches worker's payout account.
    2. Payer is someone the worker previously sent payouts to.
    """
    flags: list[str] = []

    if funding_account_number and worker_payout_account_number and funding_account_number.strip() == worker_payout_account_number.strip():
        flags.append(
            "CIRCULAR_FLOW: Funding source account matches the worker's destination payout account."
        )

    if funding_account_number and past_beneficiary_accounts and funding_account_number.strip() in past_beneficiary_accounts:
        flags.append(
            "CIRCULAR_FLOW: Payer previously received funds from worker (circular fund routing)."
        )

    return flags


def check_burst_velocity(
    recent_jobs_count_by_payer: int,
    threshold: int = 4,
) -> list[str]:
    """Detects suspicious clustering of multiple jobs funded by the same client in a tight window."""
    flags: list[str] = []
    if recent_jobs_count_by_payer >= threshold:
        flags.append(
            f"VELOCITY_BURST: {recent_jobs_count_by_payer} jobs funded by this payer in rapid succession."
        )
    return flags


def evaluate_fraud_risk(
    client_phone: str,
    worker_phone: str,
    supervisor_phone: str | None = None,
    client_telegram_id: int | None = None,
    worker_telegram_id: int | None = None,
    supervisor_telegram_id: int | None = None,
    funding_account_number: str | None = None,
    worker_payout_account_number: str | None = None,
    past_beneficiary_accounts: set[str] | None = None,
    recent_jobs_count_by_payer: int = 1,
) -> FraudEvaluationResult:
    """Evaluates comprehensive fraud risk across identity, circular flows, and bursts."""
    all_flags: list[str] = []

    all_flags.extend(
        check_identity_collision(
            client_phone=client_phone,
            worker_phone=worker_phone,
            supervisor_phone=supervisor_phone,
            client_telegram_id=client_telegram_id,
            worker_telegram_id=worker_telegram_id,
            supervisor_telegram_id=supervisor_telegram_id,
        )
    )

    all_flags.extend(
        check_circular_flow(
            funding_account_number=funding_account_number,
            worker_payout_account_number=worker_payout_account_number,
            past_beneficiary_accounts=past_beneficiary_accounts,
        )
    )

    all_flags.extend(
        check_burst_velocity(recent_jobs_count_by_payer=recent_jobs_count_by_payer)
    )

    severity = "CLEAN"
    if any("CIRCULAR_FLOW" in f or "COLLISION" in f for f in all_flags):
        severity = "HIGH"
    elif all_flags:
        severity = "MEDIUM"

    return FraudEvaluationResult(
        is_flagged=len(all_flags) > 0,
        flags=all_flags,
        severity=severity,
    )
