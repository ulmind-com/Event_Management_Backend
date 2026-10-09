from motor.motor_asyncio import AsyncIOMotorClient
from app.core.config import settings

client = AsyncIOMotorClient(settings.MONGODB_URI)
db = client.eventora_db

# ── Existing Collections ──
user_collection = db["users"]
otp_collection = db["otps"]

# ── Activity Collections ──
event_collection = db["events"]
ticket_collection = db["tickets"]
competition_collection = db["competitions"]
course_collection = db["courses"]
merchandise_collection = db["merchandise"]

# ── User Action Collections ──
registration_collection = db["registrations"]
booking_collection = db["bookings"]
order_collection = db["orders"]

# ── Gamification Collections ──
certificate_collection = db["certificates"]
reward_collection = db["rewards"]

# ── CMS / Admin Collections ──
admin_collection = db["admin_accounts"]
website_settings_collection = db["website_settings"]
banner_collection = db["banners"]
testimonial_collection = db["testimonials"]
live_event_collection = db["live_events"]
contest_promotion_collection = db["contest_promotions"]
app_promotion_collection = db["app_promotions"]
important_link_collection = db["important_links"]
audit_log_collection = db["audit_logs"]
