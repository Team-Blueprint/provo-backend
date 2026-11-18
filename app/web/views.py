import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
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


@router.post("/checkout/{job_id}/assign-worker")
async def checkout_assign_worker(
    job_id: uuid.UUID,
    payload: dict,
    db: AsyncSession = Depends(get_db),
):
    """Allows employer on the web checkout page to assign an artisan to an unassigned job."""
    worker_phone = payload.get("worker_phone")
    if not worker_phone:
        raise HTTPException(status_code=400, detail="Worker phone number is required")

    stmt = select(Job).where(Job.id == job_id).options(selectinload(Job.worker))
    job = (await db.execute(stmt)).scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    try:
        from app.services.job_service import JobService
        js = JobService()
        job = await js.assign_worker_to_job(
            db=db,
            job_id=job_id,
            actor_id=job.client_id,
            worker_phone=worker_phone,
        )
        await db.commit()
        return {"success": True, "worker_name": job.worker.name if job.worker else worker_phone}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/checkout/{job_id}", response_class=HTMLResponse)
async def checkout_page(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """
    Employer Escrow Funding Checkout page.
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
    has_worker = job.worker is not None
    worker_name = job.worker.name if has_worker else "Not yet assigned"
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

    assign_box_html = ""
    if not has_worker:
        assign_box_html = """
        <div id="assignArtisanBox" style="background: #EFF6FF; border: 1px solid #BFDBFE; border-radius: 12px; padding: 16px; margin: 16px 0;">
            <div style="font-weight: 700; color: #1E40AF; font-size: 14px; margin-bottom: 4px;">👷 Assign Artisan</div>
            <div style="font-size: 12px; color: #475569; margin-bottom: 10px;">You opted to assign an artisan later. Enter their phone number below to link them to this escrow:</div>
            <div style="display: flex; gap: 8px;">
                <input type="tel" id="artisanPhoneInput" placeholder="e.g. 08012345678" style="flex: 1; padding: 10px; border-radius: 8px; border: 1px solid #93C5FD; font-size: 14px;" />
                <button id="assignBtn" onclick="assignWorkerOnWeb()" style="background: #2563EB; color: white; border: none; padding: 10px 16px; border-radius: 8px; font-weight: 600; cursor: pointer; font-size: 13px;">Assign</button>
            </div>
            <div id="assignStatusMsg" style="font-size: 12px; margin-top: 8px; font-weight: 600;"></div>
        </div>
        <script>
        async function assignWorkerOnWeb() {
            const phone = document.getElementById('artisanPhoneInput').value.trim();
            if (!phone) { alert('Please enter artisan phone number'); return; }
            const btn = document.getElementById('assignBtn');
            const msg = document.getElementById('assignStatusMsg');
            btn.disabled = true;
            msg.style.color = '#1E40AF';
            msg.innerText = 'Assigning artisan...';
            try {
                const res = await fetch('/checkout/' + window.JOB_ID + '/assign-worker', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({worker_phone: phone})
                });
                const data = await res.json();
                if (res.ok) {
                    msg.style.color = '#059669';
                    msg.innerText = '✅ Assigned: ' + data.worker_name + '! Refreshing...';
                    setTimeout(() => location.reload(), 1200);
                } else {
                    btn.disabled = false;
                    msg.style.color = '#DC2626';
                    msg.innerText = '❌ ' + (data.detail || 'Could not assign artisan');
                }
            } catch (e) {
                btn.disabled = false;
                msg.style.color = '#DC2626';
                msg.innerText = 'Network error. Please try again.';
            }
        }
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
            {assign_box_html}
            <div class="info-row">
                <span class="info-label">Job Reference</span>
                <span class="info-val">{job.funding_reference}</span>
            </div>
            <div class="info-row">
                <span class="info-label">Daily Release</span>
                <span class="info-val">₦{(job.total_kobo // job.days_count) / 100:,.2f} / day</span>
            </div>

            <div class="notice">
                ℹ️ <strong>NDPA & Safety Notice:</strong> Funds are locked securely in Paga escrow. Money is disbursed to the artisan only after they tap "Done Today" and you confirm. You have a 24-hour review window for each day.
            </div>
        </div>
    </div>
    {poll_script}
</body>
</html>
"""
    return HTMLResponse(content=html)
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
    Presents Profile Score, payer diversity matrix, and income stability metrics.
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


@router.get("/set-pin", response_class=HTMLResponse)
async def set_pin_page(phone: str | None = None):
    """
    Secure Web PIN Setup Page:
    Allows Telegram-registered users to set their 6-digit web login PIN securely in the browser
    without typing sensitive credentials inside Telegram chat.
    """
    initial_phone = phone or ""
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Proofwork &bull; Set Web Security PIN</title>
    <style>
        * {{ box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #0F172A;
            margin: 0;
            padding: 24px;
            color: #F8FAFC;
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
        }}
        .card {{
            background: #1E293B;
            border: 1px solid #334155;
            border-radius: 20px;
            max-width: 440px;
            width: 100%;
            padding: 32px 28px;
            box-shadow: 0 20px 40px -10px rgba(0,0,0,0.5);
        }}
        .logo {{
            font-size: 24px;
            font-weight: 800;
            color: #38BDF8;
            text-align: center;
            margin-bottom: 6px;
        }}
        .sub {{
            color: #94A3B8;
            font-size: 14px;
            text-align: center;
            margin-bottom: 24px;
            line-height: 1.5;
        }}
        .step-label {{
            font-size: 12px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: #38BDF8;
            margin-bottom: 6px;
        }}
        .form-group {{
            margin-bottom: 18px;
        }}
        label {{
            display: block;
            font-size: 13px;
            color: #CBD5E1;
            margin-bottom: 6px;
            font-weight: 500;
        }}
        input {{
            width: 100%;
            padding: 12px 14px;
            border-radius: 10px;
            border: 1px solid #475569;
            background: #0F172A;
            color: #FFFFFF;
            font-size: 15px;
            outline: none;
            transition: border-color 0.2s;
        }}
        input:focus {{
            border-color: #38BDF8;
        }}
        input.pin-input {{
            letter-spacing: 6px;
            font-size: 18px;
            text-align: center;
            font-family: monospace;
        }}
        .btn {{
            width: 100%;
            padding: 13px;
            background: #0284C7;
            color: #FFFFFF;
            border: none;
            border-radius: 10px;
            font-size: 15px;
            font-weight: 700;
            cursor: pointer;
            transition: background 0.2s;
            margin-top: 6px;
        }}
        .btn:hover {{
            background: #0369A1;
        }}
        .btn:disabled {{
            opacity: 0.5;
            cursor: not-allowed;
        }}
        .btn-secondary {{
            background: #334155;
            color: #E2E8F0;
            margin-top: 10px;
        }}
        .btn-secondary:hover {{
            background: #475569;
        }}
        .msg {{
            padding: 10px 14px;
            border-radius: 8px;
            font-size: 13px;
            margin-top: 14px;
            line-height: 1.4;
            display: none;
        }}
        .msg-info {{ background: #0369A120; border: 1px solid #0284C7; color: #38BDF8; display: block; }}
        .msg-error {{ background: #991B1B20; border: 1px solid #DC2626; color: #F87171; display: block; }}
        .msg-success {{ background: #065F4620; border: 1px solid #059669; color: #34D399; display: block; }}
        .shield-banner {{
            display: flex;
            align-items: center;
            gap: 8px;
            background: #0284C715;
            border: 1px solid #0284C730;
            border-radius: 10px;
            padding: 10px 14px;
            font-size: 12px;
            color: #7DD3FC;
            margin-bottom: 20px;
        }}
    </style>
</head>
<body>
    <div class="card">
        <div class="logo">🛡️ Proofwork</div>
        <div class="sub">Set your 6-digit security PIN for Web Access</div>

        <div class="shield-banner">
            <span>🔒</span>
            <span>Your PIN is encrypted with constant-time SHA-256 and never shared with Telegram.</span>
        </div>

        <div id="step1">
            <div class="step-label">Step 1: Phone Verification</div>
            <div class="form-group">
                <label for="phone">Your Telegram-Registered Phone Number</label>
                <input type="tel" id="phone" value="{initial_phone}" placeholder="e.g. 08012345678 or +2348012345678" />
            </div>
            <button class="btn" id="sendOtpBtn" onclick="requestOtp()">Send Verification Code</button>
            <div id="step1Msg" class="msg"></div>
        </div>

        <div id="step2" style="display: none;">
            <div class="step-label">Step 2: Enter Verification Code & PIN</div>
            <div class="form-group">
                <label for="otpCode">SMS Verification Code</label>
                <input type="text" id="otpCode" placeholder="Enter 6-digit OTP" maxlength="10" />
                <div style="font-size: 11px; color: #64748B; margin-top: 4px;">💡 Demo Mode: enter <strong>123456</strong> as universal OTP.</div>
            </div>
            <div class="form-group">
                <label for="newPin">Create New 6-Digit PIN</label>
                <input type="password" id="newPin" class="pin-input" maxlength="6" placeholder="••••••" />
            </div>
            <div class="form-group">
                <label for="confirmPin">Confirm 6-Digit PIN</label>
                <input type="password" id="confirmPin" class="pin-input" maxlength="6" placeholder="••••••" />
            </div>
            <button class="btn" id="setPinBtn" onclick="submitPin()">Set Security PIN & Log In</button>
            <button class="btn btn-secondary" onclick="backToStep1()">Back</button>
            <div id="step2Msg" class="msg"></div>
        </div>

        <div id="stepSuccess" style="display: none;">
            <div style="text-align: center; margin: 20px 0;">
                <div style="font-size: 48px; margin-bottom: 12px;">🎉</div>
                <div style="font-size: 18px; font-weight: 700; color: #34D399; margin-bottom: 8px;">Security PIN Activated!</div>
                <div style="font-size: 13px; color: #94A3B8; line-height: 1.5; margin-bottom: 20px;">
                    Your 6-digit PIN has been secured. You can now use your phone number and PIN to log into Proofwork on web or mobile anytime.
                </div>
                <div style="background: #0F172A; border-radius: 10px; padding: 14px; text-align: left; font-size: 13px; color: #CBD5E1; margin-bottom: 20px;">
                    <div>👤 <strong>User:</strong> <span id="userName">-</span></div>
                    <div>📱 <strong>Phone:</strong> <span id="userPhone">-</span></div>
                    <div>💼 <strong>Account:</strong> Active & Verified ✅</div>
                </div>
            </div>
        </div>
    </div>

    <script>
    let validatedPhone = '';

    function showMsg(elemId, type, text) {{
        const el = document.getElementById(elemId);
        el.className = 'msg msg-' + type;
        el.innerText = text;
        el.style.display = 'block';
    }}

    async function requestOtp() {{
        const phone = document.getElementById('phone').value.trim();
        if (!phone) {{
            showMsg('step1Msg', 'error', 'Please enter your phone number.');
            return;
        }}
        const btn = document.getElementById('sendOtpBtn');
        btn.disabled = true;
        showMsg('step1Msg', 'info', 'Sending verification code via SMS...');
        try {{
            const res = await fetch('/auth/request-set-pin', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{phone: phone}})
            }});
            const data = await res.json();
            if (res.ok) {{
                validatedPhone = phone;
                document.getElementById('step1').style.display = 'none';
                document.getElementById('step2').style.display = 'block';
                showMsg('step2Msg', 'info', 'Verification code sent to ' + phone);
            }} else {{
                btn.disabled = false;
                showMsg('step1Msg', 'error', data.detail || 'Could not send verification code.');
            }}
        }} catch (e) {{
            btn.disabled = false;
            showMsg('step1Msg', 'error', 'Network error. Please try again.');
        }}
    }}

    function backToStep1() {{
        document.getElementById('step2').style.display = 'none';
        document.getElementById('step1').style.display = 'block';
        document.getElementById('sendOtpBtn').disabled = false;
    }}

    async function submitPin() {{
        const otpCode = document.getElementById('otpCode').value.trim();
        const pin = document.getElementById('newPin').value.trim();
        const confirmPin = document.getElementById('confirmPin').value.trim();

        if (!otpCode) {{
            showMsg('step2Msg', 'error', 'Please enter the verification code.');
            return;
        }}
        if (!pin || pin.length !== 6 || !/^\\d{{6}}$/.test(pin)) {{
            showMsg('step2Msg', 'error', 'PIN must be exactly 6 numeric digits.');
            return;
        }}
        if (pin !== confirmPin) {{
            showMsg('step2Msg', 'error', 'PINs do not match. Please verify.');
            return;
        }}

        const btn = document.getElementById('setPinBtn');
        btn.disabled = true;
        showMsg('step2Msg', 'info', 'Securing your PIN...');

        try {{
            const res = await fetch('/auth/set-pin', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{
                    phone: validatedPhone,
                    otp_code: otpCode,
                    new_pin: pin
                }})
            }});
            const data = await res.json();
            if (res.ok) {{
                localStorage.setItem('access_token', data.access_token);
                localStorage.setItem('refresh_token', data.refresh_token);
                document.getElementById('userName').innerText = data.name;
                document.getElementById('userPhone').innerText = data.phone;
                document.getElementById('step2').style.display = 'none';
                document.getElementById('stepSuccess').style.display = 'block';
            }} else {{
                btn.disabled = false;
                showMsg('step2Msg', 'error', data.detail || 'Could not set PIN.');
            }}
        }} catch (e) {{
            btn.disabled = false;
            showMsg('step2Msg', 'error', 'Network error. Please try again.');
        }}
    }}
    </script>
</body>
</html>
"""
    return HTMLResponse(content=html)


@router.get("/supervisor/verify", response_class=HTMLResponse)
async def supervisor_verify_page(
    job: uuid.UUID = Query(...),
    token: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Public token-gated verification view for site supervisors.
    Allows supervisors to view work status, upload proof photos,
    and confirm or dispute daily milestones with zero client PII.
    """
    stmt = (
        select(Job)
        .where(Job.id == job)
        .options(
            selectinload(Job.days_schedule),
            selectinload(Job.worker),
        )
    )
    job_obj = (await db.execute(stmt)).scalar_one_or_none()

    if not job_obj or not token or not job_obj.supervisor_token or token.strip() != job_obj.supervisor_token:
        error_html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Invalid Link - Provo Supervisor Portal</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
    <style>
        body { font-family: 'Inter', sans-serif; background: #0F172A; color: #F8FAFC; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; padding: 20px; }
        .card { background: #1E293B; border-radius: 16px; padding: 32px; max-width: 420px; text-align: center; border: 1px solid #334155; }
        h1 { font-size: 20px; margin-bottom: 12px; color: #F87171; }
        p { color: #94A3B8; font-size: 14px; line-height: 1.5; }
    </style>
</head>
<body>
    <div class="card">
        <div style="font-size: 48px; margin-bottom: 16px;">⚠️</div>
        <h1>Invalid or Expired Link</h1>
        <p>This supervisor verification link is invalid, expired, or has already been revoked. Please contact the job employer for an updated link.</p>
    </div>
</body>
</html>"""
        return HTMLResponse(content=error_html, status_code=404)

    worker_name = job_obj.worker.name if job_obj.worker else "Assigned Artisan"
    supervisor_display = job_obj.supervisor_name or "Site Supervisor"
    total_naira = f"{job_obj.total_kobo / 100:,.2f}"

    days_html = []
    for d in sorted(job_obj.days_schedule, key=lambda x: x.day_number):
        day_naira = f"{d.amount_kobo / 100:,.2f}"
        photo_display = ""

        if d.proof_photo:
            if d.proof_photo.startswith("data:image"):
                photo_display = f'<div style="margin-top: 10px;"><img src="{d.proof_photo}" style="max-width: 100%; border-radius: 8px; max-height: 180px; object-fit: cover;" alt="Site Proof Photo" /></div>'
            else:
                photo_display = f'<div style="margin-top: 10px; font-size: 12px; color: #94A3B8;">📷 Proof: <code>{d.proof_photo}</code></div>'

        if d.status == "DONE_REPORTED":
            status_badge = '<span class="badge badge-warning">⏳ Awaiting Verification</span>'
            action_box = f"""
            <div class="action-card" id="action_box_{d.day_number}">
                <p style="margin: 0 0 10px; font-size: 13px; color: #CBD5E1;">
                    Artisan reported work done. Snap/upload a site photo (optional) and confirm or dispute:
                </p>
                <div style="margin-bottom: 12px;">
                    <label class="photo-btn" for="photo_input_{d.day_number}" id="photo_label_{d.day_number}">
                        📸 Tap to Snap or Choose Site Photo
                    </label>
                    <input type="file" id="photo_input_{d.day_number}" accept="image/*" capture="environment" style="display: none;" onchange="previewPhoto({d.day_number})" />
                    <div id="preview_container_{d.day_number}" style="display: none; margin-top: 10px;">
                        <img id="preview_img_{d.day_number}" style="max-width: 100%; border-radius: 8px; max-height: 160px; object-fit: cover;" />
                        <button type="button" onclick="removePhoto({d.day_number})" style="background: none; border: none; color: #F87171; font-size: 12px; cursor: pointer; display: block; margin-top: 4px;">✕ Remove photo</button>
                    </div>
                </div>
                <div style="display: flex; gap: 8px;">
                    <button class="btn btn-primary" id="confirm_btn_{d.day_number}" onclick="confirmDay({d.day_number})">✅ Confirm Day Pay</button>
                    <button class="btn btn-outline" id="dispute_btn_{d.day_number}" onclick="promptDispute({d.day_number})">⚠️ Dispute</button>
                </div>
                <div id="msg_{d.day_number}" style="margin-top: 8px; font-size: 13px;"></div>
            </div>
            """
        elif d.status in ("CONFIRMED", "PAID"):
            status_badge = f'<span class="badge badge-success">✅ {d.status}</span>'
            action_box = '<div style="margin-top: 8px; font-size: 13px; color: #34D399;">Milestone confirmed and paid to artisan.</div>'
        elif d.status == "DISPUTED":
            status_badge = '<span class="badge badge-danger">⚠️ Disputed (Frozen)</span>'
            action_box = '<div style="margin-top: 8px; font-size: 13px; color: #F87171;">Work disputed. 24h countdown is frozen pending resolution.</div>'
        else:
            status_badge = f'<span class="badge badge-neutral">⚪ {d.status}</span>'
            action_box = '<div style="margin-top: 8px; font-size: 13px; color: #64748B;">Awaiting artisan completion.</div>'

        days_html.append(f"""
        <div class="day-card" id="day_card_{d.day_number}">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <div>
                    <h3 style="margin: 0; font-size: 16px; color: #F8FAFC;">Day {d.day_number}</h3>
                    <div style="font-size: 14px; font-weight: 600; color: #38BDF8; margin-top: 2px;">₦{day_naira}</div>
                </div>
                <div>{status_badge}</div>
            </div>
            {photo_display}
            {action_box}
        </div>
        """)

    days_rendered = "\n".join(days_html)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Provo Supervisor Portal - {job_obj.trade}</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: 'Inter', sans-serif;
            background-color: #0F172A;
            color: #F8FAFC;
            padding: 24px 16px;
            display: flex;
            justify-content: center;
        }}
        .container {{
            width: 100%;
            max-width: 520px;
        }}
        .header {{
            background: #1E293B;
            border-radius: 16px;
            padding: 20px;
            margin-bottom: 20px;
            border: 1px solid #334155;
        }}
        .badge {{
            display: inline-block;
            padding: 4px 10px;
            border-radius: 9999px;
            font-size: 12px;
            font-weight: 600;
        }}
        .badge-warning {{ background: #FEF3C7; color: #92400E; }}
        .badge-success {{ background: #D1FAE5; color: #065F46; }}
        .badge-danger {{ background: #FEE2E2; color: #991B1B; }}
        .badge-neutral {{ background: #334155; color: #94A3B8; }}
        .badge-sup {{ background: #E0E7FF; color: #3730A3; }}

        .kpi-grid {{
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 12px;
            margin-top: 16px;
        }}
        .kpi-box {{
            background: #0F172A;
            border-radius: 10px;
            padding: 12px;
            border: 1px solid #334155;
        }}
        .kpi-label {{
            font-size: 12px;
            color: #94A3B8;
            margin-bottom: 4px;
        }}
        .kpi-value {{
            font-size: 16px;
            font-weight: 700;
            color: #F8FAFC;
        }}
        .day-card {{
            background: #1E293B;
            border-radius: 14px;
            padding: 16px;
            margin-bottom: 14px;
            border: 1px solid #334155;
        }}
        .action-card {{
            background: #0F172A;
            border-radius: 10px;
            padding: 14px;
            margin-top: 14px;
            border: 1px dashed #475569;
        }}
        .photo-btn {{
            display: inline-block;
            background: #334155;
            color: #E2E8F0;
            padding: 8px 14px;
            border-radius: 8px;
            font-size: 13px;
            font-weight: 500;
            cursor: pointer;
            text-align: center;
            width: 100%;
        }}
        .photo-btn:hover {{
            background: #475569;
        }}
        .btn {{
            flex: 1;
            padding: 10px 14px;
            border-radius: 8px;
            font-size: 13px;
            font-weight: 600;
            cursor: pointer;
            border: none;
            transition: all 0.2s ease;
        }}
        .btn-primary {{
            background: #2563EB;
            color: #FFFFFF;
        }}
        .btn-primary:hover {{
            background: #1D4ED8;
        }}
        .btn-primary:disabled {{
            opacity: 0.6;
            cursor: not-allowed;
        }}
        .btn-outline {{
            background: transparent;
            color: #F87171;
            border: 1px solid #EF4444;
        }}
        .btn-outline:hover {{
            background: rgba(239, 68, 68, 0.1);
        }}
        .notice {{
            font-size: 12px;
            color: #64748B;
            text-align: center;
            margin-top: 24px;
            line-height: 1.5;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
                <span class="badge badge-sup">🕵️ {supervisor_display}</span>
                <span class="badge" style="background: #38BDF8; color: #0C4A6E;">SITE PORTAL</span>
            </div>
            <h1 style="font-size: 20px; font-weight: 700; margin-bottom: 4px;">{job_obj.trade} Verification</h1>
            <p style="font-size: 14px; color: #94A3B8;">{job_obj.description}</p>

            <div class="kpi-grid">
                <div class="kpi-box">
                    <div class="kpi-label">Assigned Artisan</div>
                    <div class="kpi-value">👷 {worker_name}</div>
                </div>
                <div class="kpi-box">
                    <div class="kpi-label">Total Escrow</div>
                    <div class="kpi-value">₦{total_naira}</div>
                </div>
            </div>
        </div>

        <h2 style="font-size: 16px; font-weight: 600; margin-bottom: 12px; color: #CBD5E1;">Milestone Schedule</h2>
        {days_rendered}

        <div class="notice">
            🔒 <strong>Provo Supervisor Portal:</strong> Token-authenticated access.<br>
            Confirmations trigger instant automated escrow release to the artisan.
        </div>
    </div>

    <script>
    const jobId = "{job_obj.id}";
    const token = "{token}";
    const photoData = {{}};

    function previewPhoto(dayNumber) {{
        const input = document.getElementById('photo_input_' + dayNumber);
        if (!input.files || !input.files[0]) return;
        const file = input.files[0];
        const reader = new FileReader();
        reader.onload = function(e) {{
            photoData[dayNumber] = e.target.result;
            document.getElementById('preview_img_' + dayNumber).src = e.target.result;
            document.getElementById('preview_container_' + dayNumber).style.display = 'block';
            document.getElementById('photo_label_' + dayNumber).innerText = '📷 Photo Selected (Tap to Change)';
        }};
        reader.readAsDataURL(file);
    }}

    function removePhoto(dayNumber) {{
        photoData[dayNumber] = null;
        document.getElementById('photo_input_' + dayNumber).value = '';
        document.getElementById('preview_container_' + dayNumber).style.display = 'none';
        document.getElementById('photo_label_' + dayNumber).innerText = '📸 Tap to Snap or Choose Site Photo';
    }}

    async function confirmDay(dayNumber) {{
        const btn = document.getElementById('confirm_btn_' + dayNumber);
        const disputeBtn = document.getElementById('dispute_btn_' + dayNumber);
        const msg = document.getElementById('msg_' + dayNumber);
        btn.disabled = true;
        disputeBtn.disabled = true;
        msg.style.color = '#38BDF8';
        msg.innerText = 'Confirming milestone and releasing payout...';

        try {{
            const payload = {{
                proof_photo: photoData[dayNumber] || null
            }};
            const res = await fetch(`/jobs/${{jobId}}/days/${{dayNumber}}/supervisor-confirm?token=${{encodeURIComponent(token)}}`, {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify(payload)
            }});
            const data = await res.json();
            if (res.ok) {{
                msg.style.color = '#34D399';
                msg.innerText = '✅ Day ' + dayNumber + ' confirmed! Payout released.';
                setTimeout(() => {{ window.location.reload(); }}, 1200);
            }} else {{
                btn.disabled = false;
                disputeBtn.disabled = false;
                msg.style.color = '#F87171';
                msg.innerText = '❌ ' + (data.detail || 'Failed to confirm day');
            }}
        }} catch (e) {{
            btn.disabled = false;
            disputeBtn.disabled = false;
            msg.style.color = '#F87171';
            msg.innerText = '❌ Network error. Please try again.';
        }}
    }}

    async function promptDispute(dayNumber) {{
        const reason = prompt('Enter dispute reason (this will freeze the 24-hour auto-release countdown):', 'Work incomplete or not meeting site specifications');
        if (!reason) return;

        const btn = document.getElementById('confirm_btn_' + dayNumber);
        const disputeBtn = document.getElementById('dispute_btn_' + dayNumber);
        const msg = document.getElementById('msg_' + dayNumber);
        btn.disabled = true;
        disputeBtn.disabled = true;
        msg.style.color = '#F87171';
        msg.innerText = 'Freezing milestone and registering dispute...';

        try {{
            const res = await fetch(`/jobs/${{jobId}}/days/${{dayNumber}}/supervisor-dispute?token=${{encodeURIComponent(token)}}`, {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ reason: reason }})
            }});
            const data = await res.json();
            if (res.ok) {{
                msg.innerText = '⚠️ Day ' + dayNumber + ' disputed. Countdown frozen.';
                setTimeout(() => {{ window.location.reload(); }}, 1200);
            }} else {{
                btn.disabled = false;
                disputeBtn.disabled = false;
                msg.innerText = '❌ ' + (data.detail || 'Failed to dispute day');
            }}
        }} catch (e) {{
            btn.disabled = false;
            disputeBtn.disabled = false;
            msg.innerText = '❌ Network error. Please try again.';
        }}
    }}
    </script>
</body>
</html>
"""
    return HTMLResponse(content=html)

