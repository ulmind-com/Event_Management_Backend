from pydantic import BaseModel, Field, EmailStr
from typing import Optional, List, Dict, Any
from datetime import datetime
from app.core.notification_types import (
    NotificationType,
    NotificationPriority,
    NotificationChannel,
    DeliveryStatus,
    SoundCategory,
    AudienceType,
    DevicePlatform,
)

# ── Notification Models ──

class NotificationCreate(BaseModel):
    recipient_user_id: str = Field(..., description="Recipient user email")
    notification_type: NotificationType
    title: str = Field(..., max_length=200)
    message: str = Field(..., max_length=2000)
    resource_type: Optional[str] = Field(None, max_length=100)
    resource_id: Optional[str] = Field(None, max_length=100)
    action_url: Optional[str] = Field(None, max_length=500, description="Deep link or URL path")
    priority: NotificationPriority = NotificationPriority.NORMAL
    sound_category: SoundCategory = SoundCategory.DEFAULT
    scheduled_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    source_module: Optional[str] = Field(None, max_length=100)
    source_event_id: Optional[str] = Field(None, max_length=100)
    deduplication_key: Optional[str] = Field(None, max_length=256)
    channels: Optional[List[NotificationChannel]] = None
    metadata: Optional[Dict[str, Any]] = None

class NotificationResponse(BaseModel):
    id: str
    recipient_user_id: str
    notification_type: NotificationType
    title: str
    message: str
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    action_url: Optional[str] = None
    priority: NotificationPriority
    sound_category: SoundCategory
    is_read: bool = False
    read_at: Optional[datetime] = None
    created_at: datetime
    scheduled_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    source_module: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

class NotificationListResponse(BaseModel):
    items: List[NotificationResponse]
    total: int
    unread_count: int
    page: int
    page_size: int
    has_next: bool

class UnreadCountResponse(BaseModel):
    unread_count: int

class MarkReadResponse(BaseModel):
    success: bool
    modified_count: int
    message: str

# ── User Preferences Models ──

class NotificationPreferencesUpdate(BaseModel):
    in_app_enabled: Optional[bool] = None
    email_enabled: Optional[bool] = None
    push_enabled: Optional[bool] = None
    web_push_enabled: Optional[bool] = None
    sound_enabled: Optional[bool] = None
    event_reminders: Optional[bool] = None
    voting_reminders: Optional[bool] = None
    leaderboard_updates: Optional[bool] = None
    competition_results: Optional[bool] = None
    course_updates: Optional[bool] = None
    order_updates: Optional[bool] = None
    reward_updates: Optional[bool] = None
    announcements: Optional[bool] = None

class NotificationPreferencesResponse(BaseModel):
    user_email: str
    in_app_enabled: bool = True
    email_enabled: bool = True
    push_enabled: bool = True
    web_push_enabled: bool = True
    sound_enabled: bool = True
    event_reminders: bool = True
    voting_reminders: bool = True
    leaderboard_updates: bool = True
    competition_results: bool = True
    course_updates: bool = True
    order_updates: bool = True
    reward_updates: bool = True
    announcements: bool = True
    updated_at: Optional[datetime] = None

# ── Device Push Models ──

class DeviceRegisterRequest(BaseModel):
    provider_token: str = Field(..., min_length=10, max_length=1024, description="FCM, APNs, or Web Push subscription token")
    platform: DevicePlatform
    app_identifier: Optional[str] = Field("com.eventora.app", max_length=100)
    device_label: Optional[str] = Field(None, max_length=100, description="User friendly device name e.g. Pixel 8")

class DeviceResponse(BaseModel):
    id: str
    user_email: str
    platform: DevicePlatform
    app_identifier: Optional[str] = None
    device_label: Optional[str] = None
    is_active: bool
    created_at: datetime
    last_used_at: Optional[datetime] = None

class DeviceListResponse(BaseModel):
    devices: List[DeviceResponse]
    total: int

# ── Announcement Models (Admin) ──

class AnnouncementCreate(BaseModel):
    title: str = Field(..., min_length=3, max_length=200)
    message: str = Field(..., min_length=5, max_length=2000)
    audience_type: AudienceType = AudienceType.ALL
    audience_target_id: Optional[str] = Field(None, description="Event ID, Competition ID, or Course ID if targeted")
    explicit_user_emails: Optional[List[str]] = None
    priority: NotificationPriority = NotificationPriority.URGENT
    action_url: Optional[str] = Field(None, max_length=500)
    channels: Optional[List[NotificationChannel]] = None
    scheduled_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    sound_category: SoundCategory = SoundCategory.ANNOUNCEMENT
    metadata: Optional[Dict[str, Any]] = None

class AnnouncementUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=3, max_length=200)
    message: Optional[str] = Field(None, min_length=5, max_length=2000)
    audience_type: Optional[AudienceType] = None
    audience_target_id: Optional[str] = None
    explicit_user_emails: Optional[List[str]] = None

    priority: Optional[NotificationPriority] = None
    action_url: Optional[str] = None
    channels: Optional[List[NotificationChannel]] = None
    scheduled_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    sound_category: Optional[SoundCategory] = None
    metadata: Optional[Dict[str, Any]] = None

class AnnouncementResponse(BaseModel):
    id: str
    title: str
    message: str
    audience_type: AudienceType
    audience_target_id: Optional[str] = None
    explicit_user_emails: Optional[List[str]] = None
    priority: NotificationPriority
    action_url: Optional[str] = None
    status: str  # DRAFT, SCHEDULED, PUBLISHED, CANCELLED
    created_by: str
    created_at: datetime
    scheduled_at: Optional[datetime] = None
    published_at: Optional[datetime] = None
    recipients_count: int = 0
    delivery_summary: Optional[Dict[str, Any]] = None

class AnnouncementListResponse(BaseModel):
    items: List[AnnouncementResponse]
    total: int
    page: int
    page_size: int

# ── Delivery Status & Analytics ──

class DeliveryRecordResponse(BaseModel):
    id: str
    notification_id: str
    channel: NotificationChannel
    status: DeliveryStatus
    attempt_count: int
    next_retry_at: Optional[datetime] = None
    error_message: Optional[str] = None
    provider_message_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime

class NotificationAnalyticsResponse(BaseModel):
    total_notifications: int
    unread_notifications: int
    notifications_by_type: Dict[str, int]
    deliveries_by_channel: Dict[str, int]
    deliveries_by_status: Dict[str, int]
    active_devices_count: int
    pending_scheduled_jobs: int
    failed_deliveries_count: int

# ── WebSocket Contract ──

class WebSocketEventPayload(BaseModel):
    event: str  # notification.created, notification.unread_count, ping, pong
    data: Dict[str, Any]
