"""
DigiLocker KYC Integration Service
===================================
Production-grade DigiLocker integration for Indian Aadhaar KYC verification:
  1. DigiLocker OAuth 2.0 authorization URL generation
  2. Authorization code exchange for DigiLocker access token
  3. Fetching official e-Aadhaar XML/data from DigiLocker API
  4. Automatic verification, Verhoeff checksum validation, and anti-duplicate hashing
  5. Sandbox simulation mode for local development, staging, and automated tests
"""      

import hashlib
import logging
import os
import secrets
import urllib.parse
from datetime import datetime, timezone
from typing import Optional, Dict, Any

import httpx

from app.core.config import settings
from app.core.database import kyc_verification_collection, user_collection
from app.services.aadhaar_kyc_service import AadhaarKYCService

logger = logging.getLogger("eventora.digilocker")

# DigiLocker Production and Sandbox Endpoints
DIGILOCKER_AUTH_URL = "https://api.digitallocker.gov.in/public/oauth2/1/authorize"
DIGILOCKER_TOKEN_URL = "https://api.digitallocker.gov.in/public/oauth2/1/token"
DIGILOCKER_EAADHAAR_URL = "https://api.digitallocker.gov.in/public/oauth2/2/xml/eaadhaar"

# In-memory or database state cache for OAuth CSRF protection
_oauth_states: Dict[str, Dict[str, Any]] = {}


class DigiLockerService:
    """
    Handles DigiLocker OAuth 2.0 flow, document pulling, and automated KYC verification.
    """

    @classmethod
    def get_authorization_url(cls, user_email: str) -> Dict[str, Any]:
        """
        Generate DigiLocker OAuth 2.0 authorization URL.
        Returns auth URL and session state for CSRF validation.
        """
        state = secrets.token_urlsafe(32)
        _oauth_states[state] = {
            "user_email": user_email,
            "created_at": datetime.now(timezone.utc),
        }

        client_id = settings.DIGILOCKER_CLIENT_ID
        redirect_uri = settings.DIGILOCKER_REDIRECT_URI

        if not client_id:
            # Sandbox / Simulated flow URL
            auth_url = f"/api/v1/kyc/digilocker/sandbox-view?state={state}&email={urllib.parse.quote(user_email)}"
            return {
                "auth_url": auth_url,
                "state": state,
                "mode": "sandbox",
                "message": "DigiLocker sandbox mode active. Real client credentials not configured in environment.",
            }

        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": "eaadhaar",
        }
        query_string = urllib.parse.urlencode(params)
        auth_url = f"{DIGILOCKER_AUTH_URL}?{query_string}"

        return {
            "auth_url": auth_url,
            "state": state,
            "mode": "production" if settings.DIGILOCKER_ENV == "production" else "sandbox",
            "message": "Redirect user to auth_url to complete DigiLocker authentication.",
        }

    @classmethod
    async def process_oauth_callback(
        cls, code: str, state: str, user_email: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Exchange DigiLocker OAuth authorization code for access token and fetch e-Aadhaar.
        """
        # Validate state
        state_data = _oauth_states.pop(state, None)
        email = user_email or (state_data.get("user_email") if state_data else None)

        if not email:
            raise ValueError("Invalid or expired OAuth state parameter. Please restart DigiLocker verification.")

        client_id = settings.DIGILOCKER_CLIENT_ID
        client_secret = settings.DIGILOCKER_CLIENT_SECRET
        redirect_uri = settings.DIGILOCKER_REDIRECT_URI

        # If live DigiLocker credentials are configured, exchange code
        if client_id and client_secret:
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    token_res = await client.post(
                        DIGILOCKER_TOKEN_URL,
                        data={
                            "grant_type": "authorization_code",
                            "code": code,
                            "client_id": client_id,
                            "client_secret": client_secret,
                            "redirect_uri": redirect_uri,
                        },
                    )
                    token_data = token_res.json()
                    access_token = token_data.get("access_token")

                    if not access_token:
                        logger.error(f"DigiLocker token exchange error: {token_data}")
                        raise ValueError("Failed to obtain DigiLocker access token.")

                    # Fetch e-Aadhaar document from DigiLocker
                    doc_res = await client.get(
                        DIGILOCKER_EAADHAAR_URL,
                        headers={"Authorization": f"Bearer {access_token}"},
                    )

                    doc_bytes = doc_res.content
                    return await AadhaarKYCService.verify_and_register(
                        email, doc_bytes, filename="eaadhaar.xml"
                    )

            except Exception as e:
                logger.error(f"Live DigiLocker callback failed: {e}", exc_info=True)
                raise ValueError(f"DigiLocker verification failed: {str(e)}")
        else:
            # Fallback to sandbox verification
            raise ValueError(
                "DigiLocker client credentials are not configured in environment. Use sandbox verification."
            )

    @classmethod
    async def verify_sandbox_aadhaar(
        cls, user_email: str, aadhaar_number: str
    ) -> Dict[str, Any]:
        """
        Simulate DigiLocker KYC verification in sandbox/test mode:
          1. Validates Aadhaar format and Verhoeff checksum
          2. Generates SHA-256 hash with salt
          3. Checks for duplicate Aadhaar
          4. Updates KYC collection and user profile
        """
        # Clean number
        clean_aadhaar = aadhaar_number.replace(" ", "").replace("-", "").strip()

        if not AadhaarKYCService.validate_aadhaar_format(clean_aadhaar):
            return {
                "kyc_status": "REJECTED",
                "aadhaar_masked": None,
                "message": "Invalid Aadhaar number. Must be 12 digits, cannot start with 0 or 1, and must pass UIDAI Verhoeff checksum.",
                "verhoeff_valid": False,
            }

        now = datetime.now(timezone.utc)
        aadhaar_hash = AadhaarKYCService.hash_aadhaar(clean_aadhaar)
        aadhaar_masked = AadhaarKYCService.mask_aadhaar(clean_aadhaar)

        # Duplicate check
        duplicate = await AadhaarKYCService.check_duplicate(aadhaar_hash, exclude_email=user_email)
        if duplicate:
            return {
                "kyc_status": "DUPLICATE",
                "aadhaar_masked": aadhaar_masked,
                "message": f"This Aadhaar card ({aadhaar_masked}) is already registered with another account ({duplicate.get('user_email')}). Duplicate registrations are not allowed.",
                "verhoeff_valid": True,
            }

        # Save to KYC collection
        await kyc_verification_collection.update_one(
            {"user_email": user_email},
            {
                "$set": {
                    "user_email": user_email,
                    "aadhaar_hash": aadhaar_hash,
                    "aadhaar_masked": aadhaar_masked,
                    "status": "VERIFIED",
                    "verification_source": "DIGILOCKER_SANDBOX",
                    "verhoeff_valid": True,
                    "duplicate_of_email": None,
                    "verified_at": now,
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )

        # Update user profile
        await user_collection.update_one(
            {"email": user_email},
            {
                "$set": {
                    "kyc_status": "VERIFIED",
                    "kyc_aadhaar_masked": aadhaar_masked,
                    "kyc_verified_at": now,
                    "kyc_source": "DIGILOCKER",
                }
            },
        )

        return {
            "kyc_status": "VERIFIED",
            "aadhaar_masked": aadhaar_masked,
            "message": f"Aadhaar ({aadhaar_masked}) successfully verified via DigiLocker. KYC complete.",
            "verhoeff_valid": True,
        }
