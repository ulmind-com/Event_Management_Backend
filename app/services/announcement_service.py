import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from bson import ObjectId

from app.core.database import (
    notification_announcement_collection,
    notification_job_collection,
    user_collection,
    registration_collection,
    competition_registration_collection,
)
from app.core.notification_types import (
    NotificationType,
    NotificationPriority,
    SoundCategory,
    AudienceType,
    to_enum_val,
)
from app.schemas.notification import (
    AnnouncementCreate,
    AnnouncementUpdate,
    AnnouncementResponse,
    AnnouncementListResponse,
)
from app.services.notification_service import NotificationService

logger = logging.getLogger("eventora.announcement_service")

class AnnouncementService:
    """
    Manages Super Admin announcements: drafting, audience targeting, scheduling, and publishing.
    """

    @classmethod
    async def create_announcement(cls, admin_email: str, req: AnnouncementCreate) -> AnnouncementResponse:
        now = datetime.now(timezone.utc)
        status = "SCHEDULED" if req.scheduled_at and req.scheduled_at > now else "DRAFT"

        doc = {
            "title": req.title,
            "message": req.message,
            "audience_type": to_enum_val(req.audience_type),
            "audience_target_id": req.audience_target_id,
            "explicit_user_emails": req.explicit_user_emails or [],
            "priority": to_enum_val(req.priority),
            "action_url": req.action_url,
            "channels": [to_enum_val(c) for c in (req.channels or [])],
            "status": status,
            "created_by": admin_email,
            "created_at": now,
            "scheduled_at": req.scheduled_at,
            "published_at": None,
            "recipients_count": 0,
            "sound_category": to_enum_val(req.sound_category),
            "metadata": req.metadata or {},
        }

        res = await notification_announcement_collection.insert_one(doc)
        announcement_id = str(res.inserted_id)

        # If scheduled for future, register in notification_jobs
        if status == "SCHEDULED":
            await notification_job_collection.insert_one({
                "job_type": "SCHEDULED_ANNOUNCEMENT",
                "idempotency_key": f"announcement_{announcement_id}",
                "status": "PENDING",
                "scheduled_at": req.scheduled_at,
                "payload": {"announcement_id": announcement_id},
                "attempts": 0,
                "created_at": now,
                "updated_at": now,
            })

        return await cls.get_announcement_by_id(announcement_id)

    @classmethod
    async def update_announcement(cls, announcement_id: str, req: AnnouncementUpdate) -> Optional[AnnouncementResponse]:
        if not ObjectId.is_valid(announcement_id):
            return None

        existing = await notification_announcement_collection.find_one({"_id": ObjectId(announcement_id)})
        if not existing or existing.get("status") in ["PUBLISHED", "CANCELLED"]:
            return None

        update_dict = {k: v for k, v in req.model_dump(exclude_unset=True).items() if v is not None}
        if "audience_type" in update_dict and update_dict["audience_type"]:
            update_dict["audience_type"] = to_enum_val(update_dict["audience_type"])
        if "priority" in update_dict and update_dict["priority"]:
            update_dict["priority"] = to_enum_val(update_dict["priority"])
        if "sound_category" in update_dict and update_dict["sound_category"]:
            update_dict["sound_category"] = to_enum_val(update_dict["sound_category"])
        if "channels" in update_dict and update_dict["channels"]:
            update_dict["channels"] = [to_enum_val(c) for c in update_dict["channels"]]

        update_dict["updated_at"] = datetime.now(timezone.utc)

        await notification_announcement_collection.update_one(
            {"_id": ObjectId(announcement_id)},
            {"$set": update_dict}
        )
        return await cls.get_announcement_by_id(announcement_id)

    @classmethod
    async def publish_announcement(cls, announcement_id: str, admin_email: str) -> Optional[AnnouncementResponse]:
        """
        Publishes announcement immediately and delivers to targeted audience.
        """
        if not ObjectId.is_valid(announcement_id):
            return None

        announcement = await notification_announcement_collection.find_one({"_id": ObjectId(announcement_id)})
        if not announcement or announcement.get("status") == "PUBLISHED":
            return None

        now = datetime.now(timezone.utc)

        # Resolve audience
        recipient_emails = await cls._resolve_audience(
            audience_type=announcement.get("audience_type", "ALL"),
            target_id=announcement.get("audience_target_id"),
            explicit_emails=announcement.get("explicit_user_emails")
        )

        # Deliver via central bulk notification service
        delivered_count = 0
        if recipient_emails:
            delivered_count = await NotificationService.create_bulk_notifications(
                recipient_emails=recipient_emails,
                notification_type=NotificationType.IMPORTANT_ANNOUNCEMENT,
                title=announcement.get("title", "Important Announcement"),
                message=announcement.get("message", ""),
                action_url=announcement.get("action_url"),
                priority=NotificationPriority(announcement.get("priority", "URGENT")),
                sound_category=SoundCategory(announcement.get("sound_category", "ANNOUNCEMENT")),
                dedup_prefix=f"announcement_{announcement_id}",
                metadata={"announcement_id": announcement_id},
            )

        # Update announcement status
        await notification_announcement_collection.update_one(
            {"_id": ObjectId(announcement_id)},
            {"$set": {
                "status": "PUBLISHED",
                "published_at": now,
                "published_by": admin_email,
                "recipients_count": delivered_count,
            }}
        )

        return await cls.get_announcement_by_id(announcement_id)

    @classmethod
    async def cancel_announcement(cls, announcement_id: str) -> bool:
        """Cancels a scheduled or draft announcement."""
        if not ObjectId.is_valid(announcement_id):
            return False

        now = datetime.now(timezone.utc)
        res = await notification_announcement_collection.update_one(
            {"_id": ObjectId(announcement_id), "status": {"$in": ["DRAFT", "SCHEDULED"]}},
            {"$set": {"status": "CANCELLED", "cancelled_at": now}}
        )

        if res.modified_count > 0:
            await notification_job_collection.update_one(
                {"idempotency_key": f"announcement_{announcement_id}", "status": "PENDING"},
                {"$set": {"status": "CANCELLED", "updated_at": now}}
            )
            return True
        return False

    @classmethod
    async def get_announcement_by_id(cls, announcement_id: str) -> Optional[AnnouncementResponse]:
        if not ObjectId.is_valid(announcement_id):
            return None

        doc = await notification_announcement_collection.find_one({"_id": ObjectId(announcement_id)})
        if not doc:
            return None

        return AnnouncementResponse(
            id=str(doc["_id"]),
            title=doc["title"],
            message=doc["message"],
            audience_type=to_enum_val(doc.get("audience_type", AudienceType.ALL)),
            audience_target_id=doc.get("audience_target_id"),
            explicit_user_emails=doc.get("explicit_user_emails"),
            priority=to_enum_val(doc.get("priority", NotificationPriority.URGENT)),
            action_url=doc.get("action_url"),
            status=doc.get("status", "DRAFT"),
            created_by=doc.get("created_by", "admin"),
            created_at=doc.get("created_at"),
            scheduled_at=doc.get("scheduled_at"),
            published_at=doc.get("published_at"),
            recipients_count=doc.get("recipients_count", 0),
            delivery_summary=doc.get("delivery_summary"),
        )

    @classmethod
    async def list_announcements(cls, page: int = 1, page_size: int = 20) -> AnnouncementListResponse:
        skip = max(0, (page - 1) * page_size)
        total = await notification_announcement_collection.count_documents({})
        cursor = notification_announcement_collection.find({}).sort("created_at", -1).skip(skip).limit(page_size)
        docs = await cursor.to_list(page_size)

        items = []
        for doc in docs:
            items.append(AnnouncementResponse(
                id=str(doc["_id"]),
                title=doc["title"],
                message=doc["message"],
                audience_type=to_enum_val(doc.get("audience_type", AudienceType.ALL)),
                audience_target_id=doc.get("audience_target_id"),
                explicit_user_emails=doc.get("explicit_user_emails"),
                priority=to_enum_val(doc.get("priority", NotificationPriority.URGENT)),
                action_url=doc.get("action_url"),
                status=doc.get("status", "DRAFT"),
                created_by=doc.get("created_by", "admin"),
                created_at=doc.get("created_at"),
                scheduled_at=doc.get("scheduled_at"),
                published_at=doc.get("published_at"),
                recipients_count=doc.get("recipients_count", 0),
                delivery_summary=doc.get("delivery_summary"),
            ))

        return AnnouncementListResponse(items=items, total=total, page=page, page_size=page_size)

    @classmethod
    async def _resolve_audience(cls, audience_type: str, target_id: Optional[str], explicit_emails: Optional[List[str]]) -> List[str]:
        """Resolves target recipient emails based on audience scope."""
        if audience_type == "EXPLICIT_USERS" and explicit_emails:
            return list(set(explicit_emails))

        if audience_type == "ALL":
            users = await user_collection.find({}, {"email": 1}).to_list(10000)
            return [u["email"] for u in users if u.get("email")]

        if audience_type in ["EVENT_REGISTRANTS", "COURSE_ENROLLEES"] and target_id:
            regs = await registration_collection.find({
                "activity_id": target_id,
                "status": {"$in": ["confirmed", "completed", "success"]}
            }, {"user_email": 1}).to_list(5000)
            return list(set([r["user_email"] for r in regs if r.get("user_email")]))

        if audience_type == "COMPETITION_REGISTRANTS" and target_id:
            regs = await competition_registration_collection.find({
                "competition_id": target_id
            }, {"user_email": 1}).to_list(5000)
            if not regs:
                # Fallback to standard registration collection
                regs = await registration_collection.find({
                    "activity_id": target_id
                }, {"user_email": 1}).to_list(5000)
            return list(set([r["user_email"] for r in regs if r.get("user_email")]))

        return []
