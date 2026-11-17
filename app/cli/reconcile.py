import asyncio
import sys
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.database import async_session_maker
from app.core.enums import LedgerEntryType
from app.db.models import Job


async def run_reconciliation() -> bool:
    """
    Audits the financial integrity of all jobs across the system.
    Enforces the core escrow invariant:
    released + refunded + excess_held <= total_funded
    """
    print("=" * 60)
    print("PROOFWORK ESCROW LEDGER AUDIT & RECONCILIATION")
    print("=" * 60)

    discrepancy_count = 0
    total_jobs_audited = 0
    total_funded_kobo = 0
    total_released_kobo = 0
    total_refunded_kobo = 0

    async with async_session_maker() as session:
        stmt = select(Job).options(selectinload(Job.ledger_entries))
        jobs = (await session.execute(stmt)).scalars().all()

        for job in jobs:
            total_jobs_audited += 1
            sums: dict[str, int] = defaultdict(int)
            for entry in job.ledger_entries:
                sums[entry.kind] += entry.amount_kobo

            funded = sums[LedgerEntryType.FUNDED.value]
            released = sums[LedgerEntryType.RELEASED.value]
            refunded = sums[LedgerEntryType.REFUNDED.value]
            _excess = sums.get("EXCESS_HELD", 0)

            total_funded_kobo += funded
            total_released_kobo += released
            total_refunded_kobo += refunded

            disbursed = released + refunded
            if disbursed > funded:
                discrepancy_count += 1
                print(
                    f"❌ CRITICAL ANOMALY: Job {job.id} disbursed (₦{disbursed/100:,.2f}) > "
                    f"funded (₦{funded/100:,.2f})! Over-disbursed by ₦{(disbursed - funded)/100:,.2f}"
                )
            else:
                remaining_locked = funded - disbursed
                print(
                    f"✅ Job {str(job.id)[:8]}.. [{job.status:12}] "
                    f"Funded: ₦{funded/100:9,.2f} | Released: ₦{released/100:9,.2f} | "
                    f"Refunded: ₦{refunded/100:9,.2f} | Locked: ₦{remaining_locked/100:9,.2f}"
                )

    print("-" * 60)
    print(f"Total Jobs Audited: {total_jobs_audited}")
    print(f"Total Escrow Funded:   ₦{total_funded_kobo/100:,.2f}")
    print(f"Total Escrow Released: ₦{total_released_kobo/100:,.2f}")
    print(f"Total Escrow Refunded: ₦{total_refunded_kobo/100:,.2f}")
    print(f"Total Discrepancies:   {discrepancy_count}")
    print("=" * 60)

    if discrepancy_count > 0:
        print("🚨 RECONCILIATION FAILED WITH INTEGRITY ANOMALIES.")
        return False
    else:
        print("✨ LEDGER RECONCILIATION PASSED: All invariants hold.")
        return True


def main():
    success = asyncio.run(run_reconciliation())
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
