from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.services.report_service import ReportService

router = APIRouter(prefix="/reports", tags=["Lender Reports"])
report_service = ReportService()


@router.get("/{token}")
async def get_verified_lender_report(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Public, token-gated endpoint for microfinance institutions and lenders.
    Returns the worker's verified credit report with tokenized payers (NDPA-compliant)
    and an immutable audit score.
    """
    report = await report_service.generate_lender_report(db, token)
    if not report:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Verification report not found or invalid token.",
        )

    if report.get("error") == "CONSENT_EXPIRED_OR_REVOKED":
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Worker consent for this report has expired or was revoked.",
        )

    return report
