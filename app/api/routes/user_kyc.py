"""
User KYC & DigiLocker Verification Router
==========================================
Strictly for regular 'USER' role accounts.
Other roles (Admin, Super Admin, Judge, Volunteer) do not require Aadhaar KYC.
"""

import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query, status
from pydantic import BaseModel, Field

from app.api.deps import get_current_user
from app.services.aadhaar_kyc_service import AadhaarKYCService
from app.services.digilocker_service import DigiLockerService

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
    if role != "USER":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Aadhaar KYC verification is only applicable to User accounts. Current role '{role}' is exempt.",
        )


@router.get("/status", summary="Get current user KYC status")
async def get_my_kyc_status(current_user: dict = Depends(get_current_user)):
    """
    Returns the current user's KYC verification status.
    Applicable only to accounts with role 'USER'.
    """
    _require_user_role(current_user)
    email = current_user["email"]

    kyc_doc = await AadhaarKYCService.get_kyc_status(email)
    if not kyc_doc:
        return {
            "email": email,
            "status": current_user.get("kyc_status", "NOT_SUBMITTED"),
            "aadhaar_masked": current_user.get("kyc_aadhaar_masked"),
            "verified_at": current_user.get("kyc_verified_at"),
            "is_verified": current_user.get("kyc_status") == "VERIFIED",
        }

    return {
        "email": email,
        "status": kyc_doc.get("status", "NOT_SUBMITTED"),
        "aadhaar_masked": kyc_doc.get("aadhaar_masked"),
        "source": kyc_doc.get("verification_source", "DOCUMENT"),
        "verified_at": kyc_doc.get("verified_at"),
        "is_verified": kyc_doc.get("status") == "VERIFIED",
        "verhoeff_valid": kyc_doc.get("verhoeff_valid", False),
        "admin_override": kyc_doc.get("admin_override", False),
    }


@router.post("/verify-document", summary="Upload Aadhaar document for KYC verification")
async def verify_aadhaar_document(
    govt_id_proof: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    """
    Upload Aadhaar card image (JPG/PNG) or DigiLocker e-Aadhaar PDF.
    Automatically extracts 12-digit Aadhaar number, performs Verhoeff checksum validation,
    and blocks duplicate Aadhaar registrations across accounts.
    """
    _require_user_role(current_user)
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
            detail=kyc_result.get(
                "message",
                "This Aadhaar card is already registered with another account. Duplicate registrations are not allowed.",
            ),
        )

    return {
        "message": kyc_result.get("message"),
        "kyc_status": kyc_result.get("kyc_status"),
        "aadhaar_masked": kyc_result.get("aadhaar_masked"),
        "source": kyc_result.get("source"),
        "verhoeff_valid": kyc_result.get("verhoeff_valid"),
    }


@router.post("/digilocker/initiate", summary="Initiate DigiLocker verification session")
async def initiate_digilocker(current_user: dict = Depends(get_current_user)):
    """
    Initiates DigiLocker verification.
    Returns authorization URL for the user to authenticate with DigiLocker.
    """
    _require_user_role(current_user)
    email = current_user["email"]

    return DigiLockerService.get_authorization_url(email)


@router.get("/digilocker/callback", summary="DigiLocker OAuth callback")
async def digilocker_callback(
    code: str = Query(..., description="OAuth authorization code"),
    state: str = Query(..., description="CSRF state parameter"),
):
    """
    Callback endpoint for DigiLocker OAuth redirection.
    Exchanges authorization code for access token and pulls verified e-Aadhaar.
    """
    try:
        result = await DigiLockerService.process_oauth_callback(code, state)
        if result.get("kyc_status") == "DUPLICATE":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=result.get("message"),
            )
        return {
            "message": "DigiLocker verification completed successfully.",
            "kyc_status": result.get("kyc_status"),
            "aadhaar_masked": result.get("aadhaar_masked"),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"DigiLocker callback processing error: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


@router.post(
    "/digilocker/sandbox-verify",
    summary="Simulate DigiLocker KYC in Sandbox (Dev/Test)",
)
async def sandbox_verify_aadhaar(
    req: SandboxAadhaarVerifyReq,
    current_user: dict = Depends(get_current_user),
):
    """
    Simulate DigiLocker verification with a 12-digit Aadhaar number for sandbox testing.
    Validates UIDAI Verhoeff checksum and blocks duplicates.
    """
    _require_user_role(current_user)
    email = current_user["email"]

    result = await DigiLockerService.verify_sandbox_aadhaar(email, req.aadhaar_number)

    if result.get("kyc_status") == "DUPLICATE":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=result.get("message"),
        )
    elif result.get("kyc_status") == "REJECTED":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("message"),
        )

    return result
