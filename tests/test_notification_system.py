import pytest
import pytest_asyncio
import uuid
from datetime import datetime, timezone, timedelta
from httpx import AsyncClient, ASGITransport
from bson import ObjectId

from app.main import app
from app.core.security import create_access_token
from app.core.database import (
    user_collection,
    admin_collection,
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
    SoundCategory,
    AudienceType,
    DevicePlatform,
)
from app.schemas.notification import NotificationCreate
from app.services.notification_service import NotificationService
from app.services.announcement_service import AnnouncementService
from app.services.notification_scheduler import NotificationSchedulerService

pytestmark = pytest.mark.asyncio

@pytest_asyncio.fixture(autouse=True)
async def cleanup_test_notifications():
    """Cleans up test notification artifacts before and after tests."""
    pattern = {"$regex": "^test_notif_.*@eventora.test$"}
    await notification_collection.delete_many({"recipient_user_id": pattern})
    await notification_preference_collection.delete_many({"user_email": pattern})
    await notification_device_collection.delete_many({"user_email": pattern})
    await notification_delivery_collection.delete_many({"recipient_user_id": pattern})
    await notification_job_collection.delete_many({"idempotency_key": {"$regex": "^test_.*"}})
    await notification_announcement_collection.delete_many({"title": {"$regex": "^TEST_.*"}})
    await user_collection.delete_many({"email": pattern})
    yield
    await notification_collection.delete_many({"recipient_user_id": pattern})
    await notification_preference_collection.delete_many({"user_email": pattern})
    await notification_device_collection.delete_many({"user_email": pattern})
    await notification_delivery_collection.delete_many({"recipient_user_id": pattern})
    await notification_job_collection.delete_many({"idempotency_key": {"$regex": "^test_.*"}})
    await notification_announcement_collection.delete_many({"title": {"$regex": "^TEST_.*"}})
    await user_collection.delete_many({"email": pattern})

@pytest_asyncio.fixture
async def notif_test_context():
    u_id = uuid.uuid4().hex[:6]
    user1_email = f"test_notif_user1_{u_id}@eventora.test"
    user2_email = f"test_notif_user2_{u_id}@eventora.test"
    admin_email = f"test_notif_admin_{u_id}@eventora.test"

    # Seed users
    await user_collection.insert_many([
        {"email": user1_email, "name": "User One", "registration_complete": True, "role": "USER"},
        {"email": user2_email, "name": "User Two", "registration_complete": True, "role": "USER"},
    ])
    await admin_collection.insert_one({
        "email": admin_email,
        "name": "Super Admin",
        "role": "super_admin"
    })

    user1_token = create_access_token(data={"sub": user1_email, "type": "user"})
    user2_token = create_access_token(data={"sub": user2_email, "type": "user"})
    admin_token = create_access_token(data={"sub": admin_email, "type": "admin"})

    return {
        "user1_email": user1_email,
        "user2_email": user2_email,
        "admin_email": admin_email,
        "user1_token": user1_token,
        "user2_token": user2_token,
        "admin_token": admin_token,
        "user1_headers": {"Authorization": f"Bearer {user1_token}"},
        "user2_headers": {"Authorization": f"Bearer {user2_token}"},
        "admin_headers": {"Authorization": f"Bearer {admin_token}"},
    }

# ────────────────────────────────────────────────────────────
# 1. Authorization & Ownership Tests
# ────────────────────────────────────────────────────────────

async def test_unauthenticated_requests_rejected():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.get("/api/notifications")
        assert res.status_code == 401

async def test_user_ownership_isolation(notif_test_context):
    ctx = notif_test_context
    # Create notification for User 1
    doc = await NotificationService.create_notification(NotificationCreate(
        recipient_user_id=ctx["user1_email"],
        notification_type=NotificationType.REGISTRATION_CONFIRMATION,
        title="User 1 Notification",
        message="Private message for user 1"
    ))
    notif_id = doc["id"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # User 1 can view it
        res1 = await ac.get(f"/api/notifications/{notif_id}", headers=ctx["user1_headers"])
        assert res1.status_code == 200
        assert res1.json()["id"] == notif_id

        # User 2 CANNOT view User 1's notification
        res2 = await ac.get(f"/api/notifications/{notif_id}", headers=ctx["user2_headers"])
        assert res2.status_code == 404

        # User 2 CANNOT mark User 1's notification as read
        res3 = await ac.patch(f"/api/notifications/{notif_id}/read", headers=ctx["user2_headers"])
        assert res3.status_code == 404

        # User 2 CANNOT delete User 1's notification
        res4 = await ac.delete(f"/api/notifications/{notif_id}", headers=ctx["user2_headers"])
        assert res4.status_code == 404

async def test_admin_announcements_require_admin_auth(notif_test_context):
    ctx = notif_test_context
    payload = {
        "title": "TEST_Global Update",
        "message": "All users notice",
        "audience_type": "ALL",
        "priority": "URGENT"
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Normal user cannot create announcement
        res_user = await ac.post("/api/admin/notifications/announcements", json=payload, headers=ctx["user1_headers"])
        assert res_user.status_code in [401, 403]

        # Admin can create announcement
        res_admin = await ac.post("/api/admin/notifications/announcements", json=payload, headers=ctx["admin_headers"])
        assert res_admin.status_code == 200
        assert res_admin.json()["title"] == "TEST_Global Update"

# ────────────────────────────────────────────────────────────
# 2. Persistence, Unread Count & Mark-As-Read Tests
# ────────────────────────────────────────────────────────────

async def test_notification_persistence_and_unread_count(notif_test_context):
    ctx = notif_test_context
    email = ctx["user1_email"]

    # Create 3 notifications
    for i in range(3):
        await NotificationService.create_notification(NotificationCreate(
            recipient_user_id=email,
            notification_type=NotificationType.ORDER_UPDATE,
            title=f"Order Update {i+1}",
            message=f"Order status updated {i+1}"
        ))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Check unread count
        count_res = await ac.get("/api/notifications/unread-count", headers=ctx["user1_headers"])
        assert count_res.status_code == 200
        assert count_res.json()["unread_count"] == 3

        # List notifications
        list_res = await ac.get("/api/notifications?page=1&page_size=10", headers=ctx["user1_headers"])
        assert list_res.status_code == 200
        body = list_res.json()
        assert body["total"] == 3
        assert len(body["items"]) == 3
        first_id = body["items"][0]["id"]

        # Mark first as read
        mark_res = await ac.patch(f"/api/notifications/{first_id}/read", headers=ctx["user1_headers"])
        assert mark_res.status_code == 200
        assert mark_res.json()["success"] is True

        # Idempotent mark as read
        mark_res2 = await ac.patch(f"/api/notifications/{first_id}/read", headers=ctx["user1_headers"])
        assert mark_res2.status_code == 200

        # Unread count should now be 2
        count_res2 = await ac.get("/api/notifications/unread-count", headers=ctx["user1_headers"])
        assert count_res2.json()["unread_count"] == 2

        # Mark all as read
        all_res = await ac.patch("/api/notifications/read-all", headers=ctx["user1_headers"])
        assert all_res.status_code == 200

        count_res3 = await ac.get("/api/notifications/unread-count", headers=ctx["user1_headers"])
        assert count_res3.json()["unread_count"] == 0

# ────────────────────────────────────────────────────────────
# 3. All 12 Notification Types & Idempotency Tests
# ────────────────────────────────────────────────────────────

async def test_all_twelve_notification_types_defined_and_persisted(notif_test_context):
    ctx = notif_test_context
    email = ctx["user1_email"]

    all_types = list(NotificationType)
    assert len(all_types) == 12

    for n_type in all_types:
        doc = await NotificationService.create_notification(NotificationCreate(
            recipient_user_id=email,
            notification_type=n_type,
            title=f"Test {n_type.value}",
            message=f"Message for {n_type.value}",
            deduplication_key=f"test_{n_type.value}_{uuid.uuid4().hex[:6]}"
        ))
        assert doc is not None
        assert doc["notification_type"] == n_type.value
        assert doc["recipient_user_id"] == email

async def test_deduplication_suppresses_duplicate_notifications(notif_test_context):
    ctx = notif_test_context
    email = ctx["user1_email"]
    dedup_key = f"test_dedup_{uuid.uuid4().hex}"

    # First creation
    doc1 = await NotificationService.create_notification(NotificationCreate(
        recipient_user_id=email,
        notification_type=NotificationType.PAYMENT_CONFIRMATION,
        title="Payment Done",
        message="First attempt",
        deduplication_key=dedup_key
    ))

    # Second creation with SAME deduplication key
    doc2 = await NotificationService.create_notification(NotificationCreate(
        recipient_user_id=email,
        notification_type=NotificationType.PAYMENT_CONFIRMATION,
        title="Payment Done Duplicate",
        message="Second attempt",
        deduplication_key=dedup_key
    ))

    # Must return the same existing record without creating a second one
    assert str(doc1["_id"]) == str(doc2["_id"])
    total_count = await notification_collection.count_documents({
        "recipient_user_id": email,
        "deduplication_key": dedup_key
    })
    assert total_count == 1

# ────────────────────────────────────────────────────────────
# 4. User Notification Preferences Tests
# ────────────────────────────────────────────────────────────

async def test_user_preferences_lifecycle(notif_test_context):
    ctx = notif_test_context

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Get defaults
        get_res = await ac.get("/api/notifications/preferences", headers=ctx["user1_headers"])
        assert get_res.status_code == 200
        prefs = get_res.json()
        assert prefs["email_enabled"] is True
        assert prefs["push_enabled"] is True
        assert prefs["sound_enabled"] is True

        # Update preferences (disable push and voting reminders)
        update_payload = {
            "push_enabled": False,
            "voting_reminders": False,
            "sound_enabled": False
        }
        patch_res = await ac.patch("/api/notifications/preferences", json=update_payload, headers=ctx["user1_headers"])
        assert patch_res.status_code == 200
        updated = patch_res.json()
        assert updated["push_enabled"] is False
        assert updated["voting_reminders"] is False
        assert updated["sound_enabled"] is False
        assert updated["email_enabled"] is True  # preserved

        # Ensure User 2 preferences are isolated and unchanged
        u2_res = await ac.get("/api/notifications/preferences", headers=ctx["user2_headers"])
        assert u2_res.json()["push_enabled"] is True

# ────────────────────────────────────────────────────────────
# 5. Device Push Registration Tests
# ────────────────────────────────────────────────────────────

async def test_device_registration_and_unregistration(notif_test_context):
    ctx = notif_test_context

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Register Android FCM device
        reg_payload = {
            "provider_token": f"fcm_test_token_{uuid.uuid4().hex[:12]}",
            "platform": "ANDROID",
            "device_label": "Pixel 8 Pro"
        }
        res = await ac.post("/api/notifications/devices", json=reg_payload, headers=ctx["user1_headers"])
        assert res.status_code == 200
        dev_data = res.json()
        assert dev_data["platform"] == "ANDROID"
        assert dev_data["device_label"] == "Pixel 8 Pro"
        device_id = dev_data["id"]

        # List devices
        list_res = await ac.get("/api/notifications/devices", headers=ctx["user1_headers"])
        assert list_res.status_code == 200
        assert list_res.json()["total"] == 1

        # Unregister device
        unreg_res = await ac.delete(f"/api/notifications/devices/{device_id}", headers=ctx["user1_headers"])
        assert unreg_res.status_code == 200
        assert unreg_res.json()["success"] is True

        # Device is now inactive
        list_res2 = await ac.get("/api/notifications/devices", headers=ctx["user1_headers"])
        assert list_res2.json()["total"] == 0

# ────────────────────────────────────────────────────────────
# 6. Admin Announcements & Audience Targeting Tests
# ────────────────────────────────────────────────────────────

async def test_admin_announcement_workflow(notif_test_context):
    ctx = notif_test_context

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Create Draft Announcement
        create_payload = {
            "title": "TEST_Scheduled Maintenance",
            "message": "Platform will undergo maintenance at midnight.",
            "audience_type": "EXPLICIT_USERS",
            "explicit_user_emails": [ctx["user1_email"]],
            "priority": "HIGH"
        }
        create_res = await ac.post("/api/admin/notifications/announcements", json=create_payload, headers=ctx["admin_headers"])
        assert create_res.status_code == 200
        ann = create_res.json()
        assert ann["status"] == "DRAFT"
        ann_id = ann["id"]

        # User 1 should NOT have received it while it is DRAFT
        u1_res = await ac.get("/api/notifications", headers=ctx["user1_headers"])
        assert u1_res.json()["total"] == 0

        # 2. Publish Announcement
        pub_res = await ac.post(f"/api/admin/notifications/announcements/{ann_id}/publish", headers=ctx["admin_headers"])
        assert pub_res.status_code == 200
        published = pub_res.json()
        assert published["status"] == "PUBLISHED"
        assert published["recipients_count"] == 1

        # User 1 should NOW have the announcement
        u1_res2 = await ac.get("/api/notifications", headers=ctx["user1_headers"])
        assert u1_res2.json()["total"] == 1
        assert u1_res2.json()["items"][0]["title"] == "TEST_Scheduled Maintenance"

        # User 2 was NOT in explicit recipients, so should have 0 notifications
        u2_res = await ac.get("/api/notifications", headers=ctx["user2_headers"])
        assert u2_res.json()["total"] == 0

async def test_scheduled_announcement_cancellation(notif_test_context):
    ctx = notif_test_context
    future_time = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        create_payload = {
            "title": "TEST_Future Announcement",
            "message": "Future event details",
            "audience_type": "ALL",
            "scheduled_at": future_time
        }
        res = await ac.post("/api/admin/notifications/announcements", json=create_payload, headers=ctx["admin_headers"])
        assert res.status_code == 200
        ann_id = res.json()["id"]
        assert res.json()["status"] == "SCHEDULED"

        # Cancel announcement
        cancel_res = await ac.post(f"/api/admin/notifications/announcements/{ann_id}/cancel", headers=ctx["admin_headers"])
        assert cancel_res.status_code == 200
        assert cancel_res.json()["success"] is True

        # Check status is CANCELLED
        get_res = await ac.get(f"/api/admin/notifications/announcements/{ann_id}", headers=ctx["admin_headers"])
        assert get_res.json()["status"] == "CANCELLED"

# ────────────────────────────────────────────────────────────
# 7. Scheduler & Durable Reminders Tests
# ────────────────────────────────────────────────────────────

async def test_event_reminder_scheduling_and_cancellation():
    event_id = str(ObjectId())
    now = datetime.now(timezone.utc)
    start_time = now + timedelta(days=3)

    # Schedule reminders
    await NotificationSchedulerService.schedule_event_reminders(
        event_id=event_id,
        event_title="Grand Gala",
        start_time=start_time,
        offsets_hours=[24, 1]
    )

    # Check jobs in database
    jobs = await notification_job_collection.find({"payload.event_id": event_id}).to_list(10)
    assert len(jobs) == 2
    assert all(j["status"] == "PENDING" for j in jobs)

    # Reschedule event: invalidate old reminders
    await NotificationSchedulerService.invalidate_event_reminders(event_id=event_id, is_cancelled=False)
    cancelled_jobs = await notification_job_collection.find({"payload.event_id": event_id, "status": "CANCELLED"}).to_list(10)
    assert len(cancelled_jobs) == 2

# ────────────────────────────────────────────────────────────
# 8. Admin Operational Analytics Tests
# ────────────────────────────────────────────────────────────

async def test_admin_notification_analytics(notif_test_context):
    ctx = notif_test_context
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.get("/api/admin/notifications/analytics", headers=ctx["admin_headers"])
        assert res.status_code == 200
        analytics = res.json()
        assert "total_notifications" in analytics
        assert "unread_notifications" in analytics
        assert "notifications_by_type" in analytics
        assert "deliveries_by_channel" in analytics
        assert "active_devices_count" in analytics
