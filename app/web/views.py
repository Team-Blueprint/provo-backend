import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.enums import JobStatus
from app.db.models import Job, Receipt
from app.services.report_service import ReportService

router = APIRouter(tags=["Web Views"])
report_service = ReportService()


@router.get("/checkout/{job_id}/status")
async def checkout_job_status(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Public poll endpoint for the checkout page to check if payment arrived."""
    stmt = select(Job.status).where(Job.id == job_id)
    status_val = (await db.execute(stmt)).scalar_one_or_none()
    if not status_val:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"status": status_val}


@router.get("/checkout/{job_id}", response_class=HTMLResponse)
async def checkout_page(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """
    Client Escrow Funding Checkout page.
    Displays one-time 10-digit virtual NUBAN, transfer instructions, and real-time escrow lock status.
    """
    stmt = (
        select(Job)
        .where(Job.id == job_id)
        .options(selectinload(Job.worker), selectinload(Job.days_schedule))
    )
    job = (await db.execute(stmt)).scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    amount_naira = f"₦{job.total_kobo / 100:,.2f}"
    worker_name = job.worker.name if job.worker else "Assigned Artisan"
    account_number = job.paga_account_number or "9901234567"
    is_funded = job.status in [JobStatus.FUNDED.value, JobStatus.COMPLETED.value]

    status_badge = (
        '<span style="background: #DEF7EC; color: #03543F; padding: 6px 14px; border-radius: 999px; font-weight: 700; font-size: 13px;">✅ ESCROW FUNDED</span>'
        if is_funded
        else '<span style="background: #FEF08A; color: #713F12; padding: 6px 14px; border-radius: 999px; font-weight: 700; font-size: 13px;">⏳ AWAITING TRANSFER</span>'
    )

    poll_script = ""
    if not is_funded:
        poll_script = """
        <script>
            setInterval(async () => {
                try {
                    const res = await fetch('/checkout/' + window.JOB_ID + '/status');
                    if (res.ok) {
                        const data = await res.json();
                        if (data.status === 'FUNDED' || data.status === 'COMPLETED') {
                            location.reload();
                        }
                    }
                } catch (e) {}
            }, 3000);
        </script>
        """

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Proofwork Escrow Checkout</title>
    <style>
        * {{ box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #F3F4F6; margin: 0; padding: 24px; color: #111827; }}
        .container {{ max-width: 480px; margin: 20px auto; background: #FFFFFF; border-radius: 16px; box-shadow: 0 10px 25px -5px rgba(0,0,0,0.08); overflow: hidden; border: 1px solid #E5E7EB; }}
        .header {{ background: #0F172A; color: #FFFFFF; padding: 24px; text-align: center; }}
        .header h1 {{ margin: 0 0 6px; font-size: 20px; font-weight: 700; letter-spacing: -0.02em; }}
        .header p {{ margin: 0; color: #94A3B8; font-size: 13px; }}
        .body {{ padding: 24px; }}
        .amount-box {{ background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 12px; padding: 18px; text-align: center; margin-bottom: 20px; }}
        .amount {{ font-size: 32px; font-weight: 800; color: #0284C7; margin: 4px 0 0; }}
        .nuban-box {{ background: #EFF6FF; border: 2px dashed #3B82F6; border-radius: 12px; padding: 20px; text-align: center; margin-bottom: 20px; }}
        .nuban-number {{ font-family: monospace; font-size: 26px; font-weight: 700; letter-spacing: 3px; color: #1D4ED8; margin: 8px 0; }}
        .bank-name {{ font-size: 14px; font-weight: 600; color: #374151; }}
        .info-row {{ display: flex; justify-content: space-between; padding: 10px 0; border-bottom: 1px solid #F1F5F9; font-size: 14px; }}
        .info-label {{ color: #64748B; }}
        .info-val {{ font-weight: 600; color: #0F172A; }}
        .copy-btn {{ background: #2563EB; color: #FFF; border: none; padding: 10px 18px; border-radius: 8px; font-weight: 600; cursor: pointer; margin-top: 8px; font-size: 13px; }}
        .copy-btn:hover {{ background: #1D4ED8; }}
        .badge-wrap {{ text-align: center; margin: 16px 0; }}
        .notice {{ background: #FEF3C7; border: 1px solid #FDE68A; border-radius: 8px; padding: 12px; font-size: 12px; color: #92400E; margin-top: 20px; line-height: 1.5; }}
    </style>
    <script>window.JOB_ID = "{job.id}";</script>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Proofwork Escrow Funding</h1>
            <p>100% Locked in Escrow until you confirm each day's work</p>
        </div>
        <div class="body">
            <div class="badge-wrap">{status_badge}</div>
            <div class="amount-box">
                <span style="font-size: 13px; color: #64748B; text-transform: uppercase; font-weight: 600;">Total Escrow Amount</span>
                <div class="amount">{amount_naira}</div>
                <span style="font-size: 12px; color: #64748B;">Disbursed daily across {job.days_count} work days</span>
            </div>

            <div class="nuban-box">
                <div style="font-size: 12px; color: #1E40AF; font-weight: 600; text-transform: uppercase;">Transfer to One-Time Escrow NUBAN</div>
                <div class="nuban-number" id="nubanText">{account_number}</div>
                <div class="bank-name">Paga MFB &bull; Proofwork Escrow</div>
                <button class="copy-btn" onclick="navigator.clipboard.writeText('{account_number}'); alert('Account number copied!');">📋 Copy Account Number</button>
            </div>

            <div class="info-row">
                <span class="info-label">Artisan</span>
                <span class="info-val">{worker_name} ({job.trade})</span>
            </div>
            <div class="info-row">
                <span class="info-label">Job Reference</span>
                <span class="info-val">{job.funding_reference}</span>
            </div>
            <div class="info-row">
                <span class="info-label">Daily Release</span>
                <span class="info-val">₦{(job.total_kobo // job.days_count) / 100:,.2f} / day</span>
            </div>

            <div class="notice">
                ℹ️ <strong>NDPA & Safety Notice:</strong> Funds are locked securely in Paga escrow. Money is disbursed to {worker_name} only after they tap "Done Today" and you confirm. You have a 24-hour review window for each day.
            </div>
        </div>
    </div>
    {poll_script}
</body>
</html>
"""
    return HTMLResponse(content=html)


@router.get("/receipts/{receipt_number}", response_class=HTMLResponse)
@router.get("/jobs/verify/{receipt_number}/view", response_class=HTMLResponse)
async def view_verified_receipt(
    receipt_number: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Publicly verifiable cryptographic receipt certificate page.
    """
    stmt = (
        select(Receipt)
        .where(Receipt.receipt_number == receipt_number)
        .options(selectinload(Receipt.job).selectinload(Job.worker))
    )
    receipt = (await db.execute(stmt)).scalar_one_or_none()
    if not receipt:
        raise HTTPException(status_code=404, detail="Receipt not found")

    job = receipt.job
    worker_name = job.worker.name if job.worker else "Verified Artisan"
    issued_date = receipt.issued_at.strftime("%d %B %Y, %I:%M %p UTC")
    amount_naira = f"₦{job.total_kobo / 100:,.2f}"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Proofwork Verified Certificate - {receipt.receipt_number}</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #F8FAFC; margin: 0; padding: 32px 16px; color: #0F172A; }}
        .cert-card {{ max-width: 600px; margin: 0 auto; background: #FFFFFF; border-radius: 16px; box-shadow: 0 10px 30px rgba(0,0,0,0.06); border: 2px solid #E2E8F0; padding: 40px; position: relative; }}
        .stamp {{ display: inline-block; background: #DEF7EC; color: #03543F; border: 2px solid #31C48D; font-weight: 800; font-size: 14px; padding: 8px 16px; border-radius: 999px; text-transform: uppercase; letter-spacing: 1px; }}
        .header {{ text-align: center; border-bottom: 2px solid #F1F5F9; padding-bottom: 24px; margin-bottom: 24px; }}
        .receipt-no {{ font-family: monospace; font-size: 16px; color: #64748B; margin-top: 8px; }}
        .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin: 24px 0; }}
        .field {{ background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 10px; padding: 14px; }}
        .field-label {{ font-size: 12px; color: #64748B; text-transform: uppercase; font-weight: 600; margin-bottom: 4px; }}
        .field-val {{ font-size: 16px; font-weight: 700; color: #0F172A; }}
        .footer {{ text-align: center; margin-top: 32px; padding-top: 20px; border-top: 1px dashed #CBD5E1; font-size: 12px; color: #64748B; }}
    </style>
</head>
<body>
    <div class="cert-card">
        <div class="header">
            <div class="stamp">🛡️ Cryptographically Verified Escrow</div>
            <h1 style="margin: 16px 0 4px; font-size: 24px;">Proofwork Certificate of Completion</h1>
            <div class="receipt-no">{receipt.receipt_number}</div>
        </div>

        <div class="grid">
            <div class="field">
                <div class="field-label">Artisan</div>
                <div class="field-val">{worker_name}</div>
            </div>
            <div class="field">
                <div class="field-label">Trade Track</div>
                <div class="field-val">{job.trade}</div>
            </div>
            <div class="field">
                <div class="field-label">Escrow Settled</div>
                <div class="field-val" style="color: #0284C7;">{amount_naira}</div>
            </div>
            <div class="field">
                <div class="field-label">Days Verified</div>
                <div class="field-val">{job.days_count} Days</div>
            </div>
        </div>

        <div class="field" style="margin-bottom: 20px;">
            <div class="field-label">Description of Work</div>
            <div class="field-val" style="font-weight: 500; font-size: 14px;">{job.description}</div>
        </div>

        <div class="field">
            <div class="field-label">Issued At</div>
            <div class="field-val" style="font-size: 14px;">{issued_date}</div>
        </div>

        <div class="footer">
            Immutable settlement record backed by Paga Virtual Accounts & SHA-512 Ledger Verification.
        </div>
    </div>
</body>
</html>
"""
    return HTMLResponse(content=html)


@router.get("/reports/{token}/view", response_class=HTMLResponse)
async def view_lender_report_portal(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Financial institution and lender view of the worker's verified credit report.
    Presents credit score, payer diversity matrix, and income stability metrics.
    """
    report = await report_service.generate_lender_report(db, token)
    if not report or "error" in report:
        raise HTTPException(status_code=404, detail="Lender report not found or expired")

    artisan = report["artisan"]
    score = report["credit_score"]
    earnings = report["earnings_summary"]
    diversity = report["payer_diversity"]
    consent = report["consent"]

    payers_rows = "".join(
        f"""
        <tr>
            <td style="padding: 10px; border-bottom: 1px solid #E2E8F0; font-family: monospace; font-weight: 600;">{p['token']}</td>
            <td style="padding: 10px; border-bottom: 1px solid #E2E8F0; text-align: center;">{p['jobs_count']}</td>
            <td style="padding: 10px; border-bottom: 1px solid #E2E8F0; text-align: center;">{p['days_settled']}</td>
            <td style="padding: 10px; border-bottom: 1px solid #E2E8F0; text-align: right; font-weight: 600;">₦{p['amount_kobo']/100:,.2f}</td>
        </tr>
        """
        for p in diversity["payers"]
    )

    reasons_list = "".join(f"<li>{r}</li>" for r in score["reasons"])

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Verified Credit Report - {artisan['name']}</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #F8FAFC; margin: 0; padding: 32px 16px; color: #0F172A; }}
        .portal {{ max-width: 760px; margin: 0 auto; background: #FFFFFF; border-radius: 16px; border: 1px solid #E2E8F0; box-shadow: 0 4px 20px rgba(0,0,0,0.05); padding: 36px; }}
        .header {{ display: flex; justify-content: space-between; align-items: flex-start; border-bottom: 2px solid #F1F5F9; padding-bottom: 20px; }}
        .score-box {{ background: #0F172A; color: #FFFFFF; border-radius: 14px; padding: 20px; text-align: center; min-width: 140px; }}
        .score-num {{ font-size: 44px; font-weight: 800; color: #38BDF8; line-height: 1; }}
        .score-band {{ font-size: 11px; text-transform: uppercase; font-weight: 700; color: #94A3B8; margin-top: 6px; letter-spacing: 0.05em; }}
        .kpi-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin: 24px 0; }}
        .kpi-card {{ background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 12px; padding: 16px; text-align: center; }}
        .kpi-val {{ font-size: 22px; font-weight: 800; color: #0F172A; }}
        .kpi-sub {{ font-size: 12px; color: #64748B; margin-top: 4px; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 14px; }}
        th {{ background: #F1F5F9; padding: 10px; text-align: left; font-size: 12px; color: #475569; text-transform: uppercase; }}
        .badge {{ padding: 4px 10px; border-radius: 999px; font-size: 12px; font-weight: 700; }}
        .notice {{ background: #EFF6FF; border: 1px solid #BFDBFE; border-radius: 10px; padding: 14px; font-size: 13px; color: #1E40AF; margin-top: 24px; }}
    </style>
</head>
<body>
    <div class="portal">
        <div class="header">
            <div>
                <span class="badge" style="background: #E0E7FF; color: #3730A3;">NDPA CONSENTED LENDER REPORT</span>
                <h1 style="margin: 10px 0 4px; font-size: 26px;">{artisan['name']}</h1>
                <p style="margin: 0; color: #64748B; font-size: 14px;">{artisan['trade']} &bull; {artisan['area']}</p>
            </div>
            <div class="score-box">
                <div class="score-num">{score['score']}</div>
                <div class="score-band">{score['band']}</div>
            </div>
        </div>

        <div class="kpi-grid">
            <div class="kpi-card">
                <div class="kpi-val">₦{earnings['total_earned_naira']}</div>
                <div class="kpi-sub">Total Verified Income</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-val">{earnings['total_settled_days']} Days</div>
                <div class="kpi-sub">Settled Escrow Days</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-val">{diversity['unique_payers_count']} Payers</div>
                <div class="kpi-sub">Payer Diversity Pool</div>
            </div>
        </div>

        <h3 style="font-size: 16px; margin: 28px 0 8px;">Payer Diversity & Independence (NDPA Tokenized)</h3>
        <table>
            <thead>
                <tr>
                    <th>Client Identifier</th>
                    <th style="text-align: center;">Jobs Count</th>
                    <th style="text-align: center;">Days Settled</th>
                    <th style="text-align: right;">Total Verified</th>
                </tr>
            </thead>
            <tbody>
                {payers_rows}
            </tbody>
        </table>

        <h3 style="font-size: 16px; margin: 28px 0 8px;">Score Model Breakdown & Factors</h3>
        <ul style="font-size: 14px; color: #334155; line-height: 1.6;">
            {reasons_list}
            <li>Auto-release ratio: {score['auto_release_ratio'] * 100:.1f}% of days (discounted by 0.5x vs full client confirmation).</li>
            <li>Single-payer concentration: {diversity['max_single_payer_concentration'] * 100:.1f}% (Cap: 35%).</li>
        </ul>

        <div class="notice">
            🔒 <strong>Audit Assurance:</strong> Generated under worker authorization ({consent['purpose']}).
            Valid until {consent['expires_at'][:10]}. Access count: {consent['access_count']}.
        </div>
    </div>
</body>
</html>
"""
    return HTMLResponse(content=html)


@router.get("/demo/channels", response_class=HTMLResponse)
async def demo_channels_page():
    """
    Demo showcase displaying simulated SMS, WhatsApp, and USSD interactions
    for competition presentation and judging.
    """
    html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Proofwork Multi-Channel Demo Interface</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0F172A; color: #F8FAFC; margin: 0; padding: 24px; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 24px; max-width: 1200px; margin: 0 auto; }
        .channel-card { background: #1E293B; border-radius: 16px; padding: 24px; border: 1px solid #334155; }
        .tag { display: inline-block; padding: 4px 10px; border-radius: 6px; font-size: 11px; font-weight: 700; text-transform: uppercase; margin-bottom: 12px; }
        .phone-mock { background: #000000; border-radius: 20px; padding: 20px 16px; border: 4px solid #475569; min-height: 380px; font-family: monospace; }
        .ussd-screen { background: #052e16; color: #4ade80; padding: 16px; border-radius: 8px; font-size: 13px; line-height: 1.5; }
        .chat-msg { background: #334155; padding: 10px 14px; border-radius: 12px; font-size: 13px; margin-bottom: 10px; max-width: 85%; }
        .chat-out { background: #0284C7; color: white; margin-left: auto; }
    </style>
</head>
<body>
    <h1 style="text-align: center; margin-bottom: 8px;">Proofwork Multi-Channel Fallback Architecture</h1>
    <p style="text-align: center; color: #94A3B8; margin-bottom: 32px;">Ensuring informal artisans can operate on basic 2G feature phones without smartphones or internet.</p>

    <div class="grid">
        <div class="channel-card">
            <span class="tag" style="background: #166534; color: #BBF7D0;">USSD (*384*99#)</span>
            <div class="phone-mock">
                <div class="ussd-screen">
                    PROOFWORK ESCROW<br>
                    1. Mark Day 2 Done (Plumbing)<br>
                    2. Check Wallet Balance<br>
                    3. Share Credit Code<br><br>
                    Reply with option:<br>
                    &gt; 1<br><br>
                    Done marked! Client has 24h to review. Release expected tomorrow 5:00 PM.
                </div>
            </div>
        </div>

        <div class="channel-card">
            <span class="tag" style="background: #065F46; color: #A7F3D0;">WhatsApp Business</span>
            <div class="phone-mock" style="background: #0B141A;">
                <div class="chat-msg" style="background: #202C33; color: #E9EDEF;">
                    📢 <strong>Proofwork Daily Alert:</strong><br>
                    Emem reported Day 2 Done for "Wall Plastering".<br>
                    Daily Release: ₦15,000.
                </div>
                <div class="chat-msg chat-out">
                    CONFIRM
                </div>
                <div class="chat-msg" style="background: #202C33; color: #E9EDEF;">
                    ✅ ₦15,000 disbursed to Emem via Paga. Day 2 settled!
                </div>
            </div>
        </div>

        <div class="channel-card">
            <span class="tag" style="background: #1E40AF; color: #BFDBFE;">Telegram Bot (Primary)</span>
            <div class="phone-mock" style="background: #17212B;">
                <div class="chat-msg" style="background: #182533; color: #FFF;">
                    🛠️ <strong>Job #PW-8492 Active</strong><br>
                    Day 1/3: ₦14,000<br>
                    Status: Done Today reported<br><br>
                    [✅ Confirm Release] [⚠️ Dispute Work]
                </div>
            </div>
        </div>
    </div>
</body>
</html>
"""
    return HTMLResponse(content=html)
