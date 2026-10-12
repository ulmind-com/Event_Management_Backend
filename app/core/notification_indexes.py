import logging
from pymongo import ASCENDING, DESCENDING, IndexModel
from app.core.database import (
    notification_collection,
    notification_preference_collection,
    notification_device_collection,
    notification_delivery_collection,
    notification_job_collection,
    notification_announcement_collection,
)

logger = logging.getLogger("eventora.notification_indexes")

async def init_notification_indexes():
    """
    Initializes required compound and unique indexes for the unified notification system collections.
    Safe and idempotent — will not recreate existing indexes or perform destructive modifications.
    """
    try:
        # 1. Notifications Collection Indexes
        await notification_collection.create_indexes([
            IndexModel([("recipient_user_id", ASCENDING), ("created_at", DESCENDING)], name="idx_notif_user_created"),
            IndexModel([("recipient_user_id", ASCENDING), ("is_read", ASCENDING)], name="idx_notif_user_unread"),
            IndexModel([("recipient_user_id", ASCENDING), ("notification_type", ASCENDING), ("created_at", DESCENDING)], name="idx_notif_user_type"),
            IndexModel([("recipient_user_id", ASCENDING), ("deduplication_key", ASCENDING)], name="idx_notif_dedup", sparse=True),
        ])

        # 2. Notification Devices Collection Indexes
        await notification_device_collection.create_indexes([
            IndexModel([("user_email", ASCENDING), ("is_active", ASCENDING)], name="idx_dev_user_active"),
            IndexModel([("user_email", ASCENDING), ("provider_token", ASCENDING)], name="idx_dev_user_token", unique=True),
        ])

        # 3. Notification Preferences Indexes
        await notification_preference_collection.create_indexes([
            IndexModel([("user_email", ASCENDING)], name="idx_pref_user", unique=True),
        ])

        # 4. Notification Deliveries Indexes
        await notification_delivery_collection.create_indexes([
            IndexModel([("notification_id", ASCENDING), ("channel", ASCENDING)], name="idx_deliv_notif_channel", unique=True),
            IndexModel([("status", ASCENDING), ("next_retry_at", ASCENDING)], name="idx_deliv_retry"),
        ])

        # 5. Notification Jobs Indexes
        await notification_job_collection.create_indexes([
            IndexModel([("idempotency_key", ASCENDING)], name="idx_job_idempotency", unique=True),
            IndexModel([("status", ASCENDING), ("scheduled_at", ASCENDING)], name="idx_job_status_scheduled"),
        ])

        # 6. Notification Announcements Indexes
        await notification_announcement_collection.create_indexes([
            IndexModel([("status", ASCENDING), ("created_at", DESCENDING)], name="idx_ann_status_created"),
            IndexModel([("created_by", ASCENDING)], name="idx_ann_creator"),
        ])

        logger.info("Notification system database indexes successfully verified/created.")
    except Exception as e:
        logger.warning(f"Error during notification indexes initialization: {e}")
