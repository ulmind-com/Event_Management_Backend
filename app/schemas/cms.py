from pydantic import BaseModel, HttpUrl, field_validator
from typing import Optional, List
from datetime import datetime

def check_no_fake_cloudinary(v: Optional[str]) -> Optional[str]:
    if v and isinstance(v, str):
        v_low = v.lower()
        if "res.cloudinary.com/demo" in v_low or "cloudinary.com/demo" in v_low:
            raise ValueError("Fake demo Cloudinary URLs are strictly prohibited.")
    return v

# ── Website Settings (Singleton) ──
class SocialMediaLinks(BaseModel):
    facebook: Optional[str] = None
    instagram: Optional[str] = None
    twitter: Optional[str] = None
    youtube: Optional[str] = None
    linkedin: Optional[str] = None


class VotingPowerSettings(BaseModel):
    voting_power_website: int = 1
    voting_power_mobile: int = 5
    voting_power_paid: int = 10

class WebsiteSettingsUpdate(BaseModel):
    brand_name: Optional[str] = None
    logo_url: Optional[str] = None
    favicon_url: Optional[str] = None
    tagline: Optional[str] = None
    homepage_title: Optional[str] = None
    homepage_description: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    office_address: Optional[str] = None
    support_url: Optional[str] = None
    privacy_policy_url: Optional[str] = None
    terms_url: Optional[str] = None
    social_media: Optional[SocialMediaLinks] = None

    @field_validator("logo_url", "favicon_url", mode="before")
    @classmethod
    def validate_cms_urls(cls, v):
        return check_no_fake_cloudinary(v)

# ── Events ──
class EventCreate(BaseModel):
    title: str
    description: str
    image_url: Optional[str] = None
    date: str
    time: Optional[str] = None
    venue: str
    capacity: int = 100
    price: float = 0.0
    registration_deadline: Optional[str] = None
    event_status: str = "upcoming"  # upcoming, live, completed, cancelled
    is_published: bool = False
    is_visible: bool = True
    registration_enabled: bool = True
    ticket_enabled: bool = False
    display_order: int = 0

    @field_validator("image_url", mode="before")
    @classmethod
    def validate_image_url(cls, v):
        return check_no_fake_cloudinary(v)

class EventUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    image_url: Optional[str] = None
    date: Optional[str] = None
    time: Optional[str] = None
    venue: Optional[str] = None
    capacity: Optional[int] = None
    price: Optional[float] = None
    registration_deadline: Optional[str] = None
    event_status: Optional[str] = None
    is_published: Optional[bool] = None
    is_visible: Optional[bool] = None
    registration_enabled: Optional[bool] = None
    ticket_enabled: Optional[bool] = None
    display_order: Optional[int] = None

    @field_validator("image_url", mode="before")
    @classmethod
    def validate_image_url(cls, v):
        return check_no_fake_cloudinary(v)

# ── Live Events ──
class LiveEventCreate(BaseModel):
    title: str
    description: Optional[str] = None
    stream_url: str  # Private — only admin sees this
    public_url: Optional[str] = None  # Public embed/link
    start_time: str
    end_time: Optional[str] = None
    image_url: Optional[str] = None
    is_published: bool = False
    is_visible: bool = True
    display_order: int = 0

class LiveEventUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    stream_url: Optional[str] = None
    public_url: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    image_url: Optional[str] = None
    is_published: Optional[bool] = None
    is_visible: Optional[bool] = None
    display_order: Optional[int] = None

# ── Banners ──
class BannerCreate(BaseModel):
    title: str
    image_url: str  # desktop image (16:9)
    image_url_desktop: Optional[str] = None
    image_url_mobile: Optional[str] = None  # mobile image (9:16)
    mobile_image_url: Optional[str] = None
    description: Optional[str] = None
    sponsor_name: Optional[str] = None
    target_url: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    display_position: str = "homepage"
    display_order: int = 0
    is_published: bool = False
    is_visible: bool = True

    @field_validator("image_url", mode="before")
    @classmethod
    def validate_image_url(cls, v):
        return check_no_fake_cloudinary(v)

    @field_validator("image_url_desktop", "image_url_mobile", "mobile_image_url", mode="before")
    @classmethod
    def validate_optional_image_urls(cls, v):
        if not v:
            return v
        return check_no_fake_cloudinary(v)

class BannerUpdate(BaseModel):
    title: Optional[str] = None
    image_url: Optional[str] = None
    image_url_desktop: Optional[str] = None
    image_url_mobile: Optional[str] = None
    mobile_image_url: Optional[str] = None
    description: Optional[str] = None
    sponsor_name: Optional[str] = None
    target_url: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    display_position: Optional[str] = None
    display_order: Optional[int] = None
    is_published: Optional[bool] = None
    is_visible: Optional[bool] = None

    @field_validator("image_url", mode="before")
    @classmethod
    def validate_image_url(cls, v):
        if not v:
            return v
        return check_no_fake_cloudinary(v)

    @field_validator("image_url_desktop", "image_url_mobile", "mobile_image_url", mode="before")
    @classmethod
    def validate_optional_image_urls(cls, v):
        if not v:
            return v
        return check_no_fake_cloudinary(v)

# ── Testimonials ──
class TestimonialCreate(BaseModel):
    person_name: str
    role: Optional[str] = None
    profile_image: Optional[str] = None
    message: str
    is_approved: bool = False
    is_published: bool = False
    display_order: int = 0

    @field_validator("profile_image", mode="before")
    @classmethod
    def validate_profile_image(cls, v):
        return check_no_fake_cloudinary(v)

class TestimonialUpdate(BaseModel):
    person_name: Optional[str] = None
    role: Optional[str] = None
    profile_image: Optional[str] = None
    message: Optional[str] = None
    is_approved: Optional[bool] = None
    is_published: Optional[bool] = None
    display_order: Optional[int] = None

    @field_validator("profile_image", mode="before")
    @classmethod
    def validate_profile_image(cls, v):
        return check_no_fake_cloudinary(v)

# ── Courses / Workshops ──
class CourseCreate(BaseModel):
    title: str
    description: str
    thumbnail: Optional[str] = None
    instructor_name: str
    instructor_info: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    price: float = 0.0
    registration_start_at: datetime
    registration_end_at: datetime
    available_seats: int = 50
    course_type: str = "course"  # "course" or "workshop"
    is_published: bool = False
    is_visible: bool = True
    display_order: int = 0

    @field_validator("thumbnail", mode="before")
    @classmethod
    def validate_thumbnail(cls, v):
        return check_no_fake_cloudinary(v)

class CourseUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    thumbnail: Optional[str] = None
    instructor_name: Optional[str] = None
    instructor_info: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    price: Optional[float] = None
    registration_start_at: Optional[datetime] = None
    registration_end_at: Optional[datetime] = None
    available_seats: Optional[int] = None
    course_type: Optional[str] = None
    is_published: Optional[bool] = None
    is_visible: Optional[bool] = None
    display_order: Optional[int] = None

    @field_validator("thumbnail", mode="before")
    @classmethod
    def validate_thumbnail(cls, v):
        return check_no_fake_cloudinary(v)

# ── Tickets ──
class TicketCreate(BaseModel):
    event_id: str
    type: str = "general"  # general, vip, early_bird
    price: float
    total_quantity: int
    available: Optional[int] = None
    sale_start: Optional[str] = None
    sale_end: Optional[str] = None
    is_visible: bool = True
    display_order: int = 0

class TicketUpdate(BaseModel):
    type: Optional[str] = None
    price: Optional[float] = None
    total_quantity: Optional[int] = None
    available: Optional[int] = None
    sale_start: Optional[str] = None
    sale_end: Optional[str] = None
    is_visible: Optional[bool] = None
    display_order: Optional[int] = None

# ── Contest / Voting Promotions ──
class ContestPromotionCreate(BaseModel):
    title: str
    description: Optional[str] = None
    image_url: Optional[str] = None
    competition_id: Optional[str] = None
    voting_start: Optional[str] = None
    voting_end: Optional[str] = None
    cta_text: str = "Vote Now"
    target_url: Optional[str] = None
    is_published: bool = False
    is_visible: bool = True
    display_order: int = 0

    @field_validator("image_url", mode="before")
    @classmethod
    def validate_image_url(cls, v):
        return check_no_fake_cloudinary(v)

class ContestPromotionUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    image_url: Optional[str] = None
    competition_id: Optional[str] = None
    voting_start: Optional[str] = None
    voting_end: Optional[str] = None
    cta_text: Optional[str] = None
    target_url: Optional[str] = None
    is_published: Optional[bool] = None
    is_visible: Optional[bool] = None
    display_order: Optional[int] = None

    @field_validator("image_url", mode="before")
    @classmethod
    def validate_image_url(cls, v):
        return check_no_fake_cloudinary(v)

# ── Mobile App Promotion (Singleton) ──
class AppPromotionUpdate(BaseModel):
    section_title: Optional[str] = None
    description: Optional[str] = None
    promotional_image: Optional[str] = None
    android_url: Optional[str] = None
    ios_url: Optional[str] = None
    cta_text: Optional[str] = None
    is_enabled: Optional[bool] = None
    is_published: Optional[bool] = None

    @field_validator("promotional_image", mode="before")
    @classmethod
    def validate_promotional_image(cls, v):
        return check_no_fake_cloudinary(v)
    display_order: Optional[int] = None

# ── Important Links ──
class ImportantLinkCreate(BaseModel):
    label: str
    url: str
    category: str = "general"  # footer, social, legal, general
    is_enabled: bool = True
    display_order: int = 0

class ImportantLinkUpdate(BaseModel):
    label: Optional[str] = None
    url: Optional[str] = None
    category: Optional[str] = None
    is_enabled: Optional[bool] = None
    display_order: Optional[int] = None

# ── Publish/Visibility Toggle ──
class PublishToggle(BaseModel):
    is_published: bool

class VisibilityToggle(BaseModel):
    is_visible: bool

class ReorderReq(BaseModel):
    display_order: int

class ApprovalToggle(BaseModel):
    is_approved: bool
