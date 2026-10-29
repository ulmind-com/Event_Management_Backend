"""
User KYC Verification Router
============================
Strictly for regular 'USER' role accounts.
Other roles (Admin, Super Admin, Judge, Volunteer) do not require Aadhaar KYC.
"""

import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field

from app.api.deps import get_current_user_allow_incomplete
from app.core.security import decode_token
from app.core.database import user_collection
from app.services.aadhaar_kyc_service import AadhaarKYCService

logger = logging.getLogger("eventora.routes.user_kyc")

router = APIRouter()


class SandboxAadhaarVerifyReq(BaseModel):
    aadhaar_number: str = Field(
        ...,
        description="12-digit Aadhaar number for sandbox testing (must pass Verhoeff checksum)",
        json_schema_extra={"example": "999999999999"},
    )


def _require_user_role(current_user: dict):
    """Enforce that Aadhaar KYC is strictly and solely for regular 'USER' role accounts."""
    role = current_user.get("role", "USER")
    if role not in ("USER", None):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Aadhaar KYC verification is only applicable to User accounts. Current role '{role}' is exempt.",
        )


async def get_kyc_user(current_user: dict = Depends(get_current_user_allow_incomplete)):
    """
    Dependency that enforces KYC eligibility for both in-registration users and logged-in users.
    """
    _require_user_role(current_user)
    return current_user


optional_bearer = HTTPBearer(auto_error=False)


async def get_optional_kyc_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(optional_bearer),
) -> Optional[dict]:
    """
    Optional user dependency: Extracts user from Bearer token if provided,
    otherwise returns None without raising a 401 error.
    """
    if not credentials:
        return None
    token = credentials.credentials
    payload = decode_token(token)
    if not payload:
        return None
    email = payload.get("sub")
    if not email:
        return None
    user = await user_collection.find_one({"email": email})
    if user:
        _require_user_role(user)
    return user


@router.get("/status", summary="Get current user KYC status")
async def get_my_kyc_status(current_user: dict = Depends(get_kyc_user)):
    """
    Returns the current user's KYC verification status.
    Applicable only to accounts with role 'USER'.
    """
    email = current_user["email"]

    kyc_doc = await AadhaarKYCService.get_kyc_status(email)
    if not kyc_doc:
        return {
            "email": email,
            "status": current_user.get("kyc_status", "NOT_SUBMITTED"),
            "aadhaar_masked": current_user.get("kyc_aadhaar_masked"),
            "source": current_user.get("kyc_source", "DIRECT"),
            "verified_at": current_user.get("kyc_verified_at"),
            "is_verified": current_user.get("kyc_status") == "VERIFIED",
        }

    return {
        "email": email,
        "status": kyc_doc.get("status", "NOT_SUBMITTED"),
        "aadhaar_masked": kyc_doc.get("aadhaar_masked"),
        "source": kyc_doc.get("verification_source", "AADHAAR_KYC"),
        "verified_at": kyc_doc.get("verified_at"),
        "is_verified": kyc_doc.get("status") == "VERIFIED",
        "verhoeff_valid": kyc_doc.get("verhoeff_valid", False),
        "admin_override": kyc_doc.get("admin_override", False),
    }


@router.post("/verify-document", summary="Document upload for Aadhaar KYC")
async def verify_aadhaar_document(
    govt_id_proof: UploadFile = File(...),
    current_user: dict = Depends(get_kyc_user),
):
    """
    Upload Aadhaar card image or PDF for automated verification.
    """
    email = current_user["email"]
    content = await govt_id_proof.read()
    if not content or len(content) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    kyc_result = await AadhaarKYCService.verify_and_register(
        email, content, filename=govt_id_proof.filename
    )

    if kyc_result.get("kyc_status") == "DUPLICATE":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=kyc_result.get("message"),
        )

    return {
        "message": kyc_result.get("message"),
        "kyc_status": kyc_result.get("kyc_status"),
        "aadhaar_masked": kyc_result.get("aadhaar_masked"),
        "source": kyc_result.get("source"),
    }
