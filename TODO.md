# Proofwork: Escrow-Backed Work Records for Informal Workers
### Master Implementation TODO & Build Plan (Revised 16 November 2026)

---

## Pre-Requisites & External Services Setup
- [ ] Paga Sandbox credentials (Collect API, Payment Requests, Payout API, SHA-512 HMAC key)
- [ ] Telegram Bot creation via @BotFather (`aiogram 3`, get Bot Token & set Webhook Secret)
- [ ] httpSMS API key & sender phone number (used for artisan/supervisor SMS invitation deep links)
- [ ] Configure environment variables in `.env` (Paga, Telegram, httpSMS, Proofwork HMAC secret)

---

## Phase 0: Project Architecture & Repository Layout
- [x] Reorganize codebase into target architectural layout:
  - `app/domain/`: Pure Python domain logic (state machines, ledger rules, scoring, fraud checks)
  - `app/services/`: Use-case services (job, funding, release, payouts, reports, auth)
  - `app/integrations/`: External adapters (Paga Collect/Payout, Telegram bot, httpSMS)
  - `app/api/`: FastAPI routers (jobs, workers, webhooks, reports, admin, auth)
  - `app/workers/`: 24-hour auto-release scheduler & payout outbox processor
  - `app/db/`: SQLAlchemy models, session factory, migrations
- [x] Clean up single root `main.py` entrypoint (FastAPI app, lifespan, routers, Telegram webhook mount)
- [ ] Setup Docker Compose: PostgreSQL, FastAPI API service, Redis / background worker

---

## Phase 1: Database Models & Alembic Migrations (`app/db/`)
- [x] `auth_otps`: ID (UUID), phone (indexed), code_hash (SHA-256), purpose (`SIGNUP`, `LOGIN`, `PHONE_VERIFY`), attempts counter, is_used, expires_at, created_at
- [x] `users`: ID (UUID), Telegram user ID (indexed), verified phone (E.164, unique), name, is_phone_verified, role flags (`is_client`, `is_worker`, `is_supervisor`, `is_admin`), payout account number/bank
- [x] `worker_profiles`: Worker user ID (FK to users), trade (from enum list: Bricklaying, Plumbing, Electrical, etc.), area, is_available flag (with extension points for worker-specific onboarding fields)
- [x] `client_profiles`: Client user ID (FK to users) (with extension points for client-specific onboarding fields)
- [x] `jobs`: Client ID, worker ID, optional supervisor ID, trade, description, `total_kobo`, `days`, status (`DRAFT`, `AWAITING_FUNDING`, `FUNDED`, `IN_PROGRESS`, `COMPLETED`, `CANCELLED`, `DISPUTED`), funding reference
- [x] `job_days`: Job ID, day number (1..N), `amount_kobo`, status (`PENDING`, `DONE_REPORTED`, `CONFIRMED`, `AUTO_RELEASED`, `DISPUTED`, `PAID`), `done_at`, `release_at`, `decided_by`, `decided_at`
- [x] `payments_in`: Paga event ID (unique constraint), job ID, `amount_kobo`, payer name, phone, account number, raw payload
- [x] `ledger_entries`: Job ID, optional day ID, kind (`FUNDED`, `RELEASED`, `REFUNDED`), `amount_kobo`, `created_at`
- [x] `payouts`: Day ID, `amount_kobo`, idempotency reference (unique), status (`PENDING`, `PROCESSING`, `SUCCESS`, `FAILED`), attempts, Paga response
- [x] `disputes`: Day ID, opened_by, reason, status (`OPEN`, `RESOLVED`), resolution, resolved_by
- [x] `receipts`: Receipt ID (`PW-YYYY-XXXX`), job ID, issued_at, receipt metadata
- [x] `payer_tokens`: Client ID, token used in lender reports (HMAC scoped per worker)
- [x] `consents`: Worker ID, purpose, expiry, granted_at, revoked_at
- [x] `client_notices`: Record that client was notified that payment is recorded
- [x] `audit_events`: Entity name, entity ID, action, actor, timestamp, change details
- [x] Generate clean Alembic migration for updated schema and test upgrade/downgrade

---

## Phase 2: Domain Layer (Pure Python, Zero DB/Network) (`app/domain/`)
- [x] State Machine transitions:
  - `transition_job_state(current_state, event)` enforcing valid paths (`DRAFT` -> `AWAITING_FUNDING` -> `FUNDED` -> `IN_PROGRESS` -> `COMPLETED`, exits `CANCELLED`, `DISPUTED`)
  - `transition_day_state(current_state, event)` enforcing valid paths (`PENDING` -> `DONE_REPORTED` -> `CONFIRMED` / `AUTO_RELEASED` -> `PAID`, exit `DISPUTED`)
  - Invariant: Disallowed transitions raise explicit ValueError; every transition logs to `audit_events`
- [x] Daily pay partition calculation:
  - Pure calculation: `daily_pay = total_kobo // days`, remainder assigned to final day
  - Sum of daily pay schedule equals `total_kobo` exactly
- [x] Ledger rules:
  - Ledger invariant validator: $\text{released} + \text{refunded} \le \text{funded}$ at all times
  - On completed job: $\text{released} + \text{refunded} == \text{funded}$
- [x] Scoring & Fraud Evaluation Engine:
  - Pure scoring function: `calculate_worker_score(job_days_history)`
  - Confirmed days count at full weight; auto-released days count at discounted weight
  - Disputed days count 0 until resolved
  - Unique payers vs repeat payers weighting
  - Active earning months factor
  - Single-payer contribution cap (prevents single patron inflation)
  - Down-weight payers with no other system history
  - Fraud detection flags:
    - Circular flows (payer previously received funds from worker, or payer account matches worker payout account)
    - Identity collision (worker, client, supervisor share phone or Telegram ID)
    - Rapid burst volume outside trade norms
- [x] Comprehensive unit tests for state machines, ledger invariants, scoring, and fraud rules

---

## Phase 3: Paga Escrow Integration (Collect & Payout Adapters)
- [x] Paga Collect Adapter (`app/integrations/paga/`):
  - Interface definition with real sandbox client and mock fixture adapter
  - Create payment request & dedicated one-time account number per job
  - Save reference against job for callback matching
- [x] Paga Webhook Ingestion (`POST /webhooks/paga`):
  - Raw body SHA-512 HMAC signature verification
  - Idempotent deduplication against `payments_in.paga_event_id`
  - Amount validation: Match received kobo with job's `total_kobo`
  - On exact match: Move job to `FUNDED`, write `FUNDED` ledger entry
  - On mismatch: Keep in `AWAITING_FUNDING` and flag anomaly
- [x] Paga Payout Adapter:
  - Interface for disbursing day pay to worker's Paga account
  - Real sandbox client + mock implementation switched by config
  - Use unique `payouts.idempotency_ref` to guarantee zero duplicate payouts
  - Retry handling with exponential backoff

---

## Phase 4: Work-Day Lifecycle & Release Services (`app/services/`)
- [ ] Worker "Done" Trigger (`POST /jobs/{id}/days/{n}/done`):
  - Mark day `DONE_REPORTED`, record `done_at`
  - Compute `release_at = done_at + 24 hours` (configurable duration via env, e.g. 2 minutes for demo)
  - Notify client and supervisor via Telegram
- [ ] Client / Supervisor Confirmation (`POST /jobs/{id}/days/{n}/confirm`):
  - Validate actor is job's client or assigned supervisor
  - Validate day is in `DONE_REPORTED` and within release window
  - Transition day to `CONFIRMED`
  - In same DB transaction: Create `payouts` row (outbox pattern) and write `RELEASED` ledger entry
  - Trigger Paga payout $\rightarrow$ transition day to `PAID`
- [ ] Client / Supervisor Dispute (`POST /jobs/{id}/days/{n}/dispute`):
  - Validate actor authorization
  - Transition day to `DISPUTED`, freeze 24h countdown, create `disputes` record
- [ ] Job Completion & Receipt Minting:
  - When all days are settled, transition job to `COMPLETED`
  - Mint official Proofwork Receipt (`PW-YYYY-XXXX`) and save metadata

---

## Phase 5: Background Scheduler & Payout Outbox (`app/workers/`)
- [ ] Background Auto-Release Worker:
  - Periodic polling task checking for `DONE_REPORTED` days where `now >= release_at`
  - Lock candidate days using `SELECT ... FOR UPDATE SKIP LOCKED`
  - Transition day to `AUTO_RELEASED`
  - Queue payout in outbox, write `RELEASED` ledger entry, and trigger disbursement
- [ ] 12-Hour Midpoint Reminder:
  - Send Telegram/SMS reminder to client and supervisor at 12 hours remaining
- [ ] Payout Outbox Dispatcher:
  - Processes pending payouts with retry backoff and idempotency protection
- [ ] Ledger Reconciliation Command:
  - CLI/script checking that ledger balances match payout and refund statuses

---

## Phase 6: Telegram Bot Integration (`aiogram 3`) (`app/integrations/telegram/`)
- [ ] Mount `aiogram 3` webhook inside FastAPI at `POST /webhooks/telegram` with secret token header
- [ ] User Onboarding & Identity:
  - Telegram share-contact button to get verified E.164 phone number
  - Register as Client or Worker (set payout Paga account)
- [ ] Client Flow:
  - Menu: "New Job", "My Jobs", "Confirm Pending"
  - Interactive job creation: Pick trade from button list $\rightarrow$ Enter description $\rightarrow$ Enter total amount & days
  - Pick artisan from directory OR enter worker phone to invite
  - Bot returns one-time Paga payment link and account number
  - Optional: Assign supervisor by phone or Telegram ID
- [ ] Worker Flow:
  - Menu: "My Active Jobs", "Done Today", "My Income Record"
  - View job details with locked escrow badge
  - Click "Done Today" on active work day $\rightarrow$ starts 24h window
- [ ] Supervisor / Client Review Flow:
  - Instant notification when worker reports "done"
  - Inline buttons: [✅ Confirm Day Pay] [⚠️ Dispute Work]
  - Secure opaque callback tokens verifying user authorization
- [ ] SMS Invitation Deep Link Handling:
  - If artisan or supervisor is invited by phone before starting bot, send SMS with `t.me/<bot>?start=invite_<token>`
  - Bot recognizes invite token on `/start` and binds user to the funded job

---

## Phase 7: REST API Surface & Admin Endpoints (`app/api/`)
- [ ] Passwordless Authentication (`app/api/auth.py` & `app/services/auth_service.py`):
  - `POST /auth/signup`: Initiates registration with name and phone number; sends OTP via SMS
  - `POST /auth/login`: Initiates login with phone number; sends OTP via SMS
  - `POST /auth/verify-otp`: Validates OTP code, marks phone verified, returns JWT access token & user profile
  - `GET /auth/me`: Returns current authenticated user and profile
- [ ] Job Endpoints:
  - `POST /jobs`: Create escrow job draft
  - `GET /jobs/{id}`: View job details, daily schedule, and escrow status
  - `POST /jobs/{id}/assign`: Assign artisan
  - `POST /jobs/{id}/fund-request`: Fetch Paga payment account & link
  - `POST /jobs/{id}/days/{n}/done`: Report work completed
  - `POST /jobs/{id}/days/{n}/confirm`: Confirm day
  - `POST /jobs/{id}/days/{n}/dispute`: Dispute day
- [ ] Artisan Directory:
  - `GET /workers/available?trade=&area=`: Filter available artisans
- [ ] Verification & Reports:
  - `GET /workers/{id}/record`: Full worker profile with job & day lines
  - `POST /workers/{id}/shares`: Create 7-day consented lender share link
  - `GET /reports/{token}`: Verified lender report with tokenized payers & score breakdown
  - `GET /jobs/verify/{receipt_id}`: Public receipt verification
- [ ] Admin:
  - `POST /admin/disputes/{id}/resolve`: Admin manual resolution of frozen days

---

## Phase 8: Web Views (Jinja2 + HTMX) & Demo Polish
- [ ] Web Views:
  - Job escrow checkout page (displaying Paga account number, QR, and link)
  - Public Proofwork Receipt page (`/jobs/verify/{receipt_id}`)
  - Lender Report portal (`/reports/{token}`) with score breakdown, auto-release percentage, and tokenized client list
  - Artisan directory view
- [ ] Simulated Interfaces (clearly labelled as simulated for demo):
  - WhatsApp template confirmation fallback view
  - SMS notification view
  - USSD basic-phone flow simulator

---

## Phase 9: Automated Testing & Verification
- [ ] Pure Domain Tests:
  - State machine transition tests (allowed paths pass, illegal paths raise)
  - Ledger property tests (released + refunded <= funded)
  - Daily pay calculation tests (integer remainder distributed correctly)
  - Pure scoring rule tests (auto-release down-weighting, payer caps, active months)
  - Fraud rule tests (circular flows, bursts, identity collision)
- [ ] Integration Tests:
  - Paga webhook signature verification & replay idempotency
  - Paga payout idempotency & mock client tests
  - Telegram webhook secret token validation
- [ ] End-to-End Test Suite:
  - Complete flow: Create Job $\rightarrow$ Paga Webhook Fund $\rightarrow$ Worker Done $\rightarrow$ Confirm/Auto-Release $\rightarrow$ Payout $\rightarrow$ Receipt Mint $\rightarrow$ Lender Report

---

## Phase 10: Demonstration & Documentation Assets
- [ ] Seed script (`python -m app.db.seed`):
  - Seed Emem the bricklayer with 4 months of mixed history (confirmed days, auto-released days, repeat clients, and 1 flagged circular transaction for demo)
- [ ] README.md: Architecture overview, setup guide, environment variable table, and Docker instructions
- [ ] API_NOTES.md: Complete endpoint specifications, webhook schemas, and payload examples
- [ ] DEMO_SCRIPT.md: Minute-by-minute live demo script for competition judging
