from fastapi import APIRouter
from app.api.routes import (
    auth, profile, activities, registrations, bookings, merchandise, rewards,
    admin_auth, admin_cms, admin_checkin, admin_competitions, public, payments,
    advanced_competitions, judge_competitions, admin_judges, judge_auth
)

api_router = APIRouter()

# ── User APIs ──
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(profile.router, prefix="/profile", tags=["profile"])
api_router.include_router(activities.router, prefix="/activities", tags=["activities"])
api_router.include_router(registrations.router, prefix="/registrations", tags=["registrations"])
api_router.include_router(bookings.router, prefix="/bookings", tags=["bookings"])
api_router.include_router(payments.router, prefix="/payments", tags=["payments"])
api_router.include_router(merchandise.router, prefix="/merchandise", tags=["merchandise"])
api_router.include_router(rewards.router, prefix="/rewards", tags=["rewards"])
api_router.include_router(advanced_competitions.router, tags=["advanced-competitions"])

# ── Judge APIs ──
api_router.include_router(judge_auth.router, prefix="/judge", tags=["judge-auth"])
api_router.include_router(judge_competitions.router, prefix="/judge", tags=["judge-competitions"])

# ── Admin APIs ──
api_router.include_router(admin_auth.router, prefix="/admin", tags=["admin-auth"])
api_router.include_router(admin_cms.router, prefix="/admin", tags=["admin-cms"])
api_router.include_router(admin_checkin.router, prefix="/admin/tickets", tags=["admin-checkin"])
api_router.include_router(admin_competitions.router, prefix="/admin/advanced-competitions", tags=["admin-advanced-competitions"])
api_router.include_router(admin_judges.router, prefix="/admin/judges", tags=["admin-judges"])

# ── Public Homepage APIs ──
api_router.include_router(public.router, prefix="/v1", tags=["public"])
