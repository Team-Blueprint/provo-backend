from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.reports.schemas import (
    ConsentCreate,
    LenderReportResponse,
    ShareLinkCreate,
    ShareLinkResponse,
)
from app.reports.service import ReportService

router = APIRouter(prefix="/reports", tags=["Reports"])
report_service = ReportService()


@router.post("/share-link", response_model=ShareLinkResponse, status_code=status.HTTP_201_CREATED)
async def create_share_link(
    payload: ShareLinkCreate,
    db: AsyncSession = Depends(get_db),
):
    share_link = await report_service.create_share_link(db, payload)
    share_url = f"{settings.BASE_URL}/reports/view/{share_link.token}"
    return ShareLinkResponse(
        id=share_link.id,
        worker_id=share_link.worker_id,
        token=share_link.token,
        share_url=share_url,
        expires_at=share_link.expires_at,
        is_revoked=share_link.is_revoked,
    )


@router.post("/consent", status_code=status.HTTP_201_CREATED)
async def create_consent(
    payload: ConsentCreate,
    db: AsyncSession = Depends(get_db),
):
    consent = await report_service.create_consent(db, payload)
    return {"status": "success", "consent_id": str(consent.id)}


@router.get("/view/{share_token}", response_model=LenderReportResponse)
async def view_lender_report(
    share_token: str,
    db: AsyncSession = Depends(get_db),
):
    try:
        report = await report_service.get_lender_report_by_token(db, share_token)
        return report
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
