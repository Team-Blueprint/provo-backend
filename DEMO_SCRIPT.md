# Proofwork Demonstration Walkthrough Script
*5-Minute Judge & Evaluator Presentation Guide*

---

## 🎯 The Core Problem & Proofwork Solution
- **The Problem:** 80%+ of Nigerian blue-collar artisans (bricklayers, plumbers, carpenters) are locked out of credit. Banks and microfinance lenders consider informal workers "unbankable" because cash self-entries or unverified bank receipts are easily fabricated.
- **The Proofwork Rule:** **Worker self-entry never creates a verified record.** Only money locked in **Paga Escrow** upfront by real clients and released day-by-day upon completed work creates immutable proof of income.

---

## 🚀 5-Minute Live Demo Flow

### Step 1: Open the Verified Lender Report Portal
👉 Open in browser: `http://localhost:8000/reports/DEMO-LENDER-TOKEN-2026/view`

**What to highlight to judges:**
1. **NDPA-Compliant Consented Sharing:** Emem Bassey granted a 7-day token to a microfinance institution.
2. **Deterministic Tokenized Payers (`Client-XXXX`):** Protects client PII while proving **payer diversity** to lenders.
3. **Credit Scoring Factors:**
   - Evaluates repeat clients (Tunde hired Emem twice across 4 months).
   - Shows confirmed vs auto-released days (auto-released days receive a 0.5x penalty to prevent rubber-stamping).
   - **35% Concentration Cap:** Ensures the artisan is not economically reliant on a single employer.

---

### Step 2: Show the Client Escrow Checkout View
👉 Open in browser: `http://localhost:8000/checkout/1eccb1c7-88c7-4620-8dcd-7148f17e4884`

**What to highlight:**
1. Shows one-time **10-digit virtual NUBAN** powered by Paga.
2. Shows client that funds are 100% locked in escrow and only released daily upon inspection.
3. Demonstrates split cumulative funding (e.g. ₦25,000 + ₦17,000 = ₦42,000 total).

---

### Step 3: Multi-Channel Fallback Architecture (USSD / WhatsApp / Telegram)
👉 Open in browser: `http://localhost:8000/demo/channels`

**What to highlight:**
1. **Telegram Bot:** Primary smartphone interface with inline review cards (`[✅ Confirm Release]` and `[⚠️ Dispute]`).
2. **USSD (*384*99#):** Operates on basic 2G feature phones without internet.
3. **SMS OTP Authentication:** Passwordless signup and login via phone number + SMS OTP (`httpSMS`).

---

### Step 4: Live Ledger Invariant Verification (CLI)
Run in terminal:
```bash
python -m app.cli.reconcile
```
**Output:**
```
============================================================
PROOFWORK ESCROW LEDGER AUDIT & RECONCILIATION
============================================================
✅ Job 9024454c.. [COMPLETED   ] Funded: ₦60,000.00 | Released: ₦60,000.00 | Refunded: ₦0.00 | Locked: ₦0.00
✅ Job 06bba352.. [COMPLETED   ] Funded: ₦48,000.00 | Released: ₦48,000.00 | Refunded: ₦0.00 | Locked: ₦0.00
✅ Job 01462980.. [COMPLETED   ] Funded: ₦70,000.00 | Released: ₦70,000.00 | Refunded: ₦0.00 | Locked: ₦0.00
✅ Job 66624554.. [COMPLETED   ] Funded: ₦45,000.00 | Released: ₦45,000.00 | Refunded: ₦0.00 | Locked: ₦0.00
✅ Job 1eccb1c7.. [FUNDED      ] Funded: ₦30,000.00 | Released: ₦     0.00 | Refunded: ₦0.00 | Locked: ₦30,000.00
------------------------------------------------------------
Total Jobs Audited: 5
Total Escrow Funded:   ₦253,000.00
Total Escrow Released: ₦223,000.00
Total Escrow Refunded: ₦0.00
Total Discrepancies:   0
============================================================
✨ LEDGER RECONCILIATION PASSED: All invariants hold.
```
