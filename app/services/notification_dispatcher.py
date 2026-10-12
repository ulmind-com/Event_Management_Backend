import logging
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional
from bson import ObjectId
import resend

from app.core.config import settings
from app.core.database import (
    notification_collection,
    notification_device_collection,
    notification_delivery_collection,
    notification_preference_collection,
)
from app.core.notification_types import (
    NotificationType,
    NotificationChannel,
    DeliveryStatus,
    SoundCategory,
    NOTIFICATION_TYPE_CONFIG,
)
from app.core.notification_websocket import notification_ws_manager
from app.services.push_provider import PushProviderFactory

logger = logging.getLogger("eventora.notification_dispatcher")

class NotificationDispatcher:
    """
    Handles asynchronous channel delivery and delivery state tracking.
    Guarantees isolation: Push/Email failures never affect in-app persistence.
    """

    @classmethod
    async def dispatch(cls, notification: Dict[str, Any], requested_channels: Optional[List[NotificationChannel]] = None) -> Dict[str, Any]:
        """
        Dispatches a persisted notification to all enabled channels.
        """
        if not settings.NOTIFICATIONS_ENABLED:
            logger.info("Notifications globally disabled via config.")
            return {"status": "disabled"}

        notification_id = str(notification.get("_id") or notification.get("id"))
        user_email = notification.get("recipient_user_id")
        n_type = notification.get("notification_type")

        # Fetch user preferences
        prefs = await cls.get_user_preferences(user_email)

        # Determine target channels
        channels_to_deliver = requested_channels or notification.get("channels") or [
            NotificationChannel.IN_APP,
            NotificationChannel.WEBSOCKET,
            NotificationChannel.PUSH,
            NotificationChannel.EMAIL
        ]

        delivery_results = {}

        for ch in channels_to_deliver:
            ch_enum = NotificationChannel(ch) if isinstance(ch, str) else ch

            if ch_enum == NotificationChannel.IN_APP:
                # In-app is always persisted in DB; mark delivery record
                await cls._record_delivery(notification_id, user_email, ch_enum, DeliveryStatus.DELIVERED)
                delivery_results["in_app"] = "delivered"

            elif ch_enum == NotificationChannel.WEBSOCKET:
                if settings.NOTIFICATION_WEBSOCKET_ENABLED:
                    ws_res = await cls._deliver_websocket(notification, user_email)
                    delivery_results["websocket"] = ws_res

            elif ch_enum == NotificationChannel.PUSH:
                if prefs.get("push_enabled", True) and cls._is_type_enabled_in_prefs(prefs, n_type):
                    push_res = await cls._deliver_push(notification, user_email)
                    delivery_results["push"] = push_res
                else:
                    await cls._record_delivery(notification_id, user_email, ch_enum, DeliveryStatus.CANCELLED, error="User disabled push preferences")
                    delivery_results["push"] = "preference_disabled"

            elif ch_enum == NotificationChannel.EMAIL:
                if prefs.get("email_enabled", True) and cls._is_type_enabled_in_prefs(prefs, n_type):
                    email_res = await cls._deliver_email(notification, user_email)
                    delivery_results["email"] = email_res
                else:
                    await cls._record_delivery(notification_id, user_email, ch_enum, DeliveryStatus.CANCELLED, error="User disabled email preferences")
                    delivery_results["email"] = "preference_disabled"

        return delivery_results

    @classmethod
    async def _deliver_websocket(cls, notification: Dict[str, Any], user_email: str) -> str:
        """Sends notification event over WebSocket to user's active sockets."""
        notification_id = str(notification.get("_id") or notification.get("id"))
        try:
            # Prepare safe payload for client
            event_payload = {
                "notification_id": notification_id,
                "type": notification.get("notification_type"),
                "title": notification.get("title"),
                "message": notification.get("message"),
                "resource_type": notification.get("resource_type"),
                "resource_id": notification.get("resource_id"),
                "action_url": notification.get("action_url"),
                "priority": notification.get("priority", "NORMAL"),
                "sound_category": notification.get("sound_category", "DEFAULT"),
                "created_at": notification.get("created_at", datetime.now(timezone.utc)).isoformat() if isinstance(notification.get("created_at"), datetime) else notification.get("created_at"),
                "is_read": False,
            }

            sent_count = await notification_ws_manager.send_to_user(
                user_email=user_email,
                event="notification.created",
                data=event_payload
            )

            # Also broadcast updated unread count
            unread_count = await notification_collection.count_documents({
                "recipient_user_id": user_email,
                "is_read": False
            })
            await notification_ws_manager.send_to_user(
                user_email=user_email,
                event="notification.unread_count",
                data={"unread_count": unread_count}
            )

            status = DeliveryStatus.DELIVERED if sent_count > 0 else DeliveryStatus.ACCEPTED
            await cls._record_delivery(notification_id, user_email, NotificationChannel.WEBSOCKET, status)
            return "delivered" if sent_count > 0 else "queued_no_active_socket"
        except Exception as e:
            logger.error(f"WebSocket delivery error for {user_email}: {e}")
            await cls._record_delivery(notification_id, user_email, NotificationChannel.WEBSOCKET, DeliveryStatus.FAILED, error=str(e))
            return "failed"

    @classmethod
    async def _deliver_push(cls, notification: Dict[str, Any], user_email: str) -> str:
        """Delivers push notification to all registered active user devices."""
        notification_id = str(notification.get("_id") or notification.get("id"))
        try:
            devices = await notification_device_collection.find({
                "user_email": user_email,
                "is_active": True
            }).to_list(100)

            if not devices:
                await cls._record_delivery(
                    notification_id, user_email, NotificationChannel.PUSH,
                    DeliveryStatus.ACCEPTED, error="No active registered devices"
                )
                return "no_devices"

            provider = PushProviderFactory.get_provider()
            title = notification.get("title", "Eventora Notification")
            body = notification.get("message", "")
            data_payload = {
                "notification_id": notification_id,
                "type": str(notification.get("notification_type")),
                "resource_type": str(notification.get("resource_type") or ""),
                "resource_id": str(notification.get("resource_id") or ""),
                "action_url": str(notification.get("action_url") or ""),
                "sound_category": str(notification.get("sound_category") or "DEFAULT"),
            }

            all_succeeded = True
            last_message_id = None
            last_error = None

            for dev in devices:
                token = dev.get("provider_token")
                push_res = await provider.send_push(token=token, title=title, body=body, data=data_payload)

                if push_res.success:
                    last_message_id = push_res.message_id
                    # Update device last used
                    await notification_device_collection.update_one(
                        {"_id": dev["_id"]},
                        {"$set": {"last_used_at": datetime.now(timezone.utc)}}
                    )
                else:
                    all_succeeded = False
                    last_error = push_res.error
                    if push_res.token_invalid:
                        # Deactivate invalid token
                        await notification_device_collection.update_one(
                            {"_id": dev["_id"]},
                            {"$set": {"is_active": False, "deactivated_reason": "invalid_provider_token"}}
                        )
                        logger.info(f"Deactivated invalid push device token for user {user_email}")

            final_status = DeliveryStatus.DELIVERED if all_succeeded else (
                DeliveryStatus.RETRY_SCHEDULED if len(devices) > 0 and not all_succeeded else DeliveryStatus.FAILED
            )

            await cls._record_delivery(
                notification_id, user_email, NotificationChannel.PUSH,
                final_status, provider_id=last_message_id, error=last_error
            )
            return "delivered" if all_succeeded else "partial_or_failed"

        except Exception as e:
            logger.error(f"Push delivery error for {user_email}: {e}")
            await cls._record_delivery(notification_id, user_email, NotificationChannel.PUSH, DeliveryStatus.FAILED, error=str(e))
            return "failed"

    @classmethod
    async def _deliver_email(cls, notification: Dict[str, Any], user_email: str) -> str:
        """Sends email notification via Resend SDK if configured."""
        notification_id = str(notification.get("_id") or notification.get("id"))
        try:
            if not settings.RESEND_API_KEY:
                logger.info("Resend API key not configured. Email notification skipped.")
                await cls._record_delivery(notification_id, user_email, NotificationChannel.EMAIL, DeliveryStatus.ACCEPTED, error="Email provider not configured")
                return "skipped_no_api_key"

            resend.api_key = settings.RESEND_API_KEY
            title = notification.get("title", "Eventora Notification")
            message = notification.get("message", "")
            action_url = notification.get("action_url") or "https://eventora.com"

            html_body = f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #e2e8f0; border-radius: 8px;">
                <div style="margin-bottom: 20px;">
                    <h2 style="color: #4f46e5; margin: 0;">Eventora</h2>
                </div>
                <h3 style="color: #1e293b; margin-top: 0;">{title}</h3>
                <p style="color: #475569; font-size: 16px; line-height: 1.6;">{message}</p>
                <div style="margin-top: 30px;">
                    <a href="{action_url}" style="background-color: #4f46e5; color: #ffffff; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-weight: bold; display: inline-block;">View in Eventora</a>
                </div>
                <hr style="margin-top: 40px; border: none; border-top: 1px solid #e2e8f0;" />
                <p style="color: #94a3b8; font-size: 12px; margin-top: 20px;">
                    You received this email according to your Eventora notification preferences.
                </p>
            </div>
            """

            params = {
                "from": f"Eventora <{settings.MAIL_ADDRESS}>",
                "to": [user_email],
                "subject": f"Eventora: {title}",
                "html": html_body,
            }

            # Run synchronous resend call in executor
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(None, lambda: resend.Emails.send(params))

            provider_msg_id = response.get("id") if isinstance(response, dict) else str(response)
            await cls._record_delivery(
                notification_id, user_email, NotificationChannel.EMAIL,
                DeliveryStatus.DELIVERED, provider_id=provider_msg_id
            )
            return "delivered"

        except Exception as e:
            logger.error(f"Email delivery error for {user_email}: {e}")
            await cls._record_delivery(
                notification_id, user_email, NotificationChannel.EMAIL,
                DeliveryStatus.FAILED, error=str(e)
            )
            return "failed"

    @classmethod
    async def _record_delivery(
        cls,
        notification_id: str,
        user_email: str,
        channel: NotificationChannel,
        status: DeliveryStatus,
        provider_id: Optional[str] = None,
        error: Optional[str] = None
    ):
        """Creates or updates delivery tracking record in notification_delivery_collection."""
        now = datetime.now(timezone.utc)
        filter_query = {
            "notification_id": notification_id,
            "channel": str(channel)
        }
        
        # Calculate next retry if failed/retrying
        next_retry = None
        if status == DeliveryStatus.RETRY_SCHEDULED:
            next_retry = now + timedelta(seconds=60)

        update_doc = {
            "$set": {
                "recipient_user_id": user_email,
                "status": str(status),
                "provider_message_id": provider_id,
                "error_message": error,
                "updated_at": now,
                "next_retry_at": next_retry,
            },
            "$inc": {"attempt_count": 1},
            "$setOnInsert": {
                "notification_id": notification_id,
                "channel": str(channel),
                "created_at": now,
            }
        }

        await notification_delivery_collection.update_one(filter_query, update_doc, upsert=True)

    @classmethod
    async def get_user_preferences(cls, user_email: str) -> Dict[str, Any]:
        """Retrieves user notification preferences or returns default settings."""
        prefs = await notification_preference_collection.find_one({"user_email": user_email})
        if not prefs:
            return {
                "in_app_enabled": True,
                "email_enabled": True,
                "push_enabled": True,
                "web_push_enabled": True,
                "sound_enabled": True,
                "event_reminders": True,
                "voting_reminders": True,
                "leaderboard_updates": True,
                "competition_results": True,
                "course_updates": True,
                "order_updates": True,
                "reward_updates": True,
                "announcements": True,
            }
        return prefs

    @staticmethod
    def _is_type_enabled_in_prefs(prefs: Dict[str, Any], notification_type: Any) -> bool:
        """Checks if specific notification type is allowed by user preferences."""
        t_str = str(notification_type)
        type_mapping = {
            "EVENT_REMINDER": "event_reminders",
            "VOTING_REMINDER": "voting_reminders",
            "LEADERBOARD_UPDATE": "leaderboard_updates",
            "COMPETITION_RESULT": "competition_results",
            "COURSE_WORKSHOP_UPDATE": "course_updates",
            "ORDER_UPDATE": "order_updates",
            "REWARD_COIN_UPDATE": "reward_updates",
            "IMPORTANT_ANNOUNCEMENT": "announcements",
        }
        pref_key = type_mapping.get(t_str)
        if pref_key:
            return prefs.get(pref_key, True)
        # Transactional types like REGISTRATION, TICKET, PAYMENT, CERTIFICATE always allowed
        return True
