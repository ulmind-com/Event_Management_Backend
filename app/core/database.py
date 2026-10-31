import certifi
from motor.motor_asyncio import AsyncIOMotorClient
from app.core.config import settings

client = AsyncIOMotorClient(
    settings.MONGODB_URI,
    tls=True,
    tlsCAFile=certifi.where(),
    serverSelectionTimeoutMS=30000,
    connectTimeoutMS=20000,
    socketTimeoutMS=20000,
    maxPoolSize=50,
    minPoolSize=5,
    maxIdleTimeMS=45000,
)
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

issued_ticket_collection = db['issued_tickets']

# Advanced Competition System Collections
competition_rules_collection = db["competition_rules"]
competition_registration_collection = db["competition_registrations"]
competition_submission_collection = db["competition_submissions"]
submission_review_collection = db["submission_reviews"]
judge_score_collection = db["judge_scores"]
audience_vote_collection = db["audience_votes"]
leaderboard_result_collection = db["leaderboard_results"]

judge_assignment_collection = db['judge_assignments']

# ── Live Judging Session & Advanced Features ──
judging_session_collection = db["judging_sessions"]
draft_score_collection = db["draft_scores"]
judge_feedback_collection = db["judge_feedbacks"]
coi_skip_collection = db["coi_skips"]

# Referral, Reward, Wallet & Discount Collections
referral_program_config_collection = db["referral_program_config"]
referral_collection = db["referrals"]
coin_reward_lot_collection = db["coin_reward_lots"]
coin_ledger_collection = db["coin_ledger"]
user_wallet_collection = db["user_wallets"]
coin_reservation_collection = db["coin_reservations"]

# Certificate & Badge Collections
award_certificate_config_collection = db['award_certificate_configs']
certificate_job_collection = db['certificate_generation_jobs']
badge_definition_collection = db['badge_definitions']
badge_policy_collection = db['badge_policies']
user_badge_award_collection = db['user_badge_awards']

# VIP Pass Collections
vip_configuration_collection = db["vip_configurations"]
vip_booking_collection = db["vip_bookings"]
vip_pass_collection = db["vip_passes"]
vip_checkin_collection = db["vip_checkins"]
vip_payment_attempt_collection = db["vip_payment_attempts"]

# Event Merchandise E-Commerce Collections
merchandise_product_collection = db["merchandise_products"]
merchandise_order_collection = db["merchandise_orders"]
merchandise_inventory_movement_collection = db["merchandise_inventory_movements"]
merchandise_payment_attempt_collection = db["merchandise_payment_attempts"]
merchandise_refund_collection = db["merchandise_refunds"]

# ── Unified Notification System Collections ──
notification_collection = db["notifications"]
notification_preference_collection = db["notification_preferences"]
notification_device_collection = db["notification_devices"]
notification_delivery_collection = db["notification_deliveries"]
notification_job_collection = db["notification_jobs"]
notification_announcement_collection = db["notification_announcements"]

# ── Aadhaar KYC Verification Collection ──
kyc_verification_collection = db["kyc_verifications"]


