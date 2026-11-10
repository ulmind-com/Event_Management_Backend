import os
import json
import time
import logging
import asyncio
import threading
from typing import Dict, Any, Optional
from fastapi import HTTPException, status
import httpx
import jwt
from cryptography.x509 import load_pem_x509_certificate
from app.core.config import settings

logger = logging.getLogger(__name__)

_init_lock = threading.Lock()
_firebase_initialized = False

# In-memory cache for Google public x509 certificates for Firebase Auth
_public_keys_cache: Dict[str, str] = {}
_parsed_keys_cache: Dict[str, Any] = {}
_public_keys_expiry: float = 0.0
_keys_lock = asyncio.Lock()

_shared_client: Optional[httpx.AsyncClient] = None

def get_shared_client() -> httpx.AsyncClient:
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.AsyncClient(
            timeout=10.0,
            limits=httpx.Limits(max_keepalive_connections=50, max_connections=200)
        )
    return _shared_client

def init_firebase_admin() -> bool:
    """
    Thread-safe initialization of Firebase Admin SDK.
    Safe for concurrent calls.
    """
    global _firebase_initialized
    if _firebase_initialized:
        return True

    with _init_lock:
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
                cleaned = sa_json_str.strip()
                if (cleaned.startswith("'") and cleaned.endswith("'")) or \
                   (cleaned.startswith('"') and cleaned.endswith('"')):
                    cleaned = cleaned[1:-1].strip()
                sa_dict = json.loads(cleaned)
                cred = credentials.Certificate(sa_dict)
                firebase_admin.initialize_app(cred)
                _firebase_initialized = True
                logger.info("Firebase Admin initialized via FIREBASE_SERVICE_ACCOUNT_JSON.")
                return True

            # 2. File path from env var or candidate locations
            candidate_paths = [
                os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH"),
                "firebase_service_account.json",
                "../firebase_service_account.json",
                os.path.join(os.path.dirname(__file__), "../../../firebase_service_account.json"),
                os.path.join(os.path.dirname(__file__), "../../firebase_service_account.json"),
            ]
            for p in candidate_paths:
                if p and os.path.isfile(p):
                    cred = credentials.Certificate(p)
                    firebase_admin.initialize_app(cred)
                    _firebase_initialized = True
                    logger.info(f"Firebase Admin initialized via service account file: {p}")
                    return True

        except ValueError as ve:
            if "already exists" in str(ve).lower():
                _firebase_initialized = True
                return True
            logger.warning(f"Firebase Admin initialize error: {ve}")
        except Exception as e:
            logger.warning(f"Could not initialize Firebase Admin SDK: {e}")

        return False


async def _get_google_public_certs() -> Dict[str, str]:
    """
    Fetch and cache Google's public x509 certs for Firebase Auth tokens.
    Cached for up to 6 hours or per max-age header.
    """
    global _public_keys_cache, _public_keys_expiry

    now = time.time()
    if _public_keys_cache and now < _public_keys_expiry:
        return _public_keys_cache

    async with _keys_lock:
        if _public_keys_cache and now < _public_keys_expiry:
            return _public_keys_cache

        url = "https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com"
        try:
            client = get_shared_client()
            res = await client.get(url)
            if res.status_code == 200:
                data = res.json()
                _public_keys_cache = data
                # Parse max-age from Cache-Control if available, default to 6 hours
                cache_control = res.headers.get("cache-control", "")
                max_age = 21600
                for part in cache_control.split(","):
                    if "max-age=" in part:
                        try:
                            max_age = int(part.split("max-age=")[1].strip())
                        except Exception:
                            pass
                _public_keys_expiry = now + max_age
                # Pre-parse public keys for instant O(1) in-memory signature checks
                for kid_val, cert_pem in data.items():
                    try:
                        cert_obj = load_pem_x509_certificate(cert_pem.encode("utf-8"))
                        _parsed_keys_cache[kid_val] = cert_obj.public_key()
                    except Exception:
                        pass
                logger.info(f"Cached & parsed {len(_parsed_keys_cache)} Google Firebase public keys (ttl: {max_age}s).")
                return _public_keys_cache
        except Exception as e:
            logger.error(f"Failed to fetch Google public certs: {e}")
            if _public_keys_cache:
                return _public_keys_cache

    return _public_keys_cache


class FirebaseAuthService:
    @staticmethod
    async def verify_firebase_id_token(id_token: str) -> Dict[str, Any]:
        """
        Verifies a Firebase ID token concurrently with:
        1. Official Firebase Admin SDK with credentials (non-blocking threadpool, 10s clock skew tolerance)
        2. Cryptographic RS256 verification using Google's public certs (100% in-memory standalone)
        3. Google tokeninfo fallback for raw OAuth tokens (reusing connection pool)
        """
        if not id_token or not isinstance(id_token, str):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Google ID token is required."
            )

        token_clean = id_token.strip()
        expected_project_id = getattr(settings, "FIREBASE_PROJECT_ID", "event-management-4fe6f")

        # ── Method 1: Official Firebase Admin SDK (Non-blocking) ──
        if init_firebase_admin():
            try:
                from firebase_admin import auth as fb_auth
                # Run synchronous verify_id_token in threadpool with clock skew tolerance to prevent event loop blocking
                decoded = await asyncio.to_thread(fb_auth.verify_id_token, token_clean, clock_skew_seconds=10)
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
                logger.warning(f"Firebase Admin SDK verification rejected: {e}. Trying direct cert verification.")

        # ── Method 2: Direct RS256 Signature Verification via Google's Public Certs (100% In-Memory) ──
        try:
            certs = await _get_google_public_certs()
            if certs:
                unverified_header = jwt.get_unverified_header(token_clean)
                kid = unverified_header.get("kid")
                if kid and kid in certs:
                    if kid not in _parsed_keys_cache:
                        cert_pem = certs[kid]
                        cert_obj = load_pem_x509_certificate(cert_pem.encode("utf-8"))
                        _parsed_keys_cache[kid] = cert_obj.public_key()
                    public_key = _parsed_keys_cache[kid]

                    # Verify RS256 cryptographic signature locally with 10s leeway for clock skew
                    decoded = jwt.decode(
                        token_clean,
                        public_key,
                        algorithms=["RS256"],
                        options={"verify_aud": False, "verify_iss": False},
                        leeway=10,
                    )

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
                        "sub": decoded.get("sub") or decoded.get("user_id"),
                        "firebase_uid": decoded.get("user_id") or decoded.get("sub")
                    }
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"Direct Google cert verification failed: {e}. Trying tokeninfo fallback.")

        # ── Method 3: Fallback for standard Google OAuth ID Tokens ──
        try:
            client = get_shared_client()
            res = await client.get(
                "https://oauth2.googleapis.com/tokeninfo",
                params={"id_token": token_clean}
            )
            if res.status_code == 200:
                data = res.json()
                aud = data.get("aud")
                if aud == expected_project_id or aud == getattr(settings, "FIREBASE_APP_ID", "") or "google" in str(aud).lower():
                    email = data.get("email")
                    if email:
                        return {
                            "email": email.lower().strip(),
                            "name": data.get("name") or email.split("@")[0],
                            "picture": data.get("picture"),
                            "sub": data.get("sub"),
                            "firebase_uid": data.get("user_id") or data.get("sub")
                        }
        except Exception as e:
            logger.error(f"Google tokeninfo request failed: {e}")

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired Google authentication token."
        )
