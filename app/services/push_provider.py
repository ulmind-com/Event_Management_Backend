import logging
import os
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
from app.core.config import settings

logger = logging.getLogger("eventora.push_provider")

class PushDeliveryResult:
    def __init__(self, success: bool, message_id: Optional[str] = None, error: Optional[str] = None, token_invalid: bool = False):
        self.success = success
        self.message_id = message_id
        self.error = error
        self.token_invalid = token_invalid

class BasePushProvider(ABC):
    @abstractmethod
    async def send_push(self, token: str, title: str, body: str, data: Optional[Dict[str, str]] = None) -> PushDeliveryResult:
        pass

class FCMProvider(BasePushProvider):
    """
    Firebase Cloud Messaging Provider.
    Reuses firebase_admin if already initialized or loads from service account.
    Safe fallback when credentials or SDK not configured.
    """
    def __init__(self):
        self._initialized = False
        self._messaging = None
        self._init_fcm()

    def _init_fcm(self):
        try:
            import firebase_admin
            from firebase_admin import credentials, messaging
            
            # Check if app already initialized
            if not firebase_admin._apps:
                cred_path = settings.FCM_CREDENTIALS_PATH
                if os.path.exists(cred_path):
                    cred = credentials.Certificate(cred_path)
                    firebase_admin.initialize_app(cred)
                    self._initialized = True
                    self._messaging = messaging
                    logger.info("FCM push provider initialized with service account.")
                elif os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON"):
                    import json
                    cred_dict = json.loads(os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON"))
                    cred = credentials.Certificate(cred_dict)
                    firebase_admin.initialize_app(cred)
                    self._initialized = True
                    self._messaging = messaging
                    logger.info("FCM push provider initialized with JSON env.")
                else:
                    logger.warning("FCM credentials not found. FCM push provider operating in dry-run mode.")
            else:
                self._initialized = True
                self._messaging = messaging
                logger.info("FCM push provider reusing existing firebase_admin app.")
        except Exception as e:
            logger.warning(f"FCM push provider initialization skipped/failed: {e}")
            self._initialized = False

    async def send_push(self, token: str, title: str, body: str, data: Optional[Dict[str, str]] = None) -> PushDeliveryResult:
        if not self._initialized or not self._messaging:
            logger.info(f"[FCM Dry-Run] Would send push to token {token[:10]}...: '{title}' - '{body}'")
            return PushDeliveryResult(success=True, message_id=f"dry_run_{int(datetime.now(timezone.utc).timestamp())}")

        try:
            # Ensure all values in data dictionary are strings
            clean_data = {}
            if data:
                for k, v in data.items():
                    clean_data[str(k)] = str(v)

            message = self._messaging.Message(
                notification=self._messaging.Notification(title=title, body=body),
                data=clean_data,
                token=token,
            )
            # Synchronous call wrapped in safe execution
            response = self._messaging.send(message)
            return PushDeliveryResult(success=True, message_id=response)
        except Exception as e:
            err_str = str(e)
            token_invalid = any(keyword in err_str.lower() for keyword in ["not registered", "invalid registration", "unregistered", "bad device token"])
            logger.error(f"FCM push delivery failed: {err_str} (token_invalid={token_invalid})")
            return PushDeliveryResult(success=False, error=err_str, token_invalid=token_invalid)

class APNsProvider(BasePushProvider):
    """
    Apple Push Notification service provider adapter.
    Prepared for future APNs credentials without disrupting current server.
    """
    async def send_push(self, token: str, title: str, body: str, data: Optional[Dict[str, str]] = None) -> PushDeliveryResult:
        logger.info(f"[APNs Mock] Sent push notification to {token[:10]}...: {title}")
        return PushDeliveryResult(success=True, message_id=f"apns_mock_{int(datetime.now(timezone.utc).timestamp())}")

class WebPushProvider(BasePushProvider):
    """
    W3C Web Push provider adapter using VAPID keys.
    """
    async def send_push(self, token: str, title: str, body: str, data: Optional[Dict[str, str]] = None) -> PushDeliveryResult:
        if not settings.WEB_PUSH_ENABLED or not settings.WEB_PUSH_VAPID_PRIVATE_KEY:
            logger.info(f"[WebPush Disabled] Notification queued for {token[:10]}...")
            return PushDeliveryResult(success=True, message_id=f"webpush_mock_{int(datetime.now(timezone.utc).timestamp())}")

        try:
            from pywebpush import webpush
            import json
            sub_info = json.loads(token) if token.startswith("{") else {"endpoint": token}
            payload = json.dumps({"title": title, "body": body, "data": data or {}})
            webpush(
                subscription_info=sub_info,
                data=payload,
                vapid_private_key=settings.WEB_PUSH_VAPID_PRIVATE_KEY,
                vapid_claims={"sub": settings.WEB_PUSH_SUBJECT}
            )
            return PushDeliveryResult(success=True, message_id=f"webpush_{int(datetime.now(timezone.utc).timestamp())}")
        except Exception as e:
            logger.error(f"WebPush delivery failed: {e}")
            return PushDeliveryResult(success=False, error=str(e), token_invalid="410" in str(e) or "404" in str(e))

class PushProviderFactory:
    _provider_instance: Optional[BasePushProvider] = None

    @classmethod
    def get_provider(cls) -> BasePushProvider:
        if cls._provider_instance is None:
            provider_type = settings.PUSH_PROVIDER.upper()
            if provider_type == "FCM":
                cls._provider_instance = FCMProvider()
            elif provider_type == "APNS":
                cls._provider_instance = APNsProvider()
            elif provider_type == "WEBPUSH":
                cls._provider_instance = WebPushProvider()
            else:
                cls._provider_instance = FCMProvider()
        return cls._provider_instance
