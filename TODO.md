**Start these now (they run while you code)**
- [ ] Request Paga sandbox access
- [ ] Create a Meta developer app, add WhatsApp, and get the test number
- [ ] Add the demo phones as sandbox recipients (the test number only messages the numbers you add)
- [ ] Submit the templates: client confirmation (quick-reply buttons: "Yes, for work" / "No") and worker money-arrived alert
- [ ] Pick an SMS provider and register a sender ID if required

**Phase 0: Setup (1 hr)**
- [ ] Repo, FastAPI skeleton, ruff, pytest, pre-commit
- [ ] Docker Compose: API, Postgres, Redis, worker
- [ ] Settings module for all secrets (Paga, WhatsApp token, app secret, verify token, HMAC secret, SMS key)
- [ ] Tunnel with a fixed public URL for webhooks
- [ ] CI running lint and tests
- [ ] Health endpoint, structured logging with a request ID

**Phase 1: Data model (2 hrs)**
- [ ] Tables: workers, payers, payment_events, jobs, confirmations, receipts, consents, share_links, disputes, messages_log, audit_log
- [ ] Money as integer kobo; phone numbers stored in one normalized format
- [ ] Unique constraints on the Paga transaction ID and the WhatsApp message ID
- [ ] Job type enum and job status enum in the DB
- [ ] Alembic migration, plus a seed fixtures folder

**Phase 2: Paga adapter (3 hrs)**
- [ ] Adapter interface with real and mock implementations, switched by config
- [ ] Persistent account creation, stored against the worker
- [ ] Payment request creation (the payment link) with your own reference
- [ ] Fire a real sandbox payment and save the raw callback
- [ ] Write down which payer fields the callback returns, since this decides grade 2
- [ ] Turn saved payloads into fixtures and build the mock from them

**Phase 3: Paga webhook (3 hrs)**
- [ ] Signature verification against the raw body
- [ ] Store the raw event first, ack immediately, process in the worker
- [ ] Idempotency through the DB constraint, plus a replay test
- [ ] Match the event to a worker by account number or reference
- [ ] Classify the grade (link and phone-verified, or transfer)
- [ ] Create the job as `awaiting_confirmation` and queue the confirmation message

**Phase 4: WhatsApp core (4 hrs)**
- [ ] GET webhook verification handshake
- [ ] POST webhook with signature check on the raw body (app secret), ack fast, process async
- [ ] Dedupe inbound messages by message ID
- [ ] Sender module: send template, send text, send interactive buttons, send interactive list
- [ ] Inbound parser covering text, template button taps, button replies and list replies
- [ ] Delivery status handling (sent, delivered, failed) written to messages_log
- [ ] Fallback: if a send fails or the number has no WhatsApp, queue the SMS path

**Phase 5: Client confirmation flow (4 hrs)**
- [ ] Template goes out: "Did X pay you for work?" with quick-reply buttons
- [ ] Button payload carries a signed, single-use, expiring token tied to the job
- [ ] "Yes" tap opens the 24-hour window, so send an interactive list of job types
- [ ] List pick confirms the job, then issue the receipt and send it
- [ ] "No" tap marks the job disputed
- [ ] Link-payment jobs skip the list, since the job type was picked on the link page
- [ ] Reject stale, reused or mismatched tokens, with a test for each
- [ ] Expiry task that moves unanswered jobs to `expired`
- [ ] SMS version: one-tap link to a minimal confirm page, same token logic

**Phase 6: Worker flow over WhatsApp (4 hrs)**
- [ ] Signup: the worker texts the business number, which opens their window
- [ ] Collect name and trade (list message), create the worker, create the Paga account, and reply with the account number and payment link
- [ ] Money-arrived alert (template if outside the window, free-form inside it)
- [ ] Cash logging: worker sends client phone, amount and job type, creating a draft
- [ ] Send the client confirmation for the cash draft
- [ ] Commands: "record" (summary), "share" (consent prompt, then report link), "help"
- [ ] Simple state tracking per conversation so multi-step replies work

**Phase 7: Job lifecycle rules (3 hrs)**
- [ ] One transition function enforcing the state machine, with an audit entry on every change
- [ ] Drafts excluded from scoring
- [ ] Dispute window: disputes allowed for N days, and disputed jobs drop out of scoring
- [ ] Receipt ID generation and a public verify-by-ID endpoint
- [ ] Client notice that the payment is recorded, sent with the first confirmation message

**Phase 8: Scoring and fraud (4 hrs)**
- [ ] Pure functions only: confirmed jobs in, score and reasons out
- [ ] Unique payers, repeat clients, active months
- [ ] Circular-flow detection across payer and worker accounts
- [ ] New-payer down-weighting
- [ ] Cash share cap
- [ ] Tests: one per rule, edge cases (empty history, one payer, all cash), and one end-to-end scoring test
- [ ] Config object for weights so tests don't depend on magic numbers

**Phase 9: Consent and lender report (3 hrs)**
- [ ] Consent record per share, with purpose and timestamp
- [ ] Share link: random token, expiry, revocation
- [ ] Payer tokens via HMAC with a secret, scoped per worker
- [ ] Report endpoint: confirmed jobs, repeat clients, active months, score, reasons, tokenized payers
- [ ] Worker profile endpoint with each job as a line
- [ ] Access log for every report view

**Phase 10: Demo readiness (3 hrs)**
- [ ] Seed script: one worker with several months of mixed history, including a flagged circular-flow case
- [ ] One-command reset and reseed
- [ ] End-to-end test: link, pay, webhook, WhatsApp confirm, receipt, report
- [ ] Minimal frontend pages only where required: payment link page, SMS confirm page, lender report view
- [ ] Dry run on the real demo phones, and record a backup video

**Phase 11: Docs (3 hrs)**
- [ ] README with setup steps and env var table
- [ ] Architecture diagram
- [ ] API notes
- [ ] Demo script
- [ ] Fix the plan doc: "hash" becomes HMAC
- [ ] Label USSD and the loan step as simulated

**Fastest route**
1. Phases 0 to 3 first, since nothing works without them.
2. Run Phases 4 to 6 in parallel with Phase 8 if you have teammates. Scoring is pure and needs no integrations.
3. Don't touch Phase 7 until the state machine is clear, then it takes an hour.
4. Protect Phases 10 and 11 at the end.

**Risk to watch:** the WhatsApp test number can only message recipients you've added. Add every demo phone early and don't count on messaging arbitrary numbers until you have a verified business account.

How many days do you have? I can slot these into a schedule.