# Proofwork Escrow Money Flow & Funding Edge Cases

> **Backend Handoff Spec** · Aligned with [Proofwork escrow-backed work records for informal workers.docx](../Proofwork%20escrow-backed%20work%20records%20for%20informal%20workers.docx) (16 Nov 2026 revision).

---

## 1. Core Principle

- **Upfront lock:** A client locks the **full job total** in Paga escrow before Day 1 begins.
- **One-time NUBAN per job:** Each job gets its own one-time 10-digit virtual NUBAN + payment link (replacing the 13 Nov persistent per-worker NUBAN).
- **Virtual routing, single pool:** The per-job NUBAN is a virtual routing address pointing into Proofwork's Paga merchant/escrow balance, tagged to `job_id` so every incoming transfer is reconciled 1:1 without relying on transfer narration.
- **Integer kobo everywhere:** All money math (`total_kobo`, `job_days.amount_kobo`, `payments_in.amount_kobo`, `ledger_entries.amount_kobo`, `payouts.amount_kobo`) uses integer kobo. Daily pay is `total_kobo // days` with any remainder placed on the last day so `SUM(job_days.amount_kobo) == job.total_kobo`.

---

## 2. End-to-End Money Flow

```
Client App (OPay / UBA / Access / GTBank / Kuda)
   │
   │ 1. NIBSS NIP Transfer (or Paga Checkout Link)
   ▼
One-Time Virtual NUBAN (mapped 1:1 to job_id)
   │
   │ 2. Settlement into Proofwork Paga Escrow Balance
   │    + POST /webhooks/paga (signature verified, idempotent on paga_event_id)
   ▼
Proofwork Escrow Ledger (job.status = FUNDED, NUBAN deactivated)
   │
   │ 3. Worker marks Day N "Done" -> 24h confirmation window starts
   │
   ├──▶ Client / Supervisor confirms ──▶ day.status = CONFIRMED (full score weight)
   │
   ├──▶ 24h elapses with no dispute ──▶ day.status = AUTO_RELEASED (lower score weight)
   │
   └──▶ Client / Supervisor disputes ──▶ day.status = DISPUTED (held for admin resolution)
   │
   │ 4. Outbox Payout Worker (idempotency_key = payout.reference)
   ▼
Worker's Paga Account (day.status = PAID)
```

### Step-by-Step Lifecycle

1. **Create Job (`POST /jobs`)**
   - Client selects trade (from fixed enum, never free text), worker (from directory or phone invite), `total_kobo`, `days`, and optional `supervisor_id`.
   - Backend creates `jobs` row (`AWAITING_FUNDING`) and `job_days` rows (`PENDING`, `1..days`).

2. **Request Funding (`POST /jobs/{id}/fund-request`)**
   - Backend calls Paga Collect / Payment Request API with `job_id` reference and `total_kobo`.
   - Stores `funding_reference` on `jobs` and returns the **one-time 10-digit virtual NUBAN** + **Paga payment link**.
   - Client pays from **any Nigerian bank or wallet (OPay, UBA, Access, GTBank, Kuda, etc.)** over standard NIBSS NIP bank transfer, or via the Paga link.

3. **Receive Paga Webhook (`POST /webhooks/paga`)**
   - **Verify signature first** before parsing business logic; reject invalid signatures (`401`/`403`).
   - **Idempotency:** Insert into `payments_in` with a unique constraint on Paga's `event_id`. Duplicate deliveries return `200 OK` without side effects.
   - **Reconcile cumulative amount:** Compute `received_kobo = SUM(payments_in.amount_kobo WHERE job_id = job.id)` and apply the funding rules in Section 4.

4. **Daily Release & Payout (`POST /jobs/{id}/days/{n}/done|confirm|dispute`)**
   - Worker marks Day `n` done → `DONE_REPORTED`, sets `release_at = done_at + 24h` (configurable via env var for demo).
   - Client or Supervisor confirms → `CONFIRMED`; or scheduler (`SELECT ... FOR UPDATE SKIP LOCKED`) moves past-due `DONE_REPORTED` days → `AUTO_RELEASED`.
   - **Outbox pattern:** State transition to `CONFIRMED` or `AUTO_RELEASED` and creation of the `payouts` row + `RELEASED` ledger entry happen in **one DB transaction**.
   - Payout worker sends `day.amount_kobo` to `worker.payout_account` using `payout.reference` as the Paga idempotency key, retrying with backoff on transient failure, and marks the day `PAID` on success.

---

## 3. Why Not Direct Debit for Job Funding?

- **Breaks upfront escrow:** Pulling day-by-day via NIBSS e-Mandate risks insufficient funds (NSF) or mandate revocation mid-job, putting workers back into unpaid-work risk.
- **High setup friction:** Casual clients (homeowners, shop owners) will not complete BVN/bank mandate authorization for a 2–5 day job when a 10-digit NUBAN transfer takes 15 seconds.
- **Fintech wallet gaps:** Many clients pay from OPay/PalmPay/Kuda; NIP bank transfers to a virtual NUBAN work across 100% of banks and mobile wallets.
- **Where Direct Debit fits later:** Scheduled **loan repayments** from worker earnings once a licensed lender partner is connected.

---

## 4. Handling Underfunding & Overfunding

Never evaluate a webhook payment in isolation; always evaluate **cumulative received funds** for the job:

```python
received_kobo = sum(p.amount_kobo for p in job.payments_in)
```

### Case A: Exact Match (`received_kobo == job.total_kobo`)
- Move `job.status` from `AWAITING_FUNDING` → `FUNDED`.
- Write `ledger_entries(kind="FUNDED", amount_kobo=job.total_kobo)`.
- **Deactivate/close** the one-time virtual NUBAN so no stray transfers arrive later.
- Notify worker (Telegram/SMS) and client.

### Case B: Underfunding (`0 < received_kobo < job.total_kobo`)
*Common causes: client mistypes amount, splits transfer across two banks (e.g., ₦25k OPay + ₦17k UBA), or bank deducts a fee.*
- Keep `job.status = AWAITING_FUNDING` (worker **cannot** start or mark days done).
- Record the `payments_in` row and a `PARTIAL_FUNDED` (or `FUNDED_IN`) ledger entry so every kobo is tracked.
- Notify the client immediately: *"Received ₦X of ₦Y. Send the remaining ₦(Y - X) to the same account number before [expiry] to lock and start the job."*
- **If remaining funds arrive before expiry:** Total reaches `job.total_kobo` → transition to `FUNDED` and deactivate NUBAN.
- **If funding window expires (e.g. 24h):** Transition `job.status = CANCELLED`, deactivate NUBAN, and refund `received_kobo` to the client (`ledger_entries(kind="REFUNDED", amount_kobo=received_kobo)`).

### Case C: Overfunding (`received_kobo > job.total_kobo`)
*Common causes: client sends ₦45,000 for a ₦42,000 job, or sends ₦42,000 twice during a bank-app timeout.*
- **Lock the job immediately** (`AWAITING_FUNDING` → `FUNDED`) for `job.total_kobo` and **deactivate the NUBAN immediately** to block further transfers.
- **Never inflate `job_days.amount_kobo`** with the excess (that corrupts the agreed daily rate and distorts the worker's Profile Score).
- Record two ledger entries:
  - `FUNDED`: `job.total_kobo` (backs the daily schedule)
  - `EXCESS_HELD`: `received_kobo - job.total_kobo`
- **Refund `excess_kobo = received_kobo - job.total_kobo`:**
  - If Paga's callback includes the sender's account number + bank code, queue an automatic refund payout (`REFUNDED`) for `excess_kobo`.
  - If Paga's callback omits sender bank details, mark `excess_status = REFUND_PENDING` and prompt the client on Telegram/Web to confirm their refund account in one tap.

### Ledger Invariant (Must Be Property-Tested)
For every job at all times:
```text
SUM(RELEASED) + SUM(REFUNDED) <= SUM(payments_in)
```
And when a job reaches a terminal state (`COMPLETED` or `CANCELLED` with all disputes/refunds settled):
```text
SUM(RELEASED) + SUM(REFUNDED) == SUM(payments_in)
```

---

## 5. Paga Sandbox Verification Checklist

Before finalizing the real `PagaClient` implementation in `app/integrations/`, confirm these in Paga's Collect & Business REST sandbox:
1. **Exact-amount flag:** Does the Collect/Payment Request API support rejecting mismatched amounts at transfer time (`isAmountFixed`), or can any amount land on the virtual NUBAN? *(Keep Case B & C handlers regardless as a safety net.)*
2. **Webhook payload & signature:** What exact header/scheme signs `POST /webhooks/paga` (e.g., `x-paga-hash-v3` SHA-512), and does the payload include `payerName`, `payerPhone`, and `payerAccountNumber` / `sourceBankCode` for automated refunds?
3. **NUBAN deactivation:** Which endpoint closes/expires a one-time payment request or virtual account immediately once `FUNDED`?
4. **Payouts in sandbox:** Which Paga endpoint disburses daily tranches to a worker's Paga account, and does sandbox allow live payout testing or require a simulated adapter behind the same interface?
