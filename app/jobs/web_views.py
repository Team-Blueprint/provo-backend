from app.core.enums import JobType, VerificationGrade
from app.core.utils import kobo_to_naira_str
from app.jobs.models import Confirmation, Receipt


def render_client_confirmation_page(conf: Confirmation) -> str:
    job = conf.job
    worker = job.worker
    amount_str = kobo_to_naira_str(job.amount_kobo)
    needs_job_type = job.grade != VerificationGrade.GRADE_1_LINK and job.job_type == JobType.OTHER

    job_type_options = "".join(
        f'<option value="{t.value}">{t.value.replace("_", " ").title()}</option>'
        for t in JobType
        if t != JobType.OTHER
    )

    job_type_section = (
        f"""
        <div style="margin-bottom: 20px; text-align: left;">
            <label style="display: block; font-weight: 600; margin-bottom: 8px; color: #374151;">Select Job Type:</label>
            <select name="job_type" required style="width: 100%; padding: 10px; border-radius: 8px; border: 1px solid #D1D5DB; font-size: 16px;">
                <option value="" disabled selected>-- Choose what work was done --</option>
                {job_type_options}
            </select>
        </div>
        """
        if needs_job_type
        else f"""
        <div style="background-color: #F3F4F6; padding: 12px 16px; border-radius: 8px; margin-bottom: 20px; text-align: left;">
            <span style="color: #6B7280; font-size: 13px;">Job Type:</span>
            <p style="margin: 4px 0 0; font-weight: 600; color: #111827;">{job.job_type.value.replace("_", " ").title()}</p>
        </div>
        """
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Confirm Payment - Proofwork</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background: #F9FAFB; margin: 0; padding: 20px; color: #111827; }}
        .card {{ max-width: 440px; margin: 30px auto; background: #FFFFFF; border-radius: 16px; box-shadow: 0 4px 20px rgba(0,0,0,0.06); padding: 32px 24px; text-align: center; border: 1px solid #E5E7EB; }}
        .badge {{ background: #E0E7FF; color: #3730A3; padding: 6px 14px; border-radius: 999px; font-size: 12px; font-weight: 700; letter-spacing: 0.05em; text-transform: uppercase; }}
        .btn {{ display: block; width: 100%; padding: 14px; border-radius: 10px; font-size: 16px; font-weight: 600; cursor: pointer; border: none; margin-top: 12px; text-decoration: none; box-sizing: border-box; }}
        .btn-confirm {{ background: #10B981; color: #FFFFFF; }}
        .btn-confirm:hover {{ background: #059669; }}
        .btn-dispute {{ background: #FFFFFF; color: #DC2626; border: 1px solid #FECACA; }}
        .btn-dispute:hover {{ background: #FEF2F2; }}
    </style>
</head>
<body>
    <div class="card">
        <span class="badge">Proofwork Verification</span>
        <h2 style="margin: 16px 0 6px; font-size: 22px;">Confirm Payment for Work</h2>
        <p style="color: #6B7280; font-size: 14px; margin-bottom: 24px;">Your confirmation helps this worker build a verified credit record for lending.</p>

        <div style="background-color: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 12px; padding: 16px; margin-bottom: 24px; text-align: left;">
            <div style="margin-bottom: 12px;">
                <span style="color: #64748B; font-size: 12px; text-transform: uppercase; font-weight: 600;">Worker:</span>
                <p style="margin: 2px 0 0; font-weight: 600; font-size: 16px;">{worker.name} <span style="font-size: 13px; font-weight: 400; color: #64748B;">({worker.trade})</span></p>
            </div>
            <div>
                <span style="color: #64748B; font-size: 12px; text-transform: uppercase; font-weight: 600;">Amount Paid:</span>
                <p style="margin: 2px 0 0; font-weight: 700; font-size: 24px; color: #0F172A;">{amount_str}</p>
            </div>
        </div>

        <form method="POST" action="/confirm/{conf.confirmation_token}">
            {job_type_section}
            <input type="hidden" name="confirmed" value="true">
            <button type="submit" class="btn btn-confirm">Yes, Confirm Work & Get Receipt</button>
        </form>

        <form method="POST" action="/confirm/{conf.confirmation_token}" style="margin-top: 10px;">
            <input type="hidden" name="confirmed" value="false">
            <button type="submit" class="btn btn-dispute">No, this was not for work</button>
        </form>
    </div>
</body>
</html>
"""


def render_receipt_success_page(receipt: Receipt) -> str:
    data = receipt.receipt_data
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Official Receipt - {receipt.receipt_number}</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background: #F3F4F6; margin: 0; padding: 20px; }}
        .card {{ max-width: 440px; margin: 30px auto; background: #FFFFFF; border-radius: 16px; box-shadow: 0 4px 20px rgba(0,0,0,0.06); padding: 32px 24px; text-align: center; border: 1px solid #E5E7EB; }}
        .check-icon {{ width: 56px; height: 56px; background: #DEF7EC; color: #0E9F6E; border-radius: 50%; display: flex; align-items: center; justify-content: center; margin: 0 auto 16px; font-size: 28px; }}
        .row {{ display: flex; justify-content: space-between; padding: 10px 0; border-bottom: 1px dashed #E5E7EB; font-size: 14px; text-align: left; }}
        .label {{ color: #6B7280; }}
        .val {{ font-weight: 600; color: #111827; text-align: right; }}
    </style>
</head>
<body>
    <div class="card">
        <div class="check-icon">✓</div>
        <h2 style="margin: 0 0 6px; font-size: 22px; color: #111827;">Work Confirmed!</h2>
        <p style="color: #6B7280; font-size: 14px; margin-bottom: 24px;">An official Proofwork receipt has been issued and added to the worker's verified credit history.</p>

        <div style="background: #F9FAFB; border: 1px solid #E5E7EB; border-radius: 12px; padding: 16px; margin-bottom: 24px;">
            <div class="row"><span class="label">Receipt Number</span><span class="val" style="color: #4F46E5;">{receipt.receipt_number}</span></div>
            <div class="row"><span class="label">Worker</span><span class="val">{data.get("worker_name")} ({data.get("worker_trade")})</span></div>
            <div class="row"><span class="label">Job Type</span><span class="val">{data.get("job_type")}</span></div>
            <div class="row"><span class="label">Amount</span><span class="val" style="font-size: 16px; color: #047857;">{data.get("amount_formatted")}</span></div>
            <div class="row" style="border: none;"><span class="label">Date Verified</span><span class="val">{data.get("confirmed_at", "")[:10]}</span></div>
        </div>

        <p style="font-size: 12px; color: #9CA3AF;">Proofwork Income Verification • Lender-Trust Rails</p>
    </div>
</body>
</html>
"""


def render_message_page(title: str, message: str, is_error: bool = False) -> str:
    color = "#DC2626" if is_error else "#4F46E5"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} - Proofwork</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background: #F9FAFB; margin: 0; padding: 20px; }}
        .card {{ max-width: 440px; margin: 40px auto; background: #FFFFFF; border-radius: 16px; box-shadow: 0 4px 20px rgba(0,0,0,0.06); padding: 32px 24px; text-align: center; border: 1px solid #E5E7EB; }}
    </style>
</head>
<body>
    <div class="card">
        <h2 style="color: {color}; margin-top: 0;">{title}</h2>
        <p style="color: #4B5563; font-size: 15px; line-height: 1.5;">{message}</p>
    </div>
</body>
</html>
"""
