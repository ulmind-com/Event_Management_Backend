from fastapi import APIRouter
from app.api.routes import auth, profile, activities, registrations, bookings, merchandise, rewards, admin_auth, admin_cms, public

api_router = APIRouter()

# ── User APIs ──
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(profile.router, prefix="/profile", tags=["profile"])
api_router.include_router(activities.router, prefix="/activities", tags=["activities"])
api_router.include_router(registrations.router, prefix="/registrations", tags=["registrations"])
api_router.include_router(bookings.router, prefix="/bookings", tags=["bookings"])
api_router.include_router(merchandise.router, prefix="/merchandise", tags=["merchandise"])
api_router.include_router(rewards.router, prefix="/rewards", tags=["rewards"])

# ── Admin APIs ──
api_router.include_router(admin_auth.router, prefix="/admin", tags=["admin-auth"])
api_router.include_router(admin_cms.router, prefix="/admin", tags=["admin-cms"])

# ── Public Homepage APIs ──
api_router.include_router(public.router, prefix="/v1", tags=["public"])
