# Backend Spec: Invited Supervisor Confirmation & Proof Photo

## Goal
Allow an employer to assign a site supervisor to a job by **name and phone number** (without requiring the supervisor to create an account or log in). The supervisor receives a unique token link (`/supervisor/verify?job=<job_id>&token=<supervisor_token>`) to confirm or dispute days marked done, with an **optional proof photograph**.

---

## 1. Database Changes

### `jobs` table
| Column | Type | Nullable | Notes |
|---|---|---|---|
| `supervisor_name` | `String(150)` | `True` | Supervisor's display name |
| `supervisor_phone` | `String(30)` | `True` | Normalized E.164 phone (unique identifier for supervisor) |
| `supervisor_token` | `String(64)` | `True` (unique, indexed) | Generated once as `sup_<urlsafe_token>` |

### `job_days` table
| Column | Type | Nullable | Notes |
|---|---|---|---|
| `proof_photo` | `Text` | `True` | Optional base64 data URL or image URL attached on confirm |

### Existing Response Updates
- **`JobResponse`**: include `supervisor_name`, `supervisor_phone`, `supervisor_token`.
- **`JobDayResponse`**: include `proof_photo`.

---

## 2. Endpoints

### 1) `POST /jobs/{job_id}/supervisor`
Assigns a supervisor to a job and generates `supervisor_token` if not already set.
- **Auth**: Bearer JWT (job `client_id` or `admin`).
- **Request**:
```json
{
  "supervisor_name": "Foreman Bassey",
  "supervisor_phone": "+2348051120944"
}
```
- **Response (`200 OK`)**: `JobResponse` (with `supervisor_name`, `supervisor_phone`, `supervisor_token`).

---

### 2) `GET /jobs/{job_id}/supervisor-view?token={supervisor_token}`
Public, token-gated endpoint for the supervisor's verification page. **No client PII** (no `client_id`, client name, or client phone).
- **Auth**: Public (validated by `token == job.supervisor_token`).
- **Response (`200 OK`)**:
```json
{
  "id": "uuid",
  "trade": "BRICKLAYING",
  "description": "Perimeter block wall",
  "total_kobo": 4500000,
  "days_count": 3,
  "status": "IN_PROGRESS",
  "worker_name": "Emem Bassey",
  "supervisor_name": "Foreman Bassey",
  "supervisor_phone": "+2348051120944",
  "days_schedule": [
    {
      "id": "uuid",
      "day_number": 1,
      "amount_kobo": 1500000,
      "status": "DONE_REPORTED",
      "done_at": "2026-11-18T01:00:00+00:00",
      "release_at": "2026-11-19T01:00:00+00:00",
      "proof_photo": null
    }
  ]
}
```
- **Errors**: `404` if job not found or `token` does not match `job.supervisor_token`.

---

### 3) `POST /jobs/{job_id}/days/{day_number}/supervisor-confirm?token={supervisor_token}`
Confirms a `DONE_REPORTED` day as `SUPERVISOR`, optionally saving `proof_photo` and triggering payout release.
- **Auth**: Public (validated by `token == job.supervisor_token`). If `phone` is sent, verify normalized `phone == job.supervisor_phone`.
- **Request**:
```json
{
  "proof_photo": "data:image/jpeg;base64,...",
  "phone": "+2348051120944"
}
```
*(Both `proof_photo` and `phone` are optional.)*
- **Response (`200 OK`)**: `JobDayResponse` (with updated `status` and `proof_photo`).
- **Errors**: `403` on invalid token or phone mismatch; `400` if day is not `DONE_REPORTED`.

---

### 4) `POST /jobs/{job_id}/days/{day_number}/supervisor-dispute?token={supervisor_token}`
Disputes a `DONE_REPORTED` day and freezes the 24h auto-release timer.
- **Auth**: Public (validated by `token == job.supervisor_token`).
- **Request**:
```json
{
  "reason": "Incomplete day"
}
```
- **Response (`200 OK`)**: `JobDayResponse` (`status: "DISPUTED"`).
- **Notes**: If `disputes.opened_by` is non-nullable in DB and `job.supervisor_id` is `null`, fall back to `job.client_id` for `opened_by`.
