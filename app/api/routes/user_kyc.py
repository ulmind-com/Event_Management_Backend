"""
User KYC & DigiLocker Verification Router
==========================================
Strictly for regular 'USER' role accounts.
Supports direct, zero-file DigiLocker Aadhaar verification (no PDF or image upload needed!).
Other roles (Admin, Super Admin, Judge, Volunteer) do not require Aadhaar KYC.
"""

import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from pydantic import BaseModel, Field

from app.api.deps import get_current_user_allow_incomplete
from app.services.aadhaar_kyc_service import AadhaarKYCService
from app.services.digilocker_service import DigiLockerService

logger = logging.getLogger("eventora.routes.user_kyc")

router = APIRouter()


class DigiLockerSendOTPReq(BaseModel):
    aadhaar_number: str = Field(
        ...,
        description="12-digit Aadhaar number (e.g. 999999999999)",
        json_schema_extra={"example": "999999999999"},
    )


class DigiLockerVerifyOTPReq(BaseModel):
    reference_id: str = Field(
        ...,
        description="Reference ID received from /digilocker/send-otp",
    )
    otp: str = Field(
        ...,
        description="6-digit OTP sent to Aadhaar-linked mobile (use 123456 in Sandbox)",
        json_schema_extra={"example": "123456"},
    )


class DigiLockerMobileSendOTPReq(BaseModel):
    mobile_number: str = Field(
        ...,
        description="10-digit mobile number registered with DigiLocker",
        json_schema_extra={"example": "9876543210"},
    )


class DigiLockerMobileVerifyOTPReq(BaseModel):
    reference_id: str = Field(
        ...,
        description="Reference ID received from send-otp",
    )
    otp: str = Field(
        ...,
        description="6-digit OTP sent to mobile",
        json_schema_extra={"example": "123456"},
    )
    security_pin: Optional[str] = Field(
        None,
        description="6-digit DigiLocker Security PIN",
        json_schema_extra={"example": "123456"},
    )


class DigiLockerPortalSendOTPReq(BaseModel):
    mobile_number: str
    user_email: Optional[str] = None


class DigiLockerPortalVerifyReq(BaseModel):
    reference_id: str
    otp: str
    security_pin: Optional[str] = None
    state: Optional[str] = None
    user_email: Optional[str] = None


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


# ════════════════════════════════════════════════
# 1. DIRECT DIGILOCKER OTP VERIFICATION (NO FILES!)
# ════════════════════════════════════════════════

@router.post(
    "/digilocker/send-otp",
    summary="Send OTP to Aadhaar-linked mobile via DigiLocker (No file upload needed)",
)
async def digilocker_send_otp(
    req: DigiLockerSendOTPReq,
    current_user: dict = Depends(get_kyc_user),
):
    """
    Step 1 of Direct DigiLocker KYC:
    - User enters 12-digit Aadhaar number.
    - System validates format & UIDAI Verhoeff checksum.
    - System pre-checks if this Aadhaar is already registered (blocks duplicates!).
    - DigiLocker sends OTP to citizen's Aadhaar-linked mobile number.
    """
    email = current_user["email"]
    result = await DigiLockerService.send_aadhaar_otp(email, req.aadhaar_number)

    if not result.get("success"):
        if result.get("is_duplicate"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=result.get("message"),
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("message"),
        )

    return result


@router.post(
    "/digilocker/verify-otp",
    summary="Verify DigiLocker OTP & fetch verified Govt ID details (No file upload needed)",
)
async def digilocker_verify_otp(
    req: DigiLockerVerifyOTPReq,
    current_user: dict = Depends(get_kyc_user),
):
    """
    Step 2 of Direct DigiLocker KYC:
    - User enters OTP.
    - System verifies OTP and pulls verified citizen details directly from DigiLocker (Name, DOB, Masked Aadhaar, Address).
    - Checks duplicate Aadhaar.
    - Automatically updates user's KYC status to VERIFIED without requiring any file/PDF upload!
    """
    email = current_user["email"]
    result = await DigiLockerService.verify_aadhaar_otp(
        req.reference_id, req.otp, user_email=email
    )

    if not result.get("success"):
        if result.get("kyc_status") == "DUPLICATE":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=result.get("message"),
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("message"),
        )

    return result


# ════════════════════════════════════════════════
# 2. STATUS & DIGILOCKER MOBILE / OAUTH PORTAL FLOW
# ════════════════════════════════════════════════

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
        "source": kyc_doc.get("verification_source", "DIGILOCKER"),
        "verified_at": kyc_doc.get("verified_at"),
        "is_verified": kyc_doc.get("status") == "VERIFIED",
        "verhoeff_valid": kyc_doc.get("verhoeff_valid", False),
        "admin_override": kyc_doc.get("admin_override", False),
        "verified_name": kyc_doc.get("digilocker_verified_name"),
        "verified_dob": kyc_doc.get("digilocker_verified_dob"),
        "verified_address": kyc_doc.get("digilocker_verified_address"),
    }


@router.post(
    "/digilocker/mobile/send-otp",
    summary="Send OTP to Mobile Number via DigiLocker (Mobile App & Web Flow)",
)
async def digilocker_mobile_send_otp(
    req: DigiLockerMobileSendOTPReq,
    current_user: dict = Depends(get_kyc_user),
):
    """
    DigiLocker Mobile Number Verification - Step 1:
    - User provides 10-digit mobile number.
    - DigiLocker verifies format, blocks duplicates, and dispatches OTP.
    """
    email = current_user["email"]
    result = await DigiLockerService.send_mobile_otp(email, req.mobile_number)
    if not result.get("success"):
        if result.get("is_duplicate"):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=result.get("message"))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result.get("message"))
    return result


@router.post(
    "/digilocker/mobile/verify-otp",
    summary="Verify Mobile OTP & PIN, fetch e-Aadhaar and verify KYC",
)
async def digilocker_mobile_verify_otp(
    req: DigiLockerMobileVerifyOTPReq,
    current_user: dict = Depends(get_kyc_user),
):
    """
    DigiLocker Mobile Number Verification - Step 2:
    - Verifies 6-digit OTP and 6-digit Security PIN.
    - Pulls verified citizen identity directly from DigiLocker.
    - Completes Govt ID KYC verification without requiring any document upload!
    """
    email = current_user["email"]
    result = await DigiLockerService.verify_mobile_otp(
        reference_id=req.reference_id,
        otp=req.otp,
        security_pin=req.security_pin,
        user_email=email,
    )
    if not result.get("success"):
        if result.get("kyc_status") == "DUPLICATE":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=result.get("message"))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result.get("message"))
    return result


@router.get("/digilocker/authorize", summary="DigiLocker Portal Authorization Redirect")
async def digilocker_authorize(
    request: Request,
    email: Optional[str] = Query(None, description="User email (optional if Authorization header sent)"),
):
    """
    Redirects browser directly to the official-styled DigiLocker verification portal.
    If called via API client expecting JSON, returns the authorization URL.
    """
    user_email = email or "user@eventora.com"
    auth_data = DigiLockerService.get_authorization_url(user_email)
    auth_url = auth_data["auth_url"]

    accept_header = request.headers.get("accept", "")
    if "text/html" in accept_header:
        return RedirectResponse(url=auth_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)

    return auth_data


@router.get("/digilocker/portal", response_class=HTMLResponse, summary="DigiLocker Official Portal UI")
@router.get("/digilocker/sandbox-view", response_class=HTMLResponse, summary="DigiLocker Sandbox Portal UI")
async def get_digilocker_portal_ui(
    state: str = Query("state_default", description="OAuth CSRF state"),
    email: str = Query("user@eventora.com", description="User email"),
):
    """
    Renders the official DigiLocker web portal view.
    Users sign in with their Mobile Number, enter OTP & Security PIN, and grant consent.
    """
    html_content = DigiLockerService.render_digilocker_portal_html(state=state, email=email)
    return HTMLResponse(content=html_content)


@router.post("/digilocker/portal/send-otp", summary="Send OTP from DigiLocker Portal UI")
async def portal_send_otp(req: DigiLockerPortalSendOTPReq):
    """Called by the DigiLocker Portal frontend to dispatch mobile OTP."""
    email = req.user_email or "citizen@eventora.com"
    result = await DigiLockerService.send_mobile_otp(email, req.mobile_number)
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT if result.get("is_duplicate") else status.HTTP_400_BAD_REQUEST,
            detail=result.get("message")
        )
    return result


@router.post("/digilocker/portal/verify-otp", summary="Verify OTP & PIN from DigiLocker Portal UI")
async def portal_verify_otp(req: DigiLockerPortalVerifyReq):
    """Called by the DigiLocker Portal frontend to verify OTP & PIN and grant consent."""
    result = await DigiLockerService.portal_authorize_and_issue_code(
        reference_id=req.reference_id,
        otp=req.otp,
        security_pin=req.security_pin,
        state=req.state,
        user_email=req.user_email,
    )
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT if result.get("kyc_status") == "DUPLICATE" else status.HTTP_400_BAD_REQUEST,
            detail=result.get("message")
        )
    return result


@router.post("/digilocker/initiate", summary="Initiate DigiLocker OAuth portal session")
async def initiate_digilocker(current_user: dict = Depends(get_kyc_user)):
    """
    Initiates DigiLocker hosted portal flow.
    Returns authorization URL for the user to authenticate on DigiLocker.
    """
    email = current_user["email"]
    return DigiLockerService.get_authorization_url(email)


@router.get("/digilocker/callback", summary="DigiLocker OAuth callback")
async def digilocker_callback(
    request: Request,
    code: Optional[str] = Query(None, description="OAuth authorization code"),
    state: Optional[str] = Query(None, description="CSRF state parameter"),
    error: Optional[str] = Query(None, description="Error code if denied"),
):
    """
    Callback endpoint for DigiLocker OAuth redirection.
    Exchanges authorization code for access token and pulls verified e-Aadhaar.
    Returns rich success HTML in browser and JSON for API clients.
    """
    if error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"DigiLocker authorization denied: {error}")

    if not code:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing authorization code parameter.")

    try:
        result = await DigiLockerService.process_oauth_callback(code, state or "")
        if result.get("kyc_status") == "DUPLICATE":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=result.get("message"),
            )

        accept_header = request.headers.get("accept", "")
        if "text/html" in accept_header:
            verified_name = result.get("verified_details", {}).get("name", "Citizen")
            aadhaar_masked = result.get("aadhaar_masked", "XXXX-XXXX-XXXX")
            email = result.get("verified_details", {}).get("user_email", "user@eventora.com")
            html_success = DigiLockerService.render_callback_success_html(email, aadhaar_masked, verified_name)
            return HTMLResponse(content=html_success)

        return {
            "message": "DigiLocker verification completed successfully.",
            "kyc_status": result.get("kyc_status"),
            "aadhaar_masked": result.get("aadhaar_masked"),
            "verified_details": result.get("verified_details"),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"DigiLocker callback processing error: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


@router.post(
    "/digilocker/sandbox-verify",
    summary="Simulate direct DigiLocker KYC in Sandbox (Dev/Test)",
)
async def sandbox_verify_aadhaar(
    req: SandboxAadhaarVerifyReq,
    current_user: dict = Depends(get_kyc_user),
):
    """
    Direct DigiLocker verification in sandbox mode with 12-digit Aadhaar number.
    Validates UIDAI Verhoeff checksum and blocks duplicates.
    """
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


@router.post("/verify-document", summary="Legacy document upload (Optional fallback)")
async def verify_aadhaar_document(
    govt_id_proof: UploadFile = File(...),
    current_user: dict = Depends(get_kyc_user),
):
    """
    Optional file-based fallback if user chooses to upload file instead of direct DigiLocker.
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
