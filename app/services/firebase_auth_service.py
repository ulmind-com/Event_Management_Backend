import os
import json
import logging
from typing import Dict, Any
from fastapi import HTTPException, status
import httpx
from app.core.config import settings

logger = logging.getLogger(__name__)

_firebase_initialized = False

def _init_firebase_admin() -> bool:
    global _firebase_initialized
    if _firebase_initialized:
        return True
    try:
        import firebase_admin
        from firebase_admin import credentials

        if len(firebase_admin._apps) > 0:
            _firebase_initialized = True
            return True

        # 1. Raw JSON string from env var (Ideal for Render)
        sa_json_str = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
        if sa_json_str:
            sa_dict = json.loads(sa_json_str)
            cred = credentials.Certificate(sa_dict)
            firebase_admin.initialize_app(cred)
            _firebase_initialized = True
            logger.info("Firebase Admin initialized via FIREBASE_SERVICE_ACCOUNT_JSON.")
            return True

        # 2. File path from env var or common locations
        candidate_paths = [
            os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH"),
            "firebase_service_account.json",
            "../firebase_service_account.json",
            os.path.join(os.path.dirname(__file__), "../../../firebase_service_account.json"),
            os.path.join(os.path.dirname(__file__), "../../firebase_service_account.json")
        ]
        for p in candidate_paths:
            if p and os.path.isfile(p):
                cred = credentials.Certificate(p)
                firebase_admin.initialize_app(cred)
                _firebase_initialized = True
                logger.info(f"Firebase Admin initialized via service account file: {p}")
                return True

    except Exception as e:
        logger.warning(f"Could not initialize Firebase Admin SDK with credentials: {e}")

    return False


class FirebaseAuthService:
    @staticmethod
    async def verify_firebase_id_token(id_token: str) -> Dict[str, Any]:
        """
        Verifies a Firebase ID token using:
        1. Official Firebase Admin SDK with Service Account credentials
        2. Google public tokeninfo endpoint fallback.
        """
        if not id_token or not isinstance(id_token, str):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Google ID token is required."
            )

        token_clean = id_token.strip()

        # Attempt 1: Official Firebase Admin SDK
        if _init_firebase_admin():
            try:
                from firebase_admin import auth as fb_auth
                decoded = fb_auth.verify_id_token(token_clean)
                email = decoded.get("email")
                if not email:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Google account did not provide an email address."
                    )
                return {
                    "email": email.lower().strip(),
                    "name": decoded.get("name") or email.split("@")[0],
                    "picture": decoded.get("picture"),
                    "sub": decoded.get("sub") or decoded.get("uid"),
                    "firebase_uid": decoded.get("uid") or decoded.get("sub")
                }
            except HTTPException:
                raise
            except Exception as e:
                logger.warning(f"Firebase Admin SDK token verification rejected: {e}. Falling back to tokeninfo endpoint.")

        # Attempt 2: Fallback to Google TokenInfo Endpoint
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
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired Google authentication token."
            )

        data = res.json()
        aud = data.get("aud")
        if aud != expected_project_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token was not issued for this application."
            )

        email = data.get("email")
        if not email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Google account did not provide an email address."
            )

        return {
            "email": email.lower().strip(),
            "name": data.get("name") or email.split("@")[0],
            "picture": data.get("picture"),
            "sub": data.get("sub"),
            "firebase_uid": data.get("user_id") or data.get("sub")
        }
