"""
DigiLocker Official OAuth 2.0 API Router
Implements the 4 mandatory production endpoints:
  1. GET  /api/digilocker/authorize
  2. GET  /api/digilocker/callback
  3. GET  /api/digilocker/status
  4. POST /api/digilocker/revoke
"""

from typing import Optional
from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, EmailStr

from app.core.security import decode_token
from app.services.digilocker_oauth import DigiLockerOAuthService

router = APIRouter()


class RevokeRequest(BaseModel):
    user_email: Optional[EmailStr] = None


def _extract_email_from_token(authorization: Optional[str]) -> Optional[str]:
    """Helper to safely extract citizen email from Bearer token."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization.replace("Bearer ", "").strip()
    payload = decode_token(token)
    if payload:
        return payload.get("sub")
    return None


# ─────────────────────────────────────────────────────────
# 1. GET /api/digilocker/authorize
# ─────────────────────────────────────────────────────────

@router.get(
    "/authorize",
    summary="Initiate DigiLocker OAuth 2.0 Authorization",
    description=(
        "Initializes a secure PKCE (S256) session and redirects the citizen to the "
        "official Government of India DigiLocker authentication portal. "
        "DigiLocker handles all citizen authentication and SMS OTP internally."
    ),
    response_class=RedirectResponse,
    status_code=status.HTTP_302_FOUND,
)
async def digilocker_authorize(
    user_email: Optional[str] = Query(None, description="Citizen email associated with verification session"),
    redirect_uri: Optional[str] = Query(None, description="Custom OAuth redirect callback URI"),
    format: Optional[str] = Query("redirect", description="Response format: 'redirect' (302) or 'json'"),
    authorization: Optional[str] = Header(None),
):
    # Prefer authenticated token email if available
    token_email = _extract_email_from_token(authorization)
    target_email = token_email or user_email

    session = DigiLockerOAuthService.create_authorization_session(
        user_email=target_email,
        redirect_uri=redirect_uri,
    )

    if format == "json":
        return JSONResponse(content={
            "success": True,
            "authorization_url": session["authorization_url"],
            "state": session["state"],
            "redirect_uri": session["redirect_uri"],
            "message": "Redirect user to authorization_url for official DigiLocker authentication.",
        })

    return RedirectResponse(
        url=session["authorization_url"],
        status_code=status.HTTP_302_FOUND,
    )


# ─────────────────────────────────────────────────────────
# 2. GET /api/digilocker/callback
# ─────────────────────────────────────────────────────────

@router.get(
    "/callback",
    summary="DigiLocker OAuth 2.0 Callback",
    description=(
        "Official redirect target called by DigiLocker after user consent. "
        "Validates state, exchanges authorization code for token securely on the backend, "
        "and extracts citizen e-Aadhaar verification details."
    ),
)
async def digilocker_callback(
    request: Request,
    code: Optional[str] = Query(None, description="DigiLocker authorization code"),
    state: Optional[str] = Query(None, description="CSRF protection state token"),
    error: Optional[str] = Query(None, description="OAuth error code (e.g. access_denied)"),
    error_description: Optional[str] = Query(None, description="Error explanation"),
    format: Optional[str] = Query(None, description="Force JSON response ('json')"),
):
    result = await DigiLockerOAuthService.handle_callback(
        code=code,
        state=state,
        error=error,
        error_description=error_description,
    )

    # Return JSON if requested
    accept_header = request.headers.get("accept", "")
    if format == "json" or "application/json" in accept_header:
        status_code = status.HTTP_200_OK if result.get("success") else status.HTTP_400_BAD_REQUEST
        return JSONResponse(content=result, status_code=status_code)

    # Browser presentation: Render clean official response page
    if result.get("success"):
        details = result.get("verified_details", {})
        html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>DigiLocker Verification Success | Eventora</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #0f172a;
            color: #f8fafc;
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            margin: 0;
            padding: 20px;
        }}
        .card {{
            background: #1e293b;
            border: 1px solid #334155;
            border-radius: 16px;
            max-width: 440px;
            width: 100%;
            padding: 36px 28px;
            text-align: center;
            box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.5);
        }}
        .badge {{
            width: 64px;
            height: 64px;
            background: #10b981;
            color: white;
            font-size: 32px;
            font-weight: bold;
            line-height: 64px;
            border-radius: 50%;
            margin: 0 auto 20px;
        }}
        h2 {{ margin: 0 0 10px; font-size: 24px; color: #34d399; }}
        p {{ color: #94a3b8; font-size: 14px; margin: 0 0 24px; line-height: 1.5; }}
        .data-box {{
            background: #0f172a;
            border: 1px solid #334155;
            border-radius: 10px;
            padding: 16px;
            text-align: left;
            margin-bottom: 24px;
            font-size: 13px;
        }}
        .row {{ display: flex; justify-content: space-between; margin-bottom: 8px; }}
        .row:last-child {{ margin-bottom: 0; }}
        .label {{ color: #64748b; }}
        .val {{ font-weight: 600; color: #f1f5f9; }}
        .btn {{
            display: block;
            background: #2563eb;
            color: white;
            padding: 12px;
            border-radius: 8px;
            text-decoration: none;
            font-weight: 600;
            transition: background 0.2s;
            cursor: pointer;
            border: none;
            width: 100%;
            font-size: 15px;
        }}
        .btn:hover {{ background: #1d4ed8; }}
    </style>
</head>
<body>
    <div class="card">
        <div class="badge">✓</div>
        <h2>Identity Verified via DigiLocker</h2>
        <p>Your Aadhaar identity has been verified through official Government of India DigiLocker integration.</p>
        
        <div class="data-box">
            <div class="row">
                <span class="label">Citizen Name:</span>
                <span class="val">{details.get("name", "Verified Citizen")}</span>
            </div>
            <div class="row">
                <span class="label">Aadhaar Number:</span>
                <span class="val" style="font-family: monospace;">{result.get("aadhaar_masked", "XXXX-XXXX-8899")}</span>
            </div>
            <div class="row">
                <span class="label">Source:</span>
                <span class="val" style="color: #60a5fa;">DigiLocker (Govt of India)</span>
            </div>
            <div class="row">
                <span class="label">Status:</span>
                <span class="val" style="color: #34d399;">VERIFIED</span>
            </div>
        </div>

        <button class="btn" onclick="closeAndReturn()">Return to Application</button>
    </div>

    <script>
        function closeAndReturn() {{
            if (window.opener) {{
                try {{
                    window.opener.postMessage({{
                        type: "DIGILOCKER_VERIFIED",
                        status: "VERIFIED",
                        verification_id: "{result.get('verification_id')}",
                        aadhaar_masked: "{result.get('aadhaar_masked')}"
                    }}, "*");
                }} catch(e) {{}}
                window.close();
            }} else {{
                window.location.href = "/";
            }}
        }}
    </script>
</body>
</html>"""
        return HTMLResponse(content=html_content, status_code=200)

    # Render error page
    error_msg = result.get("message", "Authentication could not be completed.")
    html_error = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>DigiLocker Verification Failed</title>
    <style>
        body {{ font-family: sans-serif; background: #0f172a; color: white; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; }}
        .card {{ background: #1e293b; border: 1px solid #ef4444; border-radius: 12px; padding: 32px; max-width: 400px; text-align: center; }}
        h2 {{ color: #ef4444; margin-top: 0; }}
        p {{ color: #94a3b8; font-size: 14px; line-height: 1.5; }}
        a {{ display: inline-block; margin-top: 16px; background: #334155; color: white; padding: 10px 20px; border-radius: 6px; text-decoration: none; }}
    </style>
</head>
<body>
    <div class="card">
        <h2>Verification Incomplete</h2>
        <p>{error_msg}</p>
        <a href="/api/digilocker/authorize">Try Again with DigiLocker</a>
    </div>
</body>
</html>"""
    return HTMLResponse(content=html_error, status_code=400)


# ─────────────────────────────────────────────────────────
# 3. GET /api/digilocker/status
# ─────────────────────────────────────────────────────────

@router.get(
    "/status",
    summary="Check DigiLocker Verification Status",
    description="Returns current DigiLocker KYC verification status and masked Aadhaar reference. Never leaks secrets or access tokens.",
)
async def digilocker_status(
    user_email: Optional[str] = Query(None, description="Citizen email address"),
    authorization: Optional[str] = Header(None),
):
    token_email = _extract_email_from_token(authorization)
    target_email = token_email or user_email

    if not target_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User email is required to retrieve DigiLocker verification status.",
        )

    status_data = await DigiLockerOAuthService.get_verification_status(target_email)
    return status_data


# ─────────────────────────────────────────────────────────
# 4. POST /api/digilocker/revoke
# ─────────────────────────────────────────────────────────

@router.post(
    "/revoke",
    summary="Revoke DigiLocker Verification",
    description="Revokes DigiLocker authorization and resets the citizen's KYC status.",
)
async def digilocker_revoke(
    payload: Optional[RevokeRequest] = None,
    authorization: Optional[str] = Header(None),
):
    token_email = _extract_email_from_token(authorization)
    target_email = token_email or (payload.user_email if payload else None)

    if not target_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User email is required to revoke DigiLocker authorization.",
        )

    result = await DigiLockerOAuthService.revoke_authorization(target_email)
    return result
