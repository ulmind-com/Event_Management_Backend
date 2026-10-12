import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional
from bson import ObjectId

from app.core.config import settings
from app.core.database import (
    notification_job_collection,
    notification_announcement_collection,
    notification_delivery_collection,
    registration_collection,
    competition_collection,
    event_collection,
)
from app.core.notification_types import (
    NotificationType,
    NotificationPriority,
    NotificationChannel,
    SoundCategory,
    DeliveryStatus,
)
from app.schemas.notification import NotificationCreate
from app.services.notification_service import NotificationService

logger = logging.getLogger("eventora.notification_scheduler")

class NotificationSchedulerService:
    """
    Manages database-backed persistent jobs for scheduled event reminders,
    voting reminders, scheduled announcements, and delivery retries.
    """

    @classmethod
    async def schedule_event_reminders(cls, event_id: str, event_title: str, start_time: datetime, offsets_hours: List[int] = [24, 1]):
        """
        Schedules durable reminder jobs for an event at configurable offsets (e.g. 24h, 1h).
        Safe against duplicate scheduling via unique idempotency keys.
        """
        now = datetime.now(timezone.utc)
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)

        for offset in offsets_hours:
            reminder_time = start_time - timedelta(hours=offset)
            if reminder_time <= now:
                continue  # Skip offsets that are already in the past

            idempotency_key = f"event_reminder_{event_id}_{offset}h"
            job_doc = {
                "job_type": "EVENT_REMINDER",
                "idempotency_key": idempotency_key,
                "status": "PENDING",
                "scheduled_at": reminder_time,
                "payload": {
                    "event_id": str(event_id),
                    "event_title": event_title,
                    "offset_hours": offset,
                    "start_time": start_time.isoformat(),
                },
                "attempts": 0,
                "created_at": now,
                "updated_at": now,
            }

            await notification_job_collection.update_one(
                {"idempotency_key": idempotency_key},
                {"$setOnInsert": job_doc},
                upsert=True
            )
            logger.info(f"Scheduled reminder job for event '{event_id}' at {reminder_time.isoformat()} ({offset}h before)")

    @classmethod
    async def invalidate_event_reminders(cls, event_id: str, is_cancelled: bool = False, new_start_time: Optional[datetime] = None, event_title: str = ""):
        """
        Cancels pending reminder jobs if an event is cancelled or rescheduled.
        If rescheduled with new_start_time, schedules new reminders.
        """
        now = datetime.now(timezone.utc)
        cancel_res = await notification_job_collection.update_many(
            {
                "job_type": "EVENT_REMINDER",
                "payload.event_id": str(event_id),
                "status": "PENDING"
            },
            {"$set": {"status": "CANCELLED", "updated_at": now, "cancelled_reason": "Event modified/cancelled"}}
        )
        logger.info(f"Cancelled {cancel_res.modified_count} pending reminder jobs for event '{event_id}'")

        if is_cancelled:
            # Trigger cancellation notification to all registered users
            registrations = await registration_collection.find({
                "activity_id": str(event_id),
                "status": {"$in": ["confirmed", "completed", "success"]}
            }).to_list(2000)

            user_emails = [r.get("user_email") for r in registrations if r.get("user_email")]
            if user_emails:
                await NotificationService.create_bulk_notifications(
                    recipient_emails=user_emails,
                    notification_type=NotificationType.IMPORTANT_ANNOUNCEMENT,
                    title="Event Cancelled",
                    message=f"The event '{event_title or 'Registered Event'}' has been cancelled. Please check your account for details.",
                    resource_type="event",
                    resource_id=str(event_id),
                    priority=NotificationPriority.URGENT,
                    sound_category=SoundCategory.ALERT,
                    dedup_prefix=f"event_cancelled_{event_id}",
                )

        elif new_start_time:
            # Reschedule with new start time
            await cls.schedule_event_reminders(event_id=event_id, event_title=event_title, start_time=new_start_time)

    @classmethod
    async def schedule_voting_reminders(cls, competition_id: str, comp_title: str, voting_end_time: datetime, offset_hours: int = 2):
        """Schedules voting deadline reminder for eligible voters."""
        now = datetime.now(timezone.utc)
        if voting_end_time.tzinfo is None:
            voting_end_time = voting_end_time.replace(tzinfo=timezone.utc)

        reminder_time = voting_end_time - timedelta(hours=offset_hours)
        if reminder_time <= now:
            return

        idempotency_key = f"voting_reminder_{competition_id}_{offset_hours}h"
        job_doc = {
            "job_type": "VOTING_REMINDER",
            "idempotency_key": idempotency_key,
            "status": "PENDING",
            "scheduled_at": reminder_time,
            "payload": {
                "competition_id": str(competition_id),
                "competition_title": comp_title,
                "voting_end_time": voting_end_time.isoformat(),
            },
            "attempts": 0,
            "created_at": now,
            "updated_at": now,
        }

        await notification_job_collection.update_one(
            {"idempotency_key": idempotency_key},
            {"$setOnInsert": job_doc},
            upsert=True
        )

    @classmethod
    async def process_due_jobs(cls) -> Dict[str, int]:
        """
        Pulls and executes all due jobs atomically.
        """
        now = datetime.now(timezone.utc)
        due_jobs = await notification_job_collection.find({
            "status": "PENDING",
            "scheduled_at": {"$lte": now}
        }).limit(50).to_list(50)

        results = {"processed": 0, "failed": 0}

        for job in due_jobs:
            job_id = job["_id"]
            # Atomic state transition to PROCESSING
            lock = await notification_job_collection.find_one_and_update(
                {"_id": job_id, "status": "PENDING"},
                {"$set": {"status": "PROCESSING", "updated_at": now}, "$inc": {"attempts": 1}}
            )
            if not lock:
                continue

            try:
                job_type = job.get("job_type")
                if job_type == "EVENT_REMINDER":
                    await cls._execute_event_reminder(job)
                elif job_type == "VOTING_REMINDER":
                    await cls._execute_voting_reminder(job)
                elif job_type == "SCHEDULED_ANNOUNCEMENT":
                    await cls._execute_scheduled_announcement(job)
                elif job_type == "DELIVERY_RETRY":
                    await cls._execute_delivery_retry(job)

                await notification_job_collection.update_one(
                    {"_id": job_id},
                    {"$set": {"status": "COMPLETED", "completed_at": datetime.now(timezone.utc)}}
                )
                results["processed"] += 1
            except Exception as e:
                logger.error(f"Error executing job {job_id}: {e}", exc_info=True)
                await notification_job_collection.update_one(
                    {"_id": job_id},
                    {"$set": {"status": "FAILED", "error": str(e), "failed_at": datetime.now(timezone.utc)}}
                )
                results["failed"] += 1

        # Also process transient delivery retries
        await cls._retry_transient_deliveries()

        return results

    @classmethod
    async def _execute_event_reminder(cls, job: Dict[str, Any]):
        payload = job.get("payload", {})
        event_id = payload.get("event_id")
        event_title = payload.get("event_title", "Event")
        offset_hours = payload.get("offset_hours", 1)

        # Query all users registered for this event
        registrations = await registration_collection.find({
            "activity_id": event_id,
            "status": {"$in": ["confirmed", "completed", "success"]}
        }).to_list(2000)

        user_emails = [r.get("user_email") for r in registrations if r.get("user_email")]
        if not user_emails:
            logger.info(f"No registered attendees found for event reminder {event_id}")
            return

        time_desc = "1 hour" if offset_hours == 1 else f"{offset_hours} hours"
        await NotificationService.create_bulk_notifications(
            recipient_emails=user_emails,
            notification_type=NotificationType.EVENT_REMINDER,
            title=f"Reminder: {event_title} starts in {time_desc}",
            message=f"Get ready! Your registered event '{event_title}' will begin in {time_desc}.",
            resource_type="event",
            resource_id=event_id,
            action_url=f"/events/{event_id}",
            priority=NotificationPriority.NORMAL,
            sound_category=SoundCategory.ALERT,
            dedup_prefix=f"remind_{event_id}_{offset_hours}h",
        )

    @classmethod
    async def _execute_voting_reminder(cls, job: Dict[str, Any]):
        payload = job.get("payload", {})
        comp_id = payload.get("competition_id")
        comp_title = payload.get("competition_title", "Competition")

        # Verify voting is STILL open
        comp = None
        if ObjectId.is_valid(comp_id):
            comp = await competition_collection.find_one({"_id": ObjectId(comp_id)})
        if not comp or comp.get("voting_status") != "OPEN":
            logger.info(f"Voting reminder skipped for competition {comp_id} — voting is closed.")
            return

        # Target registered users
        registrations = await registration_collection.find({
            "activity_id": str(comp_id),
            "status": {"$in": ["confirmed", "completed", "success"]}
        }).to_list(2000)

        user_emails = [r.get("user_email") for r in registrations if r.get("user_email")]
        if user_emails:
            await NotificationService.create_bulk_notifications(
                recipient_emails=user_emails,
                notification_type=NotificationType.VOTING_REMINDER,
                title=f"Voting Ending Soon: {comp_title}",
                message=f"Cast your vote for '{comp_title}' before the voting window closes!",
                resource_type="competition",
                resource_id=str(comp_id),
                action_url=f"/competitions/{comp_id}/vote",
                priority=NotificationPriority.NORMAL,
                sound_category=SoundCategory.ALERT,
                dedup_prefix=f"vote_remind_{comp_id}",
            )

    @classmethod
    async def _execute_scheduled_announcement(cls, job: Dict[str, Any]):
        announcement_id = job.get("payload", {}).get("announcement_id")
        if not ObjectId.is_valid(announcement_id):
            return

        announcement = await notification_announcement_collection.find_one({"_id": ObjectId(announcement_id)})
        if not announcement or announcement.get("status") != "SCHEDULED":
            return

        from app.services.announcement_service import AnnouncementService
        await AnnouncementService.publish_announcement(announcement_id=str(announcement["_id"]), admin_email=announcement.get("created_by", "system"))

    @classmethod
    async def _execute_delivery_retry(cls, job: Dict[str, Any]):
        delivery_id = job.get("payload", {}).get("delivery_id")
        if ObjectId.is_valid(delivery_id):
            await cls._retry_single_delivery(ObjectId(delivery_id))

    @classmethod
    async def _retry_transient_deliveries(cls):
        """Retries deliveries scheduled for retry where next_retry_at <= now."""
        now = datetime.now(timezone.utc)
        retry_candidates = await notification_delivery_collection.find({
            "status": DeliveryStatus.RETRY_SCHEDULED,
            "next_retry_at": {"$lte": now},
            "attempt_count": {"$lt": settings.NOTIFICATION_MAX_RETRIES}
        }).limit(20).to_list(20)

        for delivery in retry_candidates:
            await cls._retry_single_delivery(delivery["_id"])

    @classmethod
    async def _retry_single_delivery(cls, delivery_doc_id: ObjectId):
        now = datetime.now(timezone.utc)
        delivery = await notification_delivery_collection.find_one_and_update(
            {"_id": delivery_doc_id, "status": DeliveryStatus.RETRY_SCHEDULED},
            {"$set": {"status": DeliveryStatus.PROCESSING, "updated_at": now}}
        )
        if not delivery:
            return

        notification_id = delivery.get("notification_id")
        user_email = delivery.get("recipient_user_id")
        channel = delivery.get("channel")

        if not ObjectId.is_valid(notification_id):
            return

        from app.core.database import notification_collection
        notif = await notification_collection.find_one({"_id": ObjectId(notification_id)})
        if not notif:
            return

        from app.services.notification_dispatcher import NotificationDispatcher
        if channel == NotificationChannel.PUSH:
            res = await NotificationDispatcher._deliver_push(notif, user_email)
        elif channel == NotificationChannel.EMAIL:
            res = await NotificationDispatcher._deliver_email(notif, user_email)


class NotificationSchedulerWorker:
    """
    Background worker loop executing due scheduled jobs.
    Matches the pattern and stability of ExpiryWorker.
    """
    _task: Optional[asyncio.Task] = None
    _running: bool = False

    @classmethod
    async def _worker_loop(cls, interval_seconds: int = 60):
        while cls._running:
            try:
                if settings.NOTIFICATION_SCHEDULER_ENABLED:
                    await NotificationSchedulerService.process_due_jobs()
            except Exception as e:
                logger.error(f"Error in NotificationScheduler worker loop: {e}", exc_info=True)
            try:
                await asyncio.sleep(interval_seconds)
            except asyncio.CancelledError:
                break

    @classmethod
    def start(cls, interval_seconds: int = 60):
        if cls._running:
            return
        cls._running = True
        cls._task = asyncio.create_task(cls._worker_loop(interval_seconds))
        logger.info(f"Notification Scheduler Worker started with interval {interval_seconds}s")

    @classmethod
    async def stop(cls):
        if not cls._running:
            return
        cls._running = False
        if cls._task:
            cls._task.cancel()
            try:
                await cls._task
            except asyncio.CancelledError:
                pass
        logger.info("Notification Scheduler Worker stopped.")
