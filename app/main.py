from app.services.expiry_worker import ExpiryWorker
from app.services.notification_scheduler import NotificationSchedulerWorker
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from app.core.config import settings
from app.api.api import api_router

# Rate limiter
limiter = Limiter(key_func=get_remote_address)

app = FastAPI(
    title=settings.PROJECT_NAME,
    description="Eventora - Event Management Platform API",
    version="2.0.0",
)

# Rate limit error handler
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS Policy (Allowed for ALL Origins)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=".*", # This allows all origins even with credentials
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api")

@app.get("/", tags=["root"])
def root():
    return {"message": "Eventora API is running", "docs": "/docs"}

@app.on_event("startup")
async def startup_event():
    # Initialize notification indexes safely
    from app.core.notification_indexes import init_notification_indexes
    await init_notification_indexes()

    # Start background coin expiry worker (runs periodically, fully idempotent)
    ExpiryWorker.start(interval_seconds=3600)
    # Start background notification scheduler worker
    NotificationSchedulerWorker.start(interval_seconds=settings.NOTIFICATION_SCHEDULER_INTERVAL_SECONDS)


@app.on_event("shutdown")
async def shutdown_event():
    await ExpiryWorker.stop()
    await NotificationSchedulerWorker.stop()

