import logging
import asyncio
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from bson import ObjectId

from app.core.config import settings
from app.core.database import (
    notification_collection,
    notification_preference_collection,
    notification_device_collection,
    notification_delivery_collection,
    notification_job_collection,
    notification_announcement_collection,
)
from app.core.notification_types import (
    NotificationType,
    NotificationPriority,
    NotificationChannel,
    DeliveryStatus,
    SoundCategory,
    DevicePlatform,
    NOTIFICATION_TYPE_CONFIG,
    to_enum_val,
)
from app.schemas.notification import (
    NotificationCreate,
    NotificationResponse,
    NotificationListResponse,
    NotificationPreferencesUpdate,
    NotificationPreferencesResponse,
    DeviceRegisterRequest,
    DeviceResponse,
    NotificationAnalyticsResponse,
)
from app.services.notification_dispatcher import NotificationDispatcher

logger = logging.getLogger("eventora.notification_service")

class NotificationService:
    """
    Central authoritative notification service for Eventora.
    Handles persistence, idempotency, preference filtering, queries, device tokens, and dispatching.
    """

    @classmethod
    async def create_notification(cls, data: NotificationCreate) -> Dict[str, Any]:
        """
        Creates, persists, and asynchronously dispatches a single notification.
        Idempotent when deduplication_key is provided.
        """
        now = datetime.now(timezone.utc)
        type_cfg = NOTIFICATION_TYPE_CONFIG.get(data.notification_type, {})

        # 1. Idempotency Check
        if data.deduplication_key:
            existing = await notification_collection.find_one({
                "recipient_user_id": data.recipient_user_id,
                "deduplication_key": data.deduplication_key
            })
            if existing:
                logger.info(f"Duplicate notification suppressed by key '{data.deduplication_key}' for user '{data.recipient_user_id}'")
                existing["id"] = str(existing["_id"])
                return existing

        # 2. Defaults from Type Config
        priority = data.priority or type_cfg.get("default_priority", NotificationPriority.NORMAL)
        sound_category = data.sound_category or type_cfg.get("sound_category", SoundCategory.DEFAULT)
        resource_type = data.resource_type or type_cfg.get("default_resource_type")

        doc = {
            "recipient_user_id": data.recipient_user_id,
            "notification_type": to_enum_val(data.notification_type),
            "title": data.title,
            "message": data.message,
            "resource_type": resource_type,
            "resource_id": data.resource_id,
            "action_url": data.action_url,
            "priority": to_enum_val(priority),
            "sound_category": to_enum_val(sound_category),
            "is_read": False,
            "read_at": None,
            "created_at": now,
            "scheduled_at": data.scheduled_at,
            "expires_at": data.expires_at,
            "source_module": data.source_module,
            "source_event_id": data.source_event_id,
            "deduplication_key": data.deduplication_key,
            "channels": [to_enum_val(c) for c in (data.channels or [NotificationChannel.IN_APP, NotificationChannel.WEBSOCKET, NotificationChannel.PUSH, NotificationChannel.EMAIL])],
            "metadata": data.metadata or {},
        }


        # 3. Persist to MongoDB
        insert_res = await notification_collection.insert_one(doc)
        doc["_id"] = insert_res.inserted_id
        doc["id"] = str(insert_res.inserted_id)

        # 4. Asynchronous Channel Delivery (fire and forget task, failure never undoes DB write)
        asyncio.create_task(cls._safe_dispatch(doc, data.channels))

        return doc

    @classmethod
    async def _safe_dispatch(cls, notification_doc: Dict[str, Any], channels: Optional[List[NotificationChannel]]):
        try:
            await NotificationDispatcher.dispatch(notification_doc, channels)
        except Exception as e:
            logger.error(f"Error during notification dispatch: {e}", exc_info=True)

    @classmethod
    async def create_bulk_notifications(
        cls,
        recipient_emails: List[str],
        notification_type: NotificationType,
        title: str,
        message: str,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        action_url: Optional[str] = None,
        priority: NotificationPriority = NotificationPriority.NORMAL,
        sound_category: SoundCategory = SoundCategory.DEFAULT,
        dedup_prefix: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        channels: Optional[List[NotificationChannel]] = None,
    ) -> int:
        """
        Creates notifications for a list of recipients in batches to prevent memory spikes.
        """
        if not recipient_emails:
            return 0

        unique_recipients = list(set(recipient_emails))
        batch_size = settings.NOTIFICATION_BATCH_SIZE
        created_count = 0

        for i in range(0, len(unique_recipients), batch_size):
            chunk = unique_recipients[i:i + batch_size]
            for email in chunk:
                dedup_key = f"{dedup_prefix}_{email}" if dedup_prefix else None
                create_req = NotificationCreate(
                    recipient_user_id=email,
                    notification_type=notification_type,
                    title=title,
                    message=message,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    action_url=action_url,
                    priority=priority,
                    sound_category=sound_category,
                    deduplication_key=dedup_key,
                    metadata=metadata,
                    channels=channels
                )
                await cls.create_notification(create_req)
                created_count += 1

        logger.info(f"Bulk notification created for {created_count} recipients (Type: {notification_type})")
        return created_count

    @classmethod
    async def get_user_notifications(
        cls,
        user_email: str,
        is_read: Optional[bool] = None,
        notification_type: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> NotificationListResponse:
        """
        Retrieves paginated notifications for the authenticated user only.
        """
        query: Dict[str, Any] = {"recipient_user_id": user_email}
        if is_read is not None:
            query["is_read"] = is_read
        if notification_type:
            query["notification_type"] = notification_type

        skip = max(0, (page - 1) * page_size)
        total = await notification_collection.count_documents(query)
        unread_count = await notification_collection.count_documents({
            "recipient_user_id": user_email,
            "is_read": False
        })

        cursor = notification_collection.find(query).sort("created_at", -1).skip(skip).limit(page_size)
        docs = await cursor.to_list(page_size)

        items = []
        for d in docs:
            n_type = to_enum_val(d.get("notification_type"))
            prio = to_enum_val(d.get("priority", "NORMAL"))
            snd = to_enum_val(d.get("sound_category", "DEFAULT"))
            items.append(NotificationResponse(
                id=str(d["_id"]),
                recipient_user_id=d["recipient_user_id"],
                notification_type=NotificationType(n_type) if n_type in NotificationType.__members__ else NotificationType.EVENT_REMINDER,
                title=d["title"],
                message=d["message"],
                resource_type=d.get("resource_type"),
                resource_id=d.get("resource_id"),
                action_url=d.get("action_url"),
                priority=NotificationPriority(prio) if prio in NotificationPriority.__members__ else NotificationPriority.NORMAL,
                sound_category=SoundCategory(snd) if snd in SoundCategory.__members__ else SoundCategory.DEFAULT,
                is_read=d.get("is_read", False),
                read_at=d.get("read_at"),
                created_at=d.get("created_at"),
                scheduled_at=d.get("scheduled_at"),
                expires_at=d.get("expires_at"),
                source_module=d.get("source_module"),
                metadata=d.get("metadata"),
            ))

        return NotificationListResponse(
            items=items,
            total=total,
            unread_count=unread_count,
            page=page,
            page_size=page_size,
            has_next=(skip + len(items)) < total
        )

    @classmethod
    async def get_notification_by_id(cls, notification_id: str, user_email: str) -> Optional[NotificationResponse]:
        """
        Gets a single notification ensuring ownership by authenticated user.
        """
        if not ObjectId.is_valid(notification_id):
            return None

        doc = await notification_collection.find_one({
            "_id": ObjectId(notification_id),
            "recipient_user_id": user_email
        })
        if not doc:
            return None

        n_type = to_enum_val(doc.get("notification_type"))
        prio = to_enum_val(doc.get("priority", "NORMAL"))
        snd = to_enum_val(doc.get("sound_category", "DEFAULT"))
        return NotificationResponse(
            id=str(doc["_id"]),
            recipient_user_id=doc["recipient_user_id"],
            notification_type=NotificationType(n_type) if n_type in NotificationType.__members__ else NotificationType.EVENT_REMINDER,
            title=doc["title"],
            message=doc["message"],
            resource_type=doc.get("resource_type"),
            resource_id=doc.get("resource_id"),
            action_url=doc.get("action_url"),
            priority=NotificationPriority(prio) if prio in NotificationPriority.__members__ else NotificationPriority.NORMAL,
            sound_category=SoundCategory(snd) if snd in SoundCategory.__members__ else SoundCategory.DEFAULT,
            is_read=doc.get("is_read", False),
            read_at=doc.get("read_at"),
            created_at=doc.get("created_at"),
            scheduled_at=doc.get("scheduled_at"),
            expires_at=doc.get("expires_at"),
            source_module=doc.get("source_module"),
            metadata=doc.get("metadata"),
        )


    @classmethod
    async def get_unread_count(cls, user_email: str) -> int:
        return await notification_collection.count_documents({
            "recipient_user_id": user_email,
            "is_read": False
        })

    @classmethod
    async def mark_as_read(cls, notification_id: str, user_email: str) -> bool:
        """Marks a notification as read idempotently."""
        if not ObjectId.is_valid(notification_id):
            return False

        now = datetime.now(timezone.utc)
        result = await notification_collection.update_one(
            {"_id": ObjectId(notification_id), "recipient_user_id": user_email},
            {"$set": {"is_read": True, "read_at": now}}
        )
        return result.matched_count > 0

    @classmethod
    async def mark_as_unread(cls, notification_id: str, user_email: str) -> bool:
        """Marks a notification as unread idempotently."""
        if not ObjectId.is_valid(notification_id):
            return False

        result = await notification_collection.update_one(
            {"_id": ObjectId(notification_id), "recipient_user_id": user_email},
            {"$set": {"is_read": False, "read_at": None}}
        )
        return result.matched_count > 0

    @classmethod
    async def mark_all_as_read(cls, user_email: str) -> int:
        """Marks all notifications as read for the authenticated user only."""
        now = datetime.now(timezone.utc)
        result = await notification_collection.update_many(
            {"recipient_user_id": user_email, "is_read": False},
            {"$set": {"is_read": True, "read_at": now}}
        )
        return result.modified_count

    @classmethod
    async def delete_notification(cls, notification_id: str, user_email: str) -> bool:
        """
        Deletes notification document for authenticated user.
        Does NOT touch any underlying business records (payments, tickets, etc.).
        """
        if not ObjectId.is_valid(notification_id):
            return False

        result = await notification_collection.delete_one({
            "_id": ObjectId(notification_id),
            "recipient_user_id": user_email
        })
        return result.deleted_count > 0

    # ── Device Management ──

    @classmethod
    async def register_device(cls, user_email: str, req: DeviceRegisterRequest) -> DeviceResponse:
        """Registers or activates a device token for the authenticated user."""
        now = datetime.now(timezone.utc)
        filter_query = {
            "user_email": user_email,
            "provider_token": req.provider_token
        }
        update_data = {
            "$set": {
                "platform": to_enum_val(req.platform),
                "app_identifier": req.app_identifier,
                "device_label": req.device_label,
                "is_active": True,
                "last_used_at": now,
            },
            "$setOnInsert": {
                "user_email": user_email,
                "provider_token": req.provider_token,
                "created_at": now,
            }
        }
        result = await notification_device_collection.update_one(filter_query, update_data, upsert=True)
        doc = await notification_device_collection.find_one(filter_query)

        plat_str = to_enum_val(doc["platform"])
        return DeviceResponse(
            id=str(doc["_id"]),
            user_email=doc["user_email"],
            platform=DevicePlatform(plat_str) if plat_str in DevicePlatform.__members__ else DevicePlatform.ANDROID,
            app_identifier=doc.get("app_identifier"),
            device_label=doc.get("device_label"),
            is_active=doc.get("is_active", True),
            created_at=doc["created_at"],
            last_used_at=doc.get("last_used_at"),
        )

    @classmethod
    async def unregister_device(cls, device_id: str, user_email: str) -> bool:
        """Deactivates a device token for authenticated user upon logout."""
        if not ObjectId.is_valid(device_id):
            return False

        result = await notification_device_collection.update_one(
            {"_id": ObjectId(device_id), "user_email": user_email},
            {"$set": {"is_active": False, "unregistered_at": datetime.now(timezone.utc)}}
        )
        return result.matched_count > 0

    @classmethod
    async def get_user_devices(cls, user_email: str) -> List[DeviceResponse]:
        """Lists active devices registered to the authenticated user."""
        cursor = notification_device_collection.find({"user_email": user_email, "is_active": True})
        docs = await cursor.to_list(100)
        res = []
        for d in docs:
            p_str = to_enum_val(d["platform"])
            res.append(DeviceResponse(
                id=str(d["_id"]),
                user_email=d["user_email"],
                platform=DevicePlatform(p_str) if p_str in DevicePlatform.__members__ else DevicePlatform.ANDROID,
                app_identifier=d.get("app_identifier"),
                device_label=d.get("device_label"),
                is_active=d.get("is_active", True),
                created_at=d["created_at"],
                last_used_at=d.get("last_used_at"),
            ))
        return res


    # ── User Preferences ──

    @classmethod
    async def get_preferences(cls, user_email: str) -> NotificationPreferencesResponse:
        prefs = await notification_preference_collection.find_one({"user_email": user_email})
        if not prefs:
            return NotificationPreferencesResponse(user_email=user_email)

        return NotificationPreferencesResponse(
            user_email=user_email,
            in_app_enabled=prefs.get("in_app_enabled", True),
            email_enabled=prefs.get("email_enabled", True),
            push_enabled=prefs.get("push_enabled", True),
            web_push_enabled=prefs.get("web_push_enabled", True),
            sound_enabled=prefs.get("sound_enabled", True),
            event_reminders=prefs.get("event_reminders", True),
            voting_reminders=prefs.get("voting_reminders", True),
            leaderboard_updates=prefs.get("leaderboard_updates", True),
            competition_results=prefs.get("competition_results", True),
            course_updates=prefs.get("course_updates", True),
            order_updates=prefs.get("order_updates", True),
            reward_updates=prefs.get("reward_updates", True),
            announcements=prefs.get("announcements", True),
            updated_at=prefs.get("updated_at"),
        )

    @classmethod
    async def update_preferences(cls, user_email: str, req: NotificationPreferencesUpdate) -> NotificationPreferencesResponse:
        now = datetime.now(timezone.utc)
        update_dict = {k: v for k, v in req.model_dump(exclude_unset=True).items() if v is not None}
        update_dict["updated_at"] = now

        await notification_preference_collection.update_one(
            {"user_email": user_email},
            {"$set": update_dict, "$setOnInsert": {"user_email": user_email}},
            upsert=True
        )
        return await cls.get_preferences(user_email)

    # ── Operational Analytics ──

    @classmethod
    async def get_analytics(cls) -> NotificationAnalyticsResponse:
        total_notifications = await notification_collection.count_documents({})
        unread_notifications = await notification_collection.count_documents({"is_read": False})
        active_devices = await notification_device_collection.count_documents({"is_active": True})
        pending_jobs = await notification_job_collection.count_documents({"status": "PENDING"})
        failed_deliveries = await notification_delivery_collection.count_documents({"status": "FAILED"})

        # Group by notification type
        type_agg = await notification_collection.aggregate([
            {"$group": {"_id": "$notification_type", "count": {"$sum": 1}}}
        ]).to_list(100)
        notifications_by_type = {item["_id"]: item["count"] for item in type_agg if item["_id"]}

        # Group deliveries by channel
        channel_agg = await notification_delivery_collection.aggregate([
            {"$group": {"_id": "$channel", "count": {"$sum": 1}}}
        ]).to_list(100)
        deliveries_by_channel = {item["_id"]: item["count"] for item in channel_agg if item["_id"]}

        # Group deliveries by status
        status_agg = await notification_delivery_collection.aggregate([
            {"$group": {"_id": "$status", "count": {"$sum": 1}}}
        ]).to_list(100)
        deliveries_by_status = {item["_id"]: item["count"] for item in status_agg if item["_id"]}

        return NotificationAnalyticsResponse(
            total_notifications=total_notifications,
            unread_notifications=unread_notifications,
            notifications_by_type=notifications_by_type,
            deliveries_by_channel=deliveries_by_channel,
            deliveries_by_status=deliveries_by_status,
            active_devices_count=active_devices,
            pending_scheduled_jobs=pending_jobs,
            failed_deliveries_count=failed_deliveries,
        )
