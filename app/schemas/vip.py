from pydantic import BaseModel, Field, EmailStr
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from enum import Enum

class VIPPassCategory(str, Enum):
    VIP_GUEST = "VIP Guest"
    PREMIUM_VIP = "Premium VIP"
    VIP_SPEAKER = "VIP Speaker"
    VIP_SPONSOR = "VIP Sponsor"
    BACKSTAGE_VIP = "Backstage VIP"
    CUSTOM = "Custom VIP"

class VIPAccessArea(str, Enum):
    MAIN_GATE = "MAIN_GATE"
    VIP_LOUNGE = "VIP_LOUNGE"
    BACKSTAGE = "BACKSTAGE"
    SPEAKER_LOUNGE = "SPEAKER_LOUNGE"
    SPONSOR_AREA = "SPONSOR_AREA"
    NETWORKING_AREA = "NETWORKING_AREA"
    EXCLUSIVE_ZONE = "EXCLUSIVE_ZONE"

class VIPEntryPolicy(str, Enum):
    SINGLE_ENTRY = "SINGLE_ENTRY"
    ALLOW_REENTRY = "ALLOW_REENTRY"

class VIPBookingStatus(str, Enum):
    PENDING_PAYMENT = "PENDING_PAYMENT"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    REFUND_PENDING = "REFUND_PENDING"
    REFUNDED = "REFUNDED"

class VIPPassStatus(str, Enum):
    VALID = "VALID"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"

class VIPVerificationOutcome(str, Enum):
    ENTRY_APPROVED = "ENTRY_APPROVED"
    INVALID_PASS = "INVALID_PASS"
    PASS_REVOKED = "PASS_REVOKED"
    PASS_EXPIRED = "PASS_EXPIRED"
    NOT_YET_VALID = "NOT_YET_VALID"
    EVENT_MISMATCH = "EVENT_MISMATCH"
    ACCESS_DENIED = "ACCESS_DENIED"
    DUPLICATE_ENTRY = "DUPLICATE_ENTRY"
    REENTRY_NOT_ALLOWED = "REENTRY_NOT_ALLOWED"
    EVENT_NOT_AVAILABLE = "EVENT_NOT_AVAILABLE"
    VERIFICATION_UNAVAILABLE = "VERIFICATION_UNAVAILABLE"


# ──────────────────────────────────────────────────────────
# Configuration Models
# ──────────────────────────────────────────────────────────

class VIPCapacityStats(BaseModel):
    total_capacity: int
    booked_count: int
    confirmed_count: int
    pending_payment_count: int
    available_count: int
    sold_out: bool
    booking_open: bool

class VIPConfigurationCreateReq(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    category: VIPPassCategory = VIPPassCategory.PREMIUM_VIP
    description: Optional[str] = ""
    # CRITICAL: Every VIP Pass must be PAID (> 0). Free VIP pass configs are prohibited.
    price: float = Field(..., gt=0.0, description="Price must be strictly positive (> 0). Free VIP passes are strictly prohibited.")
    currency: str = Field(default="INR", min_length=3, max_length=5)
    capacity: int = Field(..., ge=1, description="Maximum total VIP pass capacity")
    booking_start: datetime
    booking_end: datetime
    validity_start: datetime
    validity_end: datetime
    max_passes_per_user: int = Field(default=1, ge=1, le=10)
    access_areas: List[str] = Field(default_factory=lambda: ["MAIN_GATE", "VIP_LOUNGE"])
    entry_policy: VIPEntryPolicy = VIPEntryPolicy.SINGLE_ENTRY
    reentry_cooldown_minutes: Optional[int] = Field(default=10, ge=0)
    max_entries: Optional[int] = Field(default=1, ge=1)
    reservation_expiry_minutes: int = Field(default=15, ge=5, le=120)
    is_enabled: bool = False
    cancellation_policy: Optional[str] = "Non-refundable once confirmed unless event is cancelled."

class VIPConfigurationUpdateReq(BaseModel):
    name: Optional[str] = None
    category: Optional[VIPPassCategory] = None
    description: Optional[str] = None
    price: Optional[float] = Field(default=None, gt=0.0)
    currency: Optional[str] = None
    capacity: Optional[int] = Field(default=None, ge=1)
    booking_start: Optional[datetime] = None
    booking_end: Optional[datetime] = None
    validity_start: Optional[datetime] = None
    validity_end: Optional[datetime] = None
    max_passes_per_user: Optional[int] = None
    access_areas: Optional[List[str]] = None
    entry_policy: Optional[VIPEntryPolicy] = None
    reentry_cooldown_minutes: Optional[int] = None
    max_entries: Optional[int] = None
    reservation_expiry_minutes: Optional[int] = None
    is_enabled: Optional[bool] = None
    cancellation_policy: Optional[str] = None

class VIPConfigurationResponse(BaseModel):
    id: str
    event_id: str
    event_title: Optional[str] = None
    name: str
    category: str
    description: str
    price: float
    currency: str
    capacity: VIPCapacityStats
    booking_start: datetime
    booking_end: datetime
    validity_start: datetime
    validity_end: datetime
    max_passes_per_user: int
    access_areas: List[str]
    entry_policy: str
    reentry_cooldown_minutes: int
    max_entries: Optional[int]
    reservation_expiry_minutes: int
    is_enabled: bool
    cancellation_policy: str
    created_at: datetime
    updated_at: datetime
    created_by: str


# ──────────────────────────────────────────────────────────
# Booking Models
# ──────────────────────────────────────────────────────────

class VIPBookingCreateReq(BaseModel):
    quantity: int = Field(default=1, ge=1, le=10)
    holder_names: Optional[List[str]] = None
    idempotency_key: Optional[str] = None

class VIPBookingResponse(BaseModel):
    booking_id: str
    booking_reference: str
    event_id: str
    event_title: Optional[str] = None
    configuration_id: str
    pass_name: str
    quantity: int
    unit_price: float
    total_amount: float
    currency: str
    booking_status: str
    payment_status: str
    user_email: str
    expires_at: Optional[datetime] = None
    booked_at: datetime
    payment_details: Optional[Dict[str, Any]] = None
    issued_pass_ids: List[str] = []
    capacity: VIPCapacityStats


# ──────────────────────────────────────────────────────────
# Pass & Verification Models
# ──────────────────────────────────────────────────────────

class VIPPassResponse(BaseModel):
    pass_id: str
    pass_reference: str
    booking_id: Optional[str] = None
    booking_reference: Optional[str] = None
    event_id: str
    event_title: Optional[str] = None
    configuration_id: str
    category: str
    pass_name: str
    user_email: str
    holder_name: str
    status: str
    issued_at: datetime
    validity_start: datetime
    validity_end: datetime
    access_areas: List[str]
    entry_policy: str
    entries_count: int
    max_entries: Optional[int]
    last_entry_at: Optional[datetime] = None
    qr_token: Optional[str] = None
    qr_image_base64: Optional[str] = None
    revocation_reason: Optional[str] = None
    revoked_at: Optional[datetime] = None

class VIPVerifyReq(BaseModel):
    verification_token: str
    event_id: str
    access_area_id: str = "MAIN_GATE"
    gate_id: str = "GATE_01"
    idempotency_key: Optional[str] = None

class VIPVerifyResponse(BaseModel):
    outcome: str
    allowed: bool
    message: str
    pass_id: Optional[str] = None
    pass_reference: Optional[str] = None
    holder_name: Optional[str] = None
    category: Optional[str] = None
    access_area_id: str
    gate_id: str
    timestamp: datetime
    entry_count: Optional[int] = 0

class VIPRevokeReq(BaseModel):
    reason: str = Field(..., min_length=3, max_length=500)
    release_capacity: bool = False

class VIPManualIssueReq(BaseModel):
    user_email: EmailStr
    holder_name: str
    payment_reference: str = Field(..., min_length=3, description="Authoritative payment transaction reference or approved financial audit reference.")
    amount_paid: float = Field(..., gt=0.0, description="Verified payment amount. Free passes are strictly prohibited.")
    currency: str = "INR"
    access_areas: Optional[List[str]] = None
