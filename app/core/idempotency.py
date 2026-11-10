import json
import logging
import asyncio
import hashlib
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, Union
from pydantic import BaseModel
from pymongo.errors import DuplicateKeyError
from fastapi import HTTPException, status, Response
from fastapi.responses import JSONResponse

from app.core.database import idempotency_collection

logger = logging.getLogger("eventora.idempotency")


def canonical_json_hash(payload: Any) -> Optional[str]:
    """Computes deterministic SHA256 hex digest for any JSON-serializable request payload."""
    if payload is None:
        return None
    try:
        if hasattr(payload, "model_dump"):
            data = payload.model_dump()
        elif isinstance(payload, dict):
            data = payload
        elif isinstance(payload, (list, tuple)):
            data = list(payload)
        else:
            data = str(payload)
        canonical_str = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(canonical_str.encode("utf-8")).hexdigest()
    except Exception as e:
        logger.warning(f"Failed to generate canonical payload hash: {e}")
        return None


class IdempotencyContext:
    def __init__(
        self,
        key: Optional[str],
        is_replay: bool = False,
        status_code: int = 200,
        response_body: Any = None
    ):
        self.key = key
        self.is_replay = is_replay
        self.status_code = status_code
        self.response_body = response_body
        self._completed_response: Any = None
        self._completed_status_code: int = 200

    def set_result(self, response_body: Any, status_code: int = 200):
        """Sets the response to be cached upon successful context manager exit."""
        self._completed_response = response_body
        self._completed_status_code = status_code

    def replay_response(self) -> JSONResponse:
        """Returns the cached response with standard idempotency response headers."""
        headers = {
            "X-Idempotency-Key": str(self.key),
            "X-Idempotency-Status": "REPLAYED",
            "Cache-Control": "no-store"
        }
        return JSONResponse(
            status_code=self.status_code,
            content=self.response_body,
            headers=headers
        )


class IdempotencyManager:
    """
    Production-grade Idempotency and Duplicate Request Prevention system.
    Supports atomic distributed locks, concurrent request waiting, payload integrity validation,
    and stale-lock auto-recovery on server crashes.
    """

    LOCK_TIMEOUT_SECONDS = 45  # Crash recovery threshold
    CONCURRENT_WAIT_SECONDS = 3.5  # Time to poll for concurrent request completion
    POLL_INTERVAL_SECONDS = 0.15

    @classmethod
    async def acquire_or_replay(
        cls,
        key: Optional[str],
        payload: Any = None,
        user_email: Optional[str] = None,
        endpoint: Optional[str] = None
    ) -> IdempotencyContext:
        """
        Attempts to acquire the idempotency execution lock or returns the previously cached response.
        If a concurrent request is currently in-flight with the same key, it polls briefly for completion.
        """
        if not key or not str(key).strip():
            # If no key provided, bypass idempotency (normal execution)
            return IdempotencyContext(key=None, is_replay=False)

        clean_key = str(key).strip()
        payload_hash = canonical_json_hash(payload)
        now_utc = datetime.now(timezone.utc)

        doc = {
            "idempotency_key": clean_key,
            "user_email": user_email,
            "endpoint": endpoint,
            "payload_hash": payload_hash,
            "status": "PROCESSING",
            "created_at": now_utc,
            "locked_at": now_utc
        }

        try:
            # 1. Atomic insertion attempt
            await idempotency_collection.insert_one(doc)
            logger.info(f"Acquired idempotency lock for key: {clean_key} on {endpoint}")
            return IdempotencyContext(key=clean_key, is_replay=False)

        except DuplicateKeyError:
            # 2. Key exists - fetch and evaluate
            existing = await idempotency_collection.find_one({"idempotency_key": clean_key})
            if not existing:
                # Rare edge case: deleted concurrently
                return IdempotencyContext(key=clean_key, is_replay=False)

            # Check for payload tampering / mismatched request payload
            existing_hash = existing.get("payload_hash")
            if existing_hash and payload_hash and existing_hash != payload_hash:
                logger.error(f"Idempotency key {clean_key} reused with mismatched payload hash.")
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Idempotency-Key has already been used with a different request payload."
                )

            # Check if previously completed
            if existing.get("status") == "COMPLETED":
                logger.info(f"Returning cached idempotent response for key: {clean_key}")
                return IdempotencyContext(
                    key=clean_key,
                    is_replay=True,
                    status_code=int(existing.get("response_code", 200)),
                    response_body=existing.get("response_body")
                )

            # Check if in-flight (concurrent request, e.g. double-click)
            if existing.get("status") == "PROCESSING":
                locked_at = existing.get("locked_at") or existing.get("created_at")
                if locked_at and (now_utc - locked_at.replace(tzinfo=timezone.utc)).total_seconds() > cls.LOCK_TIMEOUT_SECONDS:
                    # Previous server crashed while processing! Take over the lock safely.
                    logger.warning(f"Re-acquiring stale idempotency lock for crashed key: {clean_key}")
                    await idempotency_collection.update_one(
                        {"idempotency_key": clean_key, "status": "PROCESSING"},
                        {"$set": {"locked_at": now_utc, "user_email": user_email, "endpoint": endpoint}}
                    )
                    return IdempotencyContext(key=clean_key, is_replay=False)

                # Wait for concurrent request to finish
                start_time = asyncio.get_event_loop().time()
                while (asyncio.get_event_loop().time() - start_time) < cls.CONCURRENT_WAIT_SECONDS:
                    await asyncio.sleep(cls.POLL_INTERVAL_SECONDS)
                    updated = await idempotency_collection.find_one({"idempotency_key": clean_key})
                    if updated and updated.get("status") == "COMPLETED":
                        logger.info(f"Concurrent request completed for key: {clean_key}. Replaying response.")
                        return IdempotencyContext(
                            key=clean_key,
                            is_replay=True,
                            status_code=int(updated.get("response_code", 200)),
                            response_body=updated.get("response_body")
                        )

                # Still processing after wait period
                logger.warning(f"Concurrent in-flight duplicate rejected for key: {clean_key}")
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="A request with this Idempotency-Key is currently being processed. Please wait for completion."
                )

            # If previous failed, allow retry
            if existing.get("status") == "FAILED":
                await idempotency_collection.update_one(
                    {"idempotency_key": clean_key},
                    {"$set": {"status": "PROCESSING", "locked_at": now_utc}}
                )
                return IdempotencyContext(key=clean_key, is_replay=False)

            return IdempotencyContext(key=clean_key, is_replay=False)

    @classmethod
    async def complete(cls, key: Optional[str], response_body: Any, status_code: int = 200):
        """Marks idempotency record as COMPLETED and persists response data."""
        if not key:
            return
        now_utc = datetime.now(timezone.utc)
        clean_body = response_body
        if hasattr(response_body, "model_dump"):
            clean_body = response_body.model_dump()
        try:
            await idempotency_collection.update_one(
                {"idempotency_key": str(key).strip()},
                {"$set": {
                    "status": "COMPLETED",
                    "response_code": status_code,
                    "response_body": clean_body,
                    "completed_at": now_utc
                }}
            )
        except Exception as e:
            logger.warning(f"Failed to record completed idempotency response for {key}: {e}")

    @classmethod
    async def fail(cls, key: Optional[str], error_message: str):
        """Marks record as FAILED or clears lock so client can retry with same key."""
        if not key:
            return
        try:
            # We delete the lock record on client/validation failure so the user can re-submit
            await idempotency_collection.delete_one({"idempotency_key": str(key).strip()})
        except Exception as e:
            logger.warning(f"Failed to clear failed idempotency record for {key}: {e}")

