from fastapi import APIRouter
from app.api.routes import (
    auth, profile, activities, registrations, bookings, merchandise, rewards,
    admin_auth, admin_cms, admin_checkin, admin_competitions, public, payments,
    admin_courses, admin_workshops, admin_users, admin_referrals, admin_certificates,
    admin_badges, public_certificates, user_certificates,
    advanced_competitions, judge_competitions, admin_judges, ws_live,
    public_competitions, judge_auth, admin_volunteers, volunteer_auth,
    admin_vip, user_vip, vip_webhooks,
    admin_merchandise, user_merchandise, merchandise_webhooks,
    notifications, admin_notifications, notification_ws,
    admin_kyc, user_kyc
)

api_router = APIRouter()

# ── User APIs ──
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(profile.router, prefix="/profile", tags=["profile"])
api_router.include_router(activities.router, prefix="/activities", tags=["activities"])
api_router.include_router(registrations.router, prefix="/registrations", tags=["registrations"])
api_router.include_router(bookings.router, prefix="/bookings", tags=["bookings"])
api_router.include_router(payments.router, prefix="/payments", tags=["payments"])
api_router.include_router(user_merchandise.router, prefix="/merchandise", tags=["merchandise"])
api_router.include_router(admin_merchandise.router, prefix="/admin/merchandise", tags=["admin-merchandise"])
api_router.include_router(merchandise_webhooks.router, prefix="/merchandise", tags=["merchandise-webhooks"])
api_router.include_router(rewards.router, prefix="/rewards", tags=["rewards"])
api_router.include_router(user_certificates.router, prefix="/users/me", tags=["user-certificates"])
api_router.include_router(notifications.router, prefix="/notifications", tags=["notifications"])
api_router.include_router(notification_ws.router, prefix="/notifications", tags=["notification-ws"])
api_router.include_router(user_kyc.router, prefix="/kyc", tags=["user-kyc"])
api_router.include_router(user_kyc.router, prefix="/v1/kyc", tags=["user-kyc"])
api_router.include_router(advanced_competitions.router, tags=["advanced-competitions"])

# ── Judge & Volunteer APIs ──
api_router.include_router(judge_auth.router, prefix="/judge/auth", tags=["judge-auth"])
api_router.include_router(judge_competitions.router, prefix="/judge", tags=["judge-competitions"])
api_router.include_router(volunteer_auth.router, prefix="/volunteer/auth", tags=["volunteer-auth"])

# ── Admin APIs ──
api_router.include_router(admin_auth.router, prefix="/admin", tags=["admin-auth"])
api_router.include_router(admin_cms.router, prefix="/admin", tags=["admin-cms"])
api_router.include_router(admin_courses.router, prefix="/admin/courses", tags=["admin-courses"])
api_router.include_router(admin_workshops.router, prefix="/admin/workshops", tags=["admin-workshops"])
api_router.include_router(admin_users.router, prefix="/admin/users", tags=["admin-users"])
api_router.include_router(admin_referrals.router, prefix="/admin", tags=["admin-referrals"])
api_router.include_router(admin_certificates.router, prefix="/admin", tags=["admin-certificates"])
api_router.include_router(admin_badges.router, prefix="/admin/badges", tags=["admin-badges"])
api_router.include_router(admin_checkin.router, prefix="/admin/tickets", tags=["admin-checkin"])
api_router.include_router(admin_competitions.router, prefix="/admin/advanced-competitions", tags=["admin-advanced-competitions"])
api_router.include_router(admin_judges.router, prefix="/admin/judges", tags=["admin-judges"])
api_router.include_router(admin_volunteers.router, prefix="/admin/volunteers", tags=["admin-volunteers"])
api_router.include_router(admin_notifications.router, prefix="/admin/notifications", tags=["admin-notifications"])
api_router.include_router(admin_kyc.router, prefix="/admin/kyc", tags=["admin-kyc"])


# ── Public APIs ──
api_router.include_router(public.router, prefix="/v1", tags=["public"])
api_router.include_router(public_competitions.router, prefix="/v1", tags=["public-competitions"])
api_router.include_router(public_competitions.router, prefix="/public", tags=["public-competitions"])
api_router.include_router(public_certificates.router, prefix="/public/certificates", tags=["public-certificates"])

# ── WebSocket (Real-time) ──
api_router.include_router(ws_live.router, tags=["websocket-live"])

# ── VIP Pass APIs ──
api_router.include_router(user_vip.router, tags=["vip-passes"])
api_router.include_router(vip_webhooks.router, tags=["vip-webhooks"])
api_router.include_router(admin_vip.router, prefix="/admin", tags=["admin-vip"])
