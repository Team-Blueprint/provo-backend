# Provo — Backend

**Provo** protects daily pay for skilled artisans and turns settled work days into a verifiable **Profile Score** and work-history report for lenders.

This repository contains the FastAPI backend powering escrow job funding, Paga virtual account webhooks, day-by-day release & 24-hour auto-release, supervisor/employer confirmation, USSD `*123#` endpoints, Telegram notifications, and cryptographically signed lender reports.

---

## Repositories

This project is split across two repositories:

| Service | Repository | Description |
| --- | --- | --- |
| **Backend API (this repo)** | [Team-Blueprint/provo-backend](https://github.com/Team-Blueprint/provo-backend) | FastAPI REST API, Paga escrow integration, USSD/SMS webhooks, and signed snapshot ledger |
| **Frontend** | [Team-Blueprint/provo-frontend](https://github.com/Team-Blueprint/provo-frontend) | React + Vite web application, USSD simulator, and interactive docs |

---

## API Domains

- **Auth (`/api/auth`)** — Phone OTP verification, PIN setup/login, and JWT sessions
- **Jobs (`/api/jobs`)** — Escrow job creation, Paga NUBAN funding, daily check-ins, employer/supervisor confirmations, proof photos, and disputes
- **Workers (`/api/workers`)** — Artisan directory, worker dashboard, notification settings, and NDPA consent share links
- **Reports (`/api/reports`)** — Consented lender reports, Profile Score breakdown, and signed snapshot verification
- **USSD (`/api/ussd`)** — Feature-phone `*123#` registration, job check-ins, and withdrawal requests
- **Webhooks & Sandbox (`/api/webhooks`, `/api/sandbox`)** — Paga deposit notifications and sandbox funding triggers
- **Admin (`/api/admin`)** — Dispute resolution and ledger inspection
- **Health (`/health`)** — Service health check

---

## Tech Stack

- **Framework**: FastAPI, Uvicorn, Pydantic v2
- **Database**: SQLAlchemy 2 (async), Alembic, SQLite (`aiosqlite`) / PostgreSQL (`asyncpg`)
- **Messaging & HTTP**: `aiogram` (Telegram), `httpx`
- **Auth & Testing**: `pyjwt`, `pytest`, `pytest-asyncio`, `ruff`
- **Python**: `>=3.11` (managed with `uv` or `pip`)

---

## Getting Started

### Environment Variables

Copy `.env.example` to `.env` and configure your environment variables:

```bash
cp .env.example .env
```

### Install & Run

```bash
# Using uv
uv sync
uv run uvicorn main:app --reload --port 8000
```

Or with `pip`:

```bash
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Interactive API docs are available at `http://localhost:8000/docs`.

### Tests

```bash
uv run pytest
```
