from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.enums import JobType
from app.jobs.schemas import (
    CashJobDraftCreate,
    JobConfirmRequest,
    JobResponse,
    ReceiptResponse,
)
from app.jobs.service import JobService
from app.jobs.web_views import (
    render_client_confirmation_page,
    render_message_page,
    render_receipt_success_page,
)

router = APIRouter(tags=["Jobs"])
job_service = JobService()


@router.post("/jobs/draft/cash", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def create_cash_job_draft(
    payload: CashJobDraftCreate,
    db: AsyncSession = Depends(get_db),
):
    try:
        job, _confirmation = await job_service.create_cash_job_draft(
            db=db,
            worker_id=payload.worker_id,
            amount_kobo=payload.amount_kobo,
            client_phone=payload.client_phone,
            job_type=payload.job_type,
        )
        return job
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/jobs/confirm", response_model=ReceiptResponse)
async def api_confirm_or_dispute_job(
    payload: JobConfirmRequest,
    db: AsyncSession = Depends(get_db),
):
    try:
        if payload.confirmed:
            _job, receipt = await job_service.confirm_job(
                db=db,
                confirmation_token=payload.confirmation_token,
                job_type=payload.job_type,
            )
            return receipt
        else:
            job, _dispute = await job_service.dispute_job(
                db=db,
                confirmation_token=payload.confirmation_token,
            )
            raise HTTPException(
                status_code=status.HTTP_200_OK,
                detail=f"Job {job.id} has been recorded as disputed.",
            )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/confirm/{confirmation_token}", response_class=HTMLResponse)
async def client_confirmation_web_page(
    confirmation_token: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Renders the web confirmation page for clients clicking the link in SMS/WhatsApp.
    """
    conf = await job_service.get_confirmation_by_token(db, confirmation_token)
    if not conf:
        return HTMLResponse(
            render_message_page("Link Invalid", "This confirmation link is invalid or does not exist.", is_error=True),
            status_code=404,
        )

    if conf.is_confirmed:
        if conf.job.receipt:
            return HTMLResponse(render_receipt_success_page(conf.job.receipt))
        return HTMLResponse(
            render_message_page("Already Confirmed", "This payment has already been confirmed and recorded.")
        )

    return HTMLResponse(render_client_confirmation_page(conf))


@router.post("/confirm/{confirmation_token}", response_class=HTMLResponse)
async def client_confirmation_submit(
    confirmation_token: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Handles form POST from the client web confirmation page.
    """
    # Accept either Form or JSON
    job_type_str: str | None = None
    confirmed_bool: bool = True

    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        body = await request.json()
        job_type_str = body.get("job_type")
        confirmed_bool = bool(body.get("confirmed", True))
    else:
        form = await request.form()
        job_type_str = form.get("job_type")  # type: ignore
        confirmed_str = form.get("confirmed", "true")
        confirmed_bool = str(confirmed_str).lower() in ("true", "1", "yes")

    selected_job_type: JobType | None = None
    if job_type_str:
        try:
            selected_job_type = JobType(str(job_type_str).upper())
        except ValueError:
            pass

    try:
        if confirmed_bool:
            _job, receipt = await job_service.confirm_job(
                db=db,
                confirmation_token=confirmation_token,
                job_type=selected_job_type,
            )
            return HTMLResponse(render_receipt_success_page(receipt))
        else:
            await job_service.dispute_job(db=db, confirmation_token=confirmation_token)
            return HTMLResponse(
                render_message_page(
                    "Dispute Recorded",
                    "You indicated that this payment was not for work. The job record has been marked disputed and excluded from scoring.",
                )
            )
    except ValueError as e:
        return HTMLResponse(
            render_message_page("Confirmation Error", str(e), is_error=True),
            status_code=400,
        )


@router.get("/jobs/verify/{receipt_number}", response_model=ReceiptResponse)
async def verify_receipt(
    receipt_number: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Public receipt verification endpoint.
    Returns JSON for API clients or HTML receipt view for browser requests.
    """
    receipt = await job_service.verify_receipt_by_number(db, receipt_number)
    if not receipt:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Receipt not found")

    accept_header = request.headers.get("accept", "")
    if "text/html" in accept_header:
        return HTMLResponse(render_receipt_success_page(receipt))
    return receipt


@router.post("/jobs/expire-stale")
async def expire_stale_jobs(
    db: AsyncSession = Depends(get_db),
):
    """Background or cron task to expire unanswered jobs."""
    count = await job_service.expire_stale_confirmations(db)
    return {"status": "success", "expired_jobs_count": count}
