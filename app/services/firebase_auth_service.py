import httpx
from fastapi import HTTPException, status
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

class FirebaseAuthService:
    @staticmethod
    async def verify_firebase_id_token(id_token: str) -> dict:
        """
        Verifies a Firebase ID token using Google's public tokeninfo endpoint.
        Validates project ID (audience), issuer, expiration, and email verification.
        """
        if not id_token or not isinstance(id_token, str):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Google ID token is required."
            )

        token_clean = id_token.strip()
        expected_project_id = getattr(settings, "FIREBASE_PROJECT_ID", "event-management-4fe6f")

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.get(
                    "https://oauth2.googleapis.com/tokeninfo",
                    params={"id_token": token_clean}
                )
        except Exception as e:
            logger.error(f"Error connecting to Google token verification service: {e}")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Unable to verify Google credentials at this time. Please try again."
            )

        if res.status_code != 200:
            logger.warning(f"Google token verification failed with status {res.status_code}: {res.text}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired Google authentication token."
            )

        data = res.json()

        # Validate audience (Project ID)
        aud = data.get("aud")
        if aud != expected_project_id:
            logger.warning(f"Token audience mismatch: got {aud}, expected {expected_project_id}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token was not issued for this application."
            )

        # Validate issuer
        iss = data.get("iss", "")
        valid_issuers = [
            f"https://securetoken.google.com/{expected_project_id}",
            "accounts.google.com",
            "https://accounts.google.com"
        ]
        if iss not in valid_issuers:
            logger.warning(f"Token issuer invalid: {iss}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token issuer."
            )

        email = data.get("email")
        if not email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Google account did not provide an email address."
            )

        email_verified = data.get("email_verified")
        if email_verified not in [True, "true", "True", 1, "1"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Google account email is not verified."
            )

        return {
            "email": email.lower().strip(),
            "name": data.get("name") or email.split("@")[0],
            "picture": data.get("picture"),
            "sub": data.get("sub"),
            "firebase_uid": data.get("user_id") or data.get("sub")
        }
