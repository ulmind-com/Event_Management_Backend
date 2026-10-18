import logging
from fastapi import APIRouter, Depends, HTTPException, Query, status
from typing import Optional
from pydantic import BaseModel

from app.core.admin_auth import get_current_admin
from app.services.aadhaar_kyc_service import AadhaarKYCService

logger = logging.getLogger("eventora.routes.admin_kyc")

router = APIRouter()


class KYCOverrideRequest(BaseModel):
    status: str  # "VERIFIED" or "REJECTED"
    reason: str


# ── Admin KYC Management Endpoints ──


@router.get("/records", summary="List KYC verification records")
async def list_kyc_records(
    status_filter: Optional[str] = Query(
        None,
        description="Filter by KYC status: VERIFIED, PENDING, DUPLICATE, EXTRACTION_FAILED, REJECTED",
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    admin: dict = Depends(get_current_admin),
):
    """
    Returns paginated list of KYC verification records with optional status filter.
    Admin only.
    """
    return await AadhaarKYCService.list_kyc_records(
        status_filter=status_filter, page=page, page_size=page_size
    )


@router.get("/records/{user_email}", summary="Get KYC status for a specific user")
async def get_user_kyc(
    user_email: str,
    admin: dict = Depends(get_current_admin),
):
    """
    Returns the KYC verification record for a specific user.
    """
    record = await AadhaarKYCService.get_kyc_status(user_email)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No KYC record found for this user.",
        )
    return record


@router.post(
    "/records/{user_email}/override",
    summary="Admin override KYC status",
)
async def override_kyc_status(
    user_email: str,
    req: KYCOverrideRequest,
    admin: dict = Depends(get_current_admin),
):
    """
    Manually override KYC status. Allowed values: VERIFIED, REJECTED.
    Requires a reason for audit trail.
    """
    if req.status not in ("VERIFIED", "REJECTED"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Status must be either 'VERIFIED' or 'REJECTED'.",
        )

    result = await AadhaarKYCService.admin_override_kyc(
        user_email=user_email,
        new_status=req.status,
        admin_email=admin["email"],
        reason=req.reason,
    )
    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="KYC record not found or no change was made.",
        )
    return {"message": f"KYC status updated to {req.status}", "record": result}


@router.get("/analytics", summary="KYC verification analytics")
async def get_kyc_analytics(
    admin: dict = Depends(get_current_admin),
):
    """
    Returns KYC verification analytics: total, verified, pending, duplicates, etc.
    """
    return await AadhaarKYCService.get_kyc_analytics()
