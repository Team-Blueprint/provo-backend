import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.enums import PayoutStatus
from app.db.models import Job, JobDay, Payout
from app.domain.lifecycle import DayEvent, transition_day_state
from app.integrations.paga import get_paga_adapter

logger = logging.getLogger(__name__)
paga_adapter = get_paga_adapter()


async def process_pending_payouts(db: AsyncSession, max_batch: int = 20) -> int:
    """
    Processes queued outbox payouts that are in PENDING status or need retry.
    Guarantees idempotency via unique idempotency_ref.
    """
    stmt = (
        select(Payout)
        .where(
            (Payout.status == PayoutStatus.PENDING.value)
            | ((Payout.status == PayoutStatus.FAILED.value) & (Payout.attempts < 5))
        )
        .options(selectinload(Payout.day).selectinload(JobDay.job).selectinload(Job.worker))
        .limit(max_batch)
    )

    pending_payouts = list((await db.execute(stmt)).scalars().all())
    processed_count = 0

    for payout in pending_payouts:
        day = payout.day
        if not day or not day.job:
            continue

        worker = day.job.worker
        destination = worker.payout_account_number if worker and worker.payout_account_number else "9900000001"
        bank_code = worker.payout_bank_code if worker and worker.payout_bank_code else "PAGA"
        recipient_name = worker.name if worker else "Artisan"

        payout.status = PayoutStatus.PROCESSING.value
        payout.attempts += 1
        await db.flush()

        try:
            result = await paga_adapter.disburse_payout(
                payout_ref=payout.idempotency_ref,
                destination_account=destination,
                bank_code=bank_code,
                amount_kobo=payout.amount_kobo,
                recipient_name=recipient_name,
            )
            payout.status = PayoutStatus.SUCCESS.value
            payout.paga_response = result.raw_response
            day.status = transition_day_state(day.status, DayEvent.PAYOUT_SUCCESS).value
            processed_count += 1
            logger.info("Successfully disbursed payout %s for Day %s", payout.idempotency_ref, day.id)
        except Exception as e:
            logger.error("Outbox payout attempt %s failed for %s: %s", payout.attempts, payout.id, e)
            payout.status = PayoutStatus.FAILED.value

    if processed_count > 0:
        await db.commit()

    return processed_count
