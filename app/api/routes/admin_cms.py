from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Query
from pydantic import BaseModel
import re
import urllib.parse
import httpx
from typing import Optional, List, Dict, Any
from bson import ObjectId
from app.core.admin_auth import get_current_admin
from app.services.cloudinary_srv import upload_image
from app.core.database import (
    website_settings_collection, event_collection, live_event_collection,
    ticket_collection, banner_collection, testimonial_collection,
    course_collection, competition_collection, contest_promotion_collection,
    app_promotion_collection, important_link_collection, audit_log_collection,
    booking_collection, issued_ticket_collection, user_collection,
    registration_collection, competition_registration_collection
)
from app.schemas.cms import (
    WebsiteSettingsUpdate, VotingPowerSettings, EventCreate, EventUpdate,
    LiveEventCreate, LiveEventUpdate, BannerCreate, BannerUpdate,
    TestimonialCreate, TestimonialUpdate, CourseCreate, CourseUpdate,
    TicketCreate, TicketUpdate, ContestPromotionCreate, ContestPromotionUpdate,
    AppPromotionUpdate, ImportantLinkCreate, ImportantLinkUpdate,
    PublishToggle, VisibilityToggle, ReorderReq, ApprovalToggle,
    HomepageSectionItem, HomepageSectionsUpdateRequest, CreateCustomSectionRequest, UpdateSectionItemRequest,
)
from app.core.cache import FastCache
from app.schemas.attendance import (
    AttendanceStatus,
    AttendanceUpdateReq,
    BulkAttendanceUpdateReq,
    EventAttendanceDashboardResponse,
)
from app.services.audit import log_admin_action
from app.services.ticket import TicketService
from app.services.share_service import ShareService
from datetime import datetime, timezone

router = APIRouter()

def oid(id_str):
    try:
        return ObjectId(id_str)
    except:
        raise HTTPException(status_code=400, detail="Invalid ID format")

def clean(doc):
    if doc and "_id" in doc:
        doc["_id"] = str(doc["_id"])
    return doc

def clean_list(docs):
    for d in docs:
        d["_id"] = str(d["_id"])
    return docs

# ════════════════════════════════════════
# WEBSITE SETTINGS (singleton)
# ════════════════════════════════════════
@router.get("/website-settings", tags=["admin-settings"])
async def get_website_settings(admin=Depends(get_current_admin)):
    doc = await website_settings_collection.find_one({"_type": "settings"})
    return clean(doc) if doc else {}

@router.put("/website-settings", tags=["admin-settings"])
async def update_website_settings(data: WebsiteSettingsUpdate, admin=Depends(get_current_admin)):
    update = {k: v for k, v in data.model_dump().items() if v is not None}
    if "social_media" in update and update["social_media"]:
        update["social_media"] = {k: v for k, v in update["social_media"].items() if v is not None}
    update["updated_at"] = datetime.now(timezone.utc)
    await website_settings_collection.update_one(
        {"_type": "settings"}, {"$set": update}, upsert=True
    )
    await log_admin_action(admin["email"], "update", "website_settings")
    FastCache.delete("homepage")
    doc = await website_settings_collection.find_one({"_type": "settings"})
    return clean(doc)

DEFAULT_HOMEPAGE_SECTIONS = [
    {
        "id": "sponsor_popup",
        "name": "Grand Platinum Sponsor Pop-up",
        "description": "Website opening grand sponsor modal popup overlay",
        "category": "Monetization & Ads",
        "section_type": "builtin",
        "is_enabled": True,
        "display_order": 1,
        "badge": "Pop-up",
        "config": {},
    },
    {
        "id": "hero_banner",
        "name": "Hero Sponsor Banner Slider",
        "description": "Grand full-screen hero sponsor banners carousel",
        "category": "Promotions & Banners",
        "section_type": "builtin",
        "is_enabled": True,
        "display_order": 2,
        "badge": "Top Slider",
        "config": {},
    },
    {
        "id": "hero",
        "name": "Interactive Hero Showcase",
        "description": "Headline, brand tagline, dynamic events & competitions tabs",
        "category": "Hero & Branding",
        "section_type": "builtin",
        "is_enabled": True,
        "display_order": 3,
        "badge": "Main Hero",
        "config": {},
    },
    {
        "id": "scroll_cue",
        "name": "Scroll Indicator Cue",
        "description": "Visual animated scroll cue leading user down the page",
        "category": "Visual Elements",
        "section_type": "builtin",
        "is_enabled": True,
        "display_order": 4,
        "badge": "Animation",
        "config": {},
    },
    {
        "id": "quad_tiles",
        "name": "Featured 4 Action Tiles",
        "description": "Highlight tiles for Competition, Events, Shop, and Community",
        "category": "Navigation & Highlights",
        "section_type": "builtin",
        "is_enabled": True,
        "display_order": 5,
        "badge": "4 Tiles",
        "config": {},
    },
    {
        "id": "featured_show",
        "name": "Featured Show Stage",
        "description": "High-energy interactive carousel for top live events & championships",
        "category": "Showcase",
        "section_type": "builtin",
        "is_enabled": True,
        "display_order": 6,
        "badge": "Interactive",
        "config": {},
    },
    {
        "id": "marquee",
        "name": "Marquee Running Ticker",
        "description": "Continuous animated glowing text ribbon across screen",
        "category": "Visual Elements",
        "section_type": "builtin",
        "is_enabled": True,
        "display_order": 7,
        "badge": "Ticker",
        "config": {
            "words": ["JOIN THE MOVEMENT", "BOOK YOUR STAGE", "DANCE LOUDER"]
        },
    },
    {
        "id": "gallery",
        "name": "Gallery Reveal Section",
        "description": "Visual performance & showdowns photo gallery with interactive reveal",
        "category": "Media & Visuals",
        "section_type": "builtin",
        "is_enabled": True,
        "display_order": 8,
        "badge": "Gallery",
        "config": {},
    },
    {
        "id": "upcoming_competitions",
        "name": "Upcoming Competitions",
        "description": "National dance championship cards with prize pools and registration",
        "category": "Competitions",
        "section_type": "builtin",
        "is_enabled": True,
        "display_order": 9,
        "badge": "Championships",
        "config": {
            "title": "Upcoming Competitions",
            "label": "What's next",
            "view_all_url": "/competitions"
        },
    },
    {
        "id": "studio_showcase",
        "name": "3D Studio Cylinder Showcase",
        "description": "Scroll-driven 3D cylinder rotating upcoming event cards",
        "category": "3D Experience",
        "section_type": "builtin",
        "is_enabled": True,
        "display_order": 10,
        "badge": "3D Motion",
        "config": {},
    },
    {
        "id": "stats",
        "name": "Live Statistics Counter",
        "description": "Real-time count of events, registered dancers, masterclasses & cities",
        "category": "Proof & Metrics",
        "section_type": "builtin",
        "is_enabled": True,
        "display_order": 11,
        "badge": "Metrics",
        "config": {},
    },
    {
        "id": "live_now",
        "name": "Live Stage Streaming",
        "description": "Active live stage streaming broadcast cards",
        "category": "Live Streams",
        "section_type": "builtin",
        "is_enabled": True,
        "display_order": 12,
        "badge": "Live Stream",
        "config": {
            "title": "Live Stage",
            "label": "Streaming right now"
        },
    },
    {
        "id": "contest_band",
        "name": "Contest Promotion Banner",
        "description": "High-visibility promotional banner for ongoing contests",
        "category": "Promotions & Banners",
        "section_type": "builtin",
        "is_enabled": True,
        "display_order": 13,
        "badge": "Contest",
        "config": {},
    },
    {
        "id": "courses",
        "name": "Masterclasses & Courses",
        "description": "Curated dance academy courses with syllabus and instructors",
        "category": "Learning",
        "section_type": "builtin",
        "is_enabled": True,
        "display_order": 14,
        "badge": "Academy",
        "config": {
            "title": "Masterclasses",
            "label": "Courses",
            "view_all_url": "/courses"
        },
    },
    {
        "id": "workshops",
        "name": "Intensive Dance Workshops",
        "description": "Upcoming live weekend workshops with master choreographers",
        "category": "Learning",
        "section_type": "builtin",
        "is_enabled": True,
        "display_order": 15,
        "badge": "Workshops",
        "config": {
            "title": "Workshops",
            "label": "Workshops",
            "view_all_url": "/workshops"
        },
    },
    {
        "id": "testimonials",
        "name": "Dancer & Artist Testimonials",
        "description": "Verified reviews and community quotes from participants and judges",
        "category": "Proof & Metrics",
        "section_type": "builtin",
        "is_enabled": True,
        "display_order": 16,
        "badge": "Reviews",
        "config": {},
    },
    {
        "id": "stage_moments",
        "name": "Stage Moments (3D Ring)",
        "description": "Scroll-driven 3D circular photo morph showcasing stage highlights",
        "category": "3D Experience",
        "section_type": "builtin",
        "is_enabled": True,
        "display_order": 17,
        "badge": "3D Ring",
        "config": {},
    },
    {
        "id": "spotlight",
        "name": "Spotlight Posts & Social Strip",
        "description": "Curated Instagram and social media highlights strip",
        "category": "Social & Community",
        "section_type": "builtin",
        "is_enabled": True,
        "display_order": 18,
        "badge": "Social",
        "config": {},
    },
    {
        "id": "sponsors",
        "name": "Partners & Sponsors Wall",
        "description": "Official brand partners and sponsor tier logos",
        "category": "Monetization & Ads",
        "section_type": "builtin",
        "is_enabled": True,
        "display_order": 19,
        "badge": "Partners",
        "config": {},
    },
    {
        "id": "app_promo",
        "name": "Mobile App Promotion",
        "description": "Download Upgrade Talents app banner with QR code and store links",
        "category": "App & Mobile",
        "section_type": "builtin",
        "is_enabled": True,
        "display_order": 20,
        "badge": "Mobile App",
        "config": {},
    },
]

async def get_or_initialize_homepage_sections() -> List[Dict[str, Any]]:
    doc = await website_settings_collection.find_one({"_type": "settings"}, {"_id": 0, "homepage_sections": 1})
    stored = (doc or {}).get("homepage_sections")
    
    if not stored or not isinstance(stored, list) or len(stored) == 0:
        await website_settings_collection.update_one(
            {"_type": "settings"},
            {"$set": {"homepage_sections": DEFAULT_HOMEPAGE_SECTIONS, "updated_at": datetime.now(timezone.utc)}},
            upsert=True
        )
        return [dict(s) for s in DEFAULT_HOMEPAGE_SECTIONS]
    
    # Ensure all built-in sections exist in stored list
    stored_ids = {s.get("id") for s in stored if isinstance(s, dict) and s.get("id")}
    missing = [d for d in DEFAULT_HOMEPAGE_SECTIONS if d["id"] not in stored_ids]
    if missing:
        max_order = max([s.get("display_order", 0) for s in stored if isinstance(s, dict)] or [0])
        for idx, m in enumerate(missing):
            m_copy = dict(m)
            m_copy["display_order"] = max_order + idx + 1
            stored.append(m_copy)
        await website_settings_collection.update_one(
            {"_type": "settings"},
            {"$set": {"homepage_sections": stored, "updated_at": datetime.now(timezone.utc)}}
        )
    
    stored.sort(key=lambda x: x.get("display_order", 999))
    return stored

# ── Homepage Sections Endpoints ──
@router.get("/homepage-sections", tags=["admin-cms"])
async def get_admin_homepage_sections(admin=Depends(get_current_admin)):
    """Returns the full list of homepage sections (both built-in and custom) with order and enabled state."""
    sections = await get_or_initialize_homepage_sections()
    return sections

@router.put("/homepage-sections", tags=["admin-cms"])
async def update_admin_homepage_sections(data: HomepageSectionsUpdateRequest, admin=Depends(get_current_admin)):
    """Bulk update homepage sections order, visibility and config."""
    sections_data = [s.model_dump() for s in data.sections]
    sections_data.sort(key=lambda x: x.get("display_order", 999))
    
    await website_settings_collection.update_one(
        {"_type": "settings"},
        {"$set": {"homepage_sections": sections_data, "updated_at": datetime.now(timezone.utc)}},
        upsert=True
    )
    FastCache.delete("homepage")
    await log_admin_action(admin["email"], "update", "homepage_sections")
    return sections_data

@router.post("/homepage-sections/custom", tags=["admin-cms"])
async def create_admin_custom_section(data: CreateCustomSectionRequest, admin=Depends(get_current_admin)):
    """Create a new custom homepage section (banner, cards grid, rich text, split media, CTA, etc.)."""
    sections = await get_or_initialize_homepage_sections()
    
    timestamp = int(datetime.now(timezone.utc).timestamp())
    clean_slug = re.sub(r'[^a-zA-Z0-9_]', '_', data.name.lower().strip())[:20]
    sec_id = f"custom_{clean_slug}_{timestamp}"
    
    max_order = max([s.get("display_order", 0) for s in sections if isinstance(s, dict)] or [0])
    target_order = data.display_order if data.display_order is not None else (max_order + 1)
    
    new_section = {
        "id": sec_id,
        "name": data.name,
        "description": data.description or f"Custom {data.section_type.replace('_', ' ').title()} block",
        "category": data.category or "Custom Sections",
        "section_type": data.section_type,
        "is_enabled": data.is_enabled,
        "display_order": target_order,
        "badge": data.badge or "Custom",
        "config": data.config or {},
    }
    
    sections.append(new_section)
    sections.sort(key=lambda x: x.get("display_order", 999))
    
    await website_settings_collection.update_one(
        {"_type": "settings"},
        {"$set": {"homepage_sections": sections, "updated_at": datetime.now(timezone.utc)}},
        upsert=True
    )
    FastCache.delete("homepage")
    await log_admin_action(admin["email"], "create_custom_section", sec_id)
    return new_section

@router.put("/homepage-sections/{section_id}", tags=["admin-cms"])
async def update_admin_homepage_section(section_id: str, data: UpdateSectionItemRequest, admin=Depends(get_current_admin)):
    """Update visibility toggle, order, name or configuration of an individual section."""
    sections = await get_or_initialize_homepage_sections()
    
    target_idx = None
    for idx, s in enumerate(sections):
        if s.get("id") == section_id:
            target_idx = idx
            break
            
    if target_idx is None:
        raise HTTPException(status_code=404, detail="Section not found")
        
    sec = sections[target_idx]
    if data.name is not None:
        sec["name"] = data.name
    if data.description is not None:
        sec["description"] = data.description
    if data.category is not None:
        sec["category"] = data.category
    if data.is_enabled is not None:
        sec["is_enabled"] = data.is_enabled
    if data.display_order is not None:
        sec["display_order"] = data.display_order
    if data.badge is not None:
        sec["badge"] = data.badge
    if data.config is not None:
        current_config = sec.get("config") or {}
        current_config.update(data.config)
        sec["config"] = current_config
        
    sections.sort(key=lambda x: x.get("display_order", 999))
    
    await website_settings_collection.update_one(
        {"_type": "settings"},
        {"$set": {"homepage_sections": sections, "updated_at": datetime.now(timezone.utc)}},
        upsert=True
    )
    FastCache.delete("homepage")
    await log_admin_action(admin["email"], "update_section", f"{section_id}_enabled_{sec['is_enabled']}")
    return sec

@router.delete("/homepage-sections/{section_id}", tags=["admin-cms"])
async def delete_admin_homepage_section(section_id: str, admin=Depends(get_current_admin)):
    """Delete a custom section. Built-in sections cannot be deleted, but can be switched OFF."""
    sections = await get_or_initialize_homepage_sections()
    
    target_idx = None
    for idx, s in enumerate(sections):
        if s.get("id") == section_id:
            target_idx = idx
            break
            
    if target_idx is None:
        raise HTTPException(status_code=404, detail="Section not found")
        
    sec = sections[target_idx]
    if sec.get("section_type") == "builtin":
        raise HTTPException(
            status_code=400,
            detail="Built-in sections cannot be deleted. You can toggle them OFF instead."
        )
        
    sections.pop(target_idx)
    for i, s in enumerate(sections):
        s["display_order"] = i + 1
        
    await website_settings_collection.update_one(
        {"_type": "settings"},
        {"$set": {"homepage_sections": sections, "updated_at": datetime.now(timezone.utc)}},
        upsert=True
    )
    FastCache.delete("homepage")
    await log_admin_action(admin["email"], "delete_custom_section", section_id)
    return {"message": "Custom section removed successfully", "id": section_id}

@router.post("/homepage-sections/reset-defaults", tags=["admin-cms"])
async def reset_admin_homepage_sections(admin=Depends(get_current_admin)):
    """Reset all sections back to default built-in order and enable them."""
    current = await get_or_initialize_homepage_sections()
    custom_sections = [s for s in current if s.get("section_type") != "builtin"]
    
    fresh_sections = [dict(s) for s in DEFAULT_HOMEPAGE_SECTIONS]
    max_order = len(fresh_sections)
    for idx, cs in enumerate(custom_sections):
        cs["display_order"] = max_order + idx + 1
        fresh_sections.append(cs)
        
    await website_settings_collection.update_one(
        {"_type": "settings"},
        {"$set": {"homepage_sections": fresh_sections, "updated_at": datetime.now(timezone.utc)}},
        upsert=True
    )
    FastCache.delete("homepage")
    await log_admin_action(admin["email"], "reset_sections_to_defaults", "homepage_sections")
    return fresh_sections

DEFAULT_FEATURED_TILES = [
    {
        "id": "tile-competition",
        "title": "Competition",
        "subtitle": "National Championship",
        "action_text": "Learn more",
        "image_url": "https://images.unsplash.com/photo-1547153760-18fc86324498?q=80&w=1200&auto=format&fit=crop",
        "target_url": "/competitions",
        "icon_name": "Trophy",
        "display_order": 1,
    },
    {
        "id": "tile-event",
        "title": "Event",
        "subtitle": "Live Stage Shows",
        "action_text": "Learn more",
        "image_url": "https://images.unsplash.com/photo-1516450360452-9312f5e86fc7?q=80&w=1200&auto=format&fit=crop",
        "target_url": "/events",
        "icon_name": "CalendarDays",
        "display_order": 2,
    },
    {
        "id": "tile-shop",
        "title": "Shop",
        "subtitle": "Official Streetwear & Merch",
        "action_text": "Learn more",
        "image_url": "https://images.unsplash.com/photo-1509631179647-0177331693ae?q=80&w=1200&auto=format&fit=crop",
        "target_url": "/shop",
        "icon_name": "ShoppingBag",
        "display_order": 3,
    },
    {
        "id": "tile-course",
        "title": "Course",
        "subtitle": "Masterclasses & Workshops",
        "action_text": "Learn more",
        "image_url": "https://images.unsplash.com/photo-1508700115892-45ecd05ae2ad?q=80&w=1200&auto=format&fit=crop",
        "target_url": "/courses",
        "icon_name": "GraduationCap",
        "display_order": 4,
    },
]

@router.get("/featured-tiles", tags=["admin-cms"])
async def get_admin_featured_tiles(admin=Depends(get_current_admin)):
    settings = await website_settings_collection.find_one({"_type": "settings"})
    tiles = (settings or {}).get("featured_tiles")
    return tiles if tiles and isinstance(tiles, list) and len(tiles) > 0 else DEFAULT_FEATURED_TILES

@router.put("/featured-tiles", tags=["admin-cms"])
async def update_admin_featured_tiles(tiles: List[Dict[str, Any]], admin=Depends(get_current_admin)):
    await website_settings_collection.update_one(
        {"_type": "settings"},
        {"$set": {"featured_tiles": tiles, "updated_at": datetime.now(timezone.utc)}},
        upsert=True
    )
    await log_admin_action(admin["email"], "update", "featured_tiles")
    return {"message": "Featured category tiles updated successfully", "tiles": tiles}

DEFAULT_HOMEPAGE_STATS = [
    {"label": "Live events", "value": 12, "suffix": "+", "icon": "calendar"},
    {"label": "Dancers on stage", "value": 25000, "suffix": "+", "icon": "users"},
    {"label": "Masterclasses", "value": 40, "suffix": "+", "icon": "graduation"},
    {"label": "Cities covered", "value": 28, "suffix": "", "icon": "trophy"},
]

@router.get("/homepage-stats", tags=["admin-cms"])
async def get_admin_homepage_stats(admin=Depends(get_current_admin)):
    settings = await website_settings_collection.find_one({"_type": "settings"})
    stats = (settings or {}).get("homepage_stats")
    return stats if stats and isinstance(stats, list) and len(stats) > 0 else DEFAULT_HOMEPAGE_STATS

@router.put("/homepage-stats", tags=["admin-cms"])
async def update_admin_homepage_stats(stats: List[Dict[str, Any]], admin=Depends(get_current_admin)):
    await website_settings_collection.update_one(
        {"_type": "settings"},
        {"$set": {"homepage_stats": stats, "updated_at": datetime.now(timezone.utc)}},
        upsert=True
    )
    await log_admin_action(admin["email"], "update", "homepage_stats")
    return {"message": "Homepage stats updated successfully", "stats": stats}

DEFAULT_GALLERY_SECTION = {
    "title": "A stage worth watching",
    "label": "In motion",
    "description": "Every performance, workshop and showdown — captured and brought to life. Scroll through the moments that make Upgrade Talents unforgettable.",
    "view_all_url": "/gallery",
    "images": [
        {"id": "img-1", "image_url": "https://images.unsplash.com/photo-1508700115892-45ecd05ae2ad?q=80&w=800&auto=format&fit=crop", "caption": "Solo Performance"},
        {"id": "img-2", "image_url": "https://images.unsplash.com/photo-1547153760-18fc86324498?q=80&w=800&auto=format&fit=crop", "caption": "Live Showdown"},
        {"id": "img-3", "image_url": "https://images.unsplash.com/photo-1516450360452-9312f5e86fc7?q=80&w=800&auto=format&fit=crop", "caption": "Street Battle"},
        {"id": "img-4", "image_url": "https://images.unsplash.com/photo-1469488865564-c2de10f69f96?q=80&w=800&auto=format&fit=crop", "caption": "Group Choreography"},
        {"id": "img-5", "image_url": "https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4?q=80&w=800&auto=format&fit=crop", "caption": "Audience Energy"},
        {"id": "img-6", "image_url": "https://images.unsplash.com/photo-1514306191717-452ec28c7814?q=80&w=800&auto=format&fit=crop", "caption": "Grand Finale Stage"},
    ],
}

@router.get("/gallery-section", tags=["admin-cms"])
async def get_admin_gallery_section(admin=Depends(get_current_admin)):
    settings = await website_settings_collection.find_one({"_type": "settings"})
    gallery = (settings or {}).get("gallery_section")
    return gallery if gallery and isinstance(gallery, dict) else DEFAULT_GALLERY_SECTION

@router.put("/gallery-section", tags=["admin-cms"])
async def update_admin_gallery_section(payload: Dict[str, Any], admin=Depends(get_current_admin)):
    await website_settings_collection.update_one(
        {"_type": "settings"},
        {"$set": {"gallery_section": payload, "updated_at": datetime.now(timezone.utc)}},
        upsert=True
    )
    await log_admin_action(admin["email"], "update", "gallery_section")
    return {"message": "Gallery section updated successfully", "gallery_section": payload}


# ════════════════════════════════════════
# STAGE MOMENTS / FLOOR PHOTOS (RING SHOWCASE)
# ════════════════════════════════════════
DEFAULT_STAGE_PHOTOS = [
    "https://cdn.21st.dev/assets/mirror/4d/4d1bff4ea8b9f400dff7ae405f92e15bd39ec76e9285c7d96017e64ba1de3ae1.jpg",
    "https://cdn.21st.dev/assets/mirror/ee/ee36e332fe99d7611c43b90511db08a4f84e4545caa78e7b52e9ccffe273864b.jpg",
    "https://cdn.21st.dev/assets/mirror/dd/dddeaa4e6132c65bf7ff99d197f792a30937b5cbfe97a2b1ee54a793684df52e.jpg",
    "https://cdn.21st.dev/assets/mirror/48/487977107b5011b5e1c25289f6e4393ef555e1a92daee554788e9363b233ca14.jpg",
    "https://cdn.21st.dev/assets/mirror/a1/a12509688be6c9d3b2cb26d2ea1cfce48b8a8dbf2653e17be8a3fc31bd69f162.jpg",
    "https://cdn.21st.dev/assets/mirror/05/051f9e565b5b0b221384d9c27a0760febc3fb48d190c046adfda579f3614c958.jpg",
    "https://cdn.21st.dev/assets/mirror/87/87d4f60a4465d19d14b8acefa46275df4ccd767ccea6d7f234ea5bad99cfec56.jpg",
    "https://cdn.21st.dev/assets/mirror/06/0610989e0675a12c02d35aa5464e2644bf77913214b748b73a894808d2d79877.jpg",
    "https://cdn.21st.dev/assets/mirror/fe/fe90b90751e671a9526134c4743e2fcbf6c1a24991aba273ad7418c88fc9c701.jpg",
    "https://cdn.21st.dev/assets/mirror/2e/2e0452c1994fcc2130a1b8ef68e34b5ea52d1b35b80dde09b99349ad1de54598.jpg",
    "https://cdn.21st.dev/assets/mirror/84/847dc523558808119dc4bbb5218ff862ae5f5b455f33e9a0304f2f5a4b2f0f2c.jpg",
    "https://cdn.21st.dev/assets/mirror/2f/2f128763d177d6bdda3de9f0a766693406034977082bdb542138b7b74af9697b.jpg",
    "https://cdn.21st.dev/assets/mirror/30/30f8223f1d1ecc24fc8b7a9995b8ee98449b1e9fc300c14fbccec01b6a83a0ae.jpg",
    "https://cdn.21st.dev/assets/mirror/b4/b4df0e3e425ffce3b4f62fd7bc717e4dc90b94455f4d65d64fc07fe9721b0519.jpg",
    "https://cdn.21st.dev/assets/mirror/90/90c625659f072e4a701c2e37fa4ca46f25c7ca8689d723e7802fe84cf17874e9.jpg",
    "https://cdn.21st.dev/assets/mirror/6a/6aa0befc69cc3db8f3f3558d4b5e1216a5b64ceae1b86f51acf1c3f1432a8cf7.jpg",
    "https://cdn.21st.dev/assets/mirror/dc/dc126cfd753dc217945d0ea2f915e539dfe4ffb5fdea02245d0f576b25a9c4dd.jpg",
    "https://cdn.21st.dev/assets/mirror/ab/ab5f167fcb763aee7c3247fb8af97875998a7f519a6119da7dadd9cf73ae1a2c.jpg",
    "https://cdn.21st.dev/assets/mirror/57/57d6e450f7068e5f3be04907dcc0815343e67df1dc82f2da0ff1c859afc1f700.jpg",
    "https://cdn.21st.dev/assets/mirror/e4/e491359016d1494074e287dfbb55be068cffc0110017ae2257a5d4cdec193a47.jpg",
]

DEFAULT_STAGE_MOMENTS_SECTION = {
    "title": "Moments from the floor",
    "subtitle": "Stages, crews and performances from across the Upgrade Talents circuit.",
    "intro_title": "The floor is built on movement.",
    "images": [
        {"id": f"stage-{i+1}", "image_url": url, "caption": f"Stage Moment #{i+1}"}
        for i, url in enumerate(DEFAULT_STAGE_PHOTOS)
    ],
}

@router.get("/stage-moments", tags=["admin-cms"])
async def get_admin_stage_moments(admin=Depends(get_current_admin)):
    settings = await website_settings_collection.find_one({"_type": "settings"})
    stage = (settings or {}).get("stage_moments_section")
    return stage if stage and isinstance(stage, dict) else DEFAULT_STAGE_MOMENTS_SECTION

@router.put("/stage-moments", tags=["admin-cms"])
async def update_admin_stage_moments(payload: Dict[str, Any], admin=Depends(get_current_admin)):
    await website_settings_collection.update_one(
        {"_type": "settings"},
        {"$set": {"stage_moments_section": payload, "updated_at": datetime.now(timezone.utc)}},
        upsert=True
    )
    await log_admin_action(admin["email"], "update", "stage_moments_section")
    return {"message": "Stage moments ring section updated successfully", "stage_moments_section": payload}

@router.post("/upload-stage-photo", tags=["admin-cms"])
async def upload_stage_moment_photo(
    file: UploadFile = File(...),
    admin: dict = Depends(get_current_admin)
):
    """Uploads a stage photo to Cloudinary storage and returns secure URL."""
    contents = await file.read()
    if not contents or len(contents) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    url = upload_image(contents, folder="eventora/stage-moments", filename=file.filename)
    if not url:
        raise HTTPException(status_code=500, detail="Failed to upload stage photo to storage.")
    return {"url": url, "image_url": url, "filename": file.filename}


# ════════════════════════════════════════
# SPOTLIGHT SOCIAL MEDIA POSTS & PROMOTIONS
# ════════════════════════════════════════
DEFAULT_SPOTLIGHT_ITEMS = [
    {
        "id": "spotlight-1",
        "platform": "instagram",
        "title": "Nrityashastra Arena",
        "category": "Classical & Semi-Classical",
        "tag": "Season 2026",
        "image_url": "https://images.unsplash.com/photo-1547153760-18fc86324498?auto=format&fit=crop&w=1200&q=85",
        "post_url": "https://instagram.com/upgradetalents",
    },
    {
        "id": "spotlight-2",
        "platform": "instagram",
        "title": "Street Dance Showdown",
        "category": "Hip-Hop & Krump",
        "tag": "Live Battle",
        "image_url": "https://images.unsplash.com/photo-1535525153412-5a42439a210d?auto=format&fit=crop&w=1200&q=85",
        "post_url": "https://instagram.com/upgradetalents",
    },
    {
        "id": "spotlight-3",
        "platform": "instagram",
        "title": "Spotlight Solo Battle",
        "category": "Freestyle All-Styles",
        "tag": "Arena Spotlight",
        "image_url": "https://images.unsplash.com/photo-1518834107812-67b0b7c58434?auto=format&fit=crop&w=1200&q=85",
        "post_url": "https://instagram.com/upgradetalents",
    },
    {
        "id": "spotlight-4",
        "platform": "instagram",
        "title": "Grand Championship Stage",
        "category": "Live Trophy Round",
        "tag": "Finals",
        "image_url": "https://images.unsplash.com/photo-1545959570-a94084071b5d?auto=format&fit=crop&w=1200&q=85",
        "post_url": "https://instagram.com/upgradetalents",
    },
    {
        "id": "spotlight-5",
        "platform": "instagram",
        "title": "Choreography Masterclass",
        "category": "Artist Workshop",
        "tag": "Live Training",
        "image_url": "https://images.unsplash.com/photo-1514525253161-7a46d19cd819?auto=format&fit=crop&w=1200&q=85",
        "post_url": "https://events",
    },
    {
        "id": "spotlight-6",
        "platform": "instagram",
        "title": "Electrifying Stage Arena",
        "category": "National Tour 2026",
        "tag": "Main Stage",
        "image_url": "https://images.unsplash.com/photo-1540039155733-5bb30b53aa14?auto=format&fit=crop&w=1200&q=85",
        "post_url": "https://instagram.com/upgradetalents",
    },
    {
        "id": "spotlight-7",
        "platform": "instagram",
        "title": "Rhythm & Sync Showcase",
        "category": "Crew Showdown",
        "tag": "Group Dance",
        "image_url": "https://images.unsplash.com/photo-1508700115892-45ecd05ae2ad?auto=format&fit=crop&w=1200&q=85",
        "post_url": "https://instagram.com/upgradetalents",
    },
    {
        "id": "spotlight-8",
        "platform": "instagram",
        "title": "Live Audience Spectacle",
        "category": "Live Energy",
        "tag": "Electric Crowd",
        "image_url": "https://images.unsplash.com/photo-1492684223066-81342ee5ff30?auto=format&fit=crop&w=1200&q=85",
        "post_url": "https://instagram.com/upgradetalents",
    },
]

DEFAULT_SPOTLIGHT_SECTION = {
    "label": "Live Stage Moments",
    "title": "Moments in the Spotlight",
    "subtitle": "Raw passion, electrifying rhythm, and pure stage energy from our dancers and performers across India.",
    "handle": "upgradetalents",
    "follow_text": "Follow @upgradetalents on Instagram",
    "follow_url": "https://instagram.com/upgradetalents",
    "items": DEFAULT_SPOTLIGHT_ITEMS,
}

class LinkPreviewReq(BaseModel):
    url: str

@router.post("/extract-link-preview", tags=["admin-cms"])
async def extract_link_preview(
    payload: LinkPreviewReq,
    admin: dict = Depends(get_current_admin)
):
    """
    Intelligently extracts high-res poster image, video thumbnail, title and metadata
    from social media links (YouTube, Instagram, X/Twitter, Facebook, etc.) or web pages.
    """
    url = (payload.url or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL is required.")

    lowered = url.lower()
    platform = "general"
    if "instagram.com" in lowered:
        platform = "instagram"
    elif "youtube.com" in lowered or "youtu.be" in lowered:
        platform = "youtube"
    elif "facebook.com" in lowered or "fb.watch" in lowered:
        platform = "facebook"
    elif "twitter.com" in lowered or "x.com" in lowered:
        platform = "x"
    elif "linkedin.com" in lowered:
        platform = "linkedin"
    elif "tiktok.com" in lowered:
        platform = "tiktok"

    # Direct image check
    if re.search(r'\.(?:jpg|jpeg|png|webp|gif)(?:\?.*)?$', lowered):
        return {
            "success": True,
            "platform": platform,
            "title": "Promotional Poster",
            "image_url": url,
            "category": "Spotlight Visual",
            "tag": "Featured Poster"
        }

    # 1. YouTube specialized extraction
    if platform == "youtube":
        m = re.search(r'(?:v=|\/|embed\/|shorts\/)([0-9A-Za-z_-]{11})', url)
        video_id = m.group(1) if m else None
        title = "YouTube Video"
        image_url = f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg" if video_id else None
        channel_name = ""
        try:
            async with httpx.AsyncClient(timeout=6.0) as client:
                res = await client.get(f"https://www.youtube.com/oembed?url={urllib.parse.quote(url)}&format=json")
                if res.status_code == 200:
                    data = res.json()
                    title = data.get("title", title)
                    image_url = data.get("thumbnail_url", image_url)
                    channel_name = data.get("author_name", "")
        except Exception:
            pass
        return {
            "success": True,
            "platform": "youtube",
            "title": title,
            "image_url": image_url,
            "category": channel_name or "YouTube Spotlight",
            "tag": "Video Showcase"
        }

    # 2. X / Twitter via vxtwitter
    if platform == "x":
        m = re.search(r'status(?:es)?\/([0-9]+)', url)
        if m:
            tid = m.group(1)
            try:
                async with httpx.AsyncClient(timeout=6.0) as client:
                    res = await client.get(f"https://api.vxtwitter.com/Twitter/status/{tid}")
                    if res.status_code == 200:
                        data = res.json()
                        text = data.get("text", "")
                        media_urls = data.get("mediaURLs", [])
                        image_url = media_urls[0] if media_urls else None
                        return {
                            "success": True,
                            "platform": "x",
                            "title": (text[:60] + "...") if len(text) > 60 else text or "X Spotlight Post",
                            "image_url": image_url,
                            "category": f"@{data.get('user_screen_name', 'X')}",
                            "tag": "Social Update"
                        }
            except Exception:
                pass

    # 3. Instagram oEmbed attempt
    if platform == "instagram":
        try:
            async with httpx.AsyncClient(timeout=6.0) as client:
                oembed_url = f"https://api.instagram.com/oembed/?url={urllib.parse.quote(url)}"
                res = await client.get(oembed_url)
                if res.status_code == 200:
                    data = res.json()
                    return {
                        "success": True,
                        "platform": "instagram",
                        "title": data.get("title") or "Instagram Spotlight Moment",
                        "image_url": data.get("thumbnail_url"),
                        "category": data.get("author_name") or "Instagram Reel",
                        "tag": "Live Stage"
                    }
        except Exception:
            pass

    # 4. Universal Open Graph extraction with social crawler user agent
    headers = {
        "User-Agent": "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }
    try:
        async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=8.0) as client:
            res = await client.get(url)
            html = res.text
            og_img = re.search(r'<meta[^>]+property=[\"\']og:image[\"\'][^>]+content=[\"\']([^\"\']+)[\"\']', html, re.I)
            if not og_img:
                og_img = re.search(r'<meta[^>]+content=[\"\']([^\"\']+)[\"\'][^>]+property=[\"\']og:image[\"\']', html, re.I)
            if not og_img:
                og_img = re.search(r'<meta[^>]+name=[\"\']twitter:image[\"\'][^>]+content=[\"\']([^\"\']+)[\"\']', html, re.I)
            
            og_title = re.search(r'<meta[^>]+property=[\"\']og:title[\"\'][^>]+content=[\"\']([^\"\']+)[\"\']', html, re.I)
            if not og_title:
                og_title = re.search(r'<meta[^>]+name=[\"\']twitter:title[\"\'][^>]+content=[\"\']([^\"\']+)[\"\']', html, re.I)
            if not og_title:
                og_title = re.search(r'<title>([^<]+)</title>', html, re.I)

            img_url = og_img.group(1) if og_img else None
            title_text = og_title.group(1).strip() if og_title else f"{platform.title()} Spotlight Moment"
            
            return {
                "success": True,
                "platform": platform,
                "title": title_text[:70],
                "image_url": img_url,
                "category": f"{platform.title()} Spotlight",
                "tag": "Live Stage"
            }
    except Exception as e:
        return {
            "success": True,
            "platform": platform,
            "title": f"{platform.title()} Spotlight Post",
            "image_url": None,
            "category": f"{platform.title()} Post",
            "tag": "Live Stage",
            "note": "Could not auto-extract image. Please upload a poster or flyer."
        }

@router.get("/spotlight-section", tags=["admin-cms"])
async def get_admin_spotlight_section(admin=Depends(get_current_admin)):
    settings = await website_settings_collection.find_one({"_type": "settings"})
    spotlight = (settings or {}).get("spotlight_section")
    return spotlight if spotlight and isinstance(spotlight, dict) else DEFAULT_SPOTLIGHT_SECTION

@router.put("/spotlight-section", tags=["admin-cms"])
async def update_admin_spotlight_section(payload: Dict[str, Any], admin=Depends(get_current_admin)):
    await website_settings_collection.update_one(
        {"_type": "settings"},
        {"$set": {"spotlight_section": payload, "updated_at": datetime.now(timezone.utc)}},
        upsert=True
    )
    await log_admin_action(admin["email"], "update", "spotlight_section")
    return {"message": "Spotlight section updated successfully", "spotlight_section": payload}

@router.post("/upload-spotlight-photo", tags=["admin-cms"])
async def upload_spotlight_moment_photo(
    file: UploadFile = File(...),
    admin: dict = Depends(get_current_admin)
):
    """Uploads a spotlight promo poster/photo to Cloudinary storage and returns secure URL."""
    contents = await file.read()
    if not contents or len(contents) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    url = upload_image(contents, folder="eventora/spotlight", filename=file.filename)
    if not url:
        raise HTTPException(status_code=500, detail="Failed to upload spotlight photo to storage.")
    return {"url": url, "image_url": url, "filename": file.filename}


# ════════════════════════════════════════
# PARTNERS & SPONSORS SECTION
# ════════════════════════════════════════
DEFAULT_PARTNERS = [
    {
        "id": "partner-1",
        "name": "Red Bull",
        "logo_url": "https://upload.wikimedia.org/wikipedia/en/thumb/f/f5/RedBullEnergyDrink.svg/640px-RedBullEnergyDrink.svg.png",
        "website_url": "https://www.redbull.com",
        "is_active": True,
    },
    {
        "id": "partner-2",
        "name": "Puma",
        "logo_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ae/Puma-logo-%28text%29.svg/640px-Puma-logo-%28text%29.svg.png",
        "website_url": "https://puma.com",
        "is_active": True,
    },
    {
        "id": "partner-3",
        "name": "Sony Music",
        "logo_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/7/75/Sony_Music_logo.svg/640px-Sony_Music_logo.svg.png",
        "website_url": "https://sonymusic.com",
        "is_active": True,
    },
    {
        "id": "partner-4",
        "name": "Monster Energy",
        "logo_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/d/d7/Monster_Energy_logo.svg/640px-Monster_Energy_logo.svg.png",
        "website_url": "https://www.monsterenergy.com",
        "is_active": True,
    },
    {
        "id": "partner-5",
        "name": "JBL",
        "logo_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/1/1e/JBL_logo.svg/640px-JBL_logo.svg.png",
        "website_url": "https://jbl.com",
        "is_active": True,
    },
    {
        "id": "partner-6",
        "name": "Spotify",
        "logo_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/1/19/Spotify_logo_without_text.svg/640px-Spotify_logo_without_text.svg.png",
        "website_url": "https://spotify.com",
        "is_active": True,
    },
]

DEFAULT_PARTNERS_SECTION = {
    "label": "Powered by our partners",
    "title": "Official Partners & Sponsors",
    "partners": DEFAULT_PARTNERS,
}

@router.get("/partners-section", tags=["admin-cms"])
async def get_admin_partners_section(admin=Depends(get_current_admin)):
    settings = await website_settings_collection.find_one({"_type": "settings"})
    partners = (settings or {}).get("partners_section")
    return partners if partners and isinstance(partners, dict) else DEFAULT_PARTNERS_SECTION

@router.put("/partners-section", tags=["admin-cms"])
async def update_admin_partners_section(payload: Dict[str, Any], admin=Depends(get_current_admin)):
    await website_settings_collection.update_one(
        {"_type": "settings"},
        {"$set": {"partners_section": payload, "updated_at": datetime.now(timezone.utc)}},
        upsert=True
    )
    await log_admin_action(admin["email"], "update", "partners_section")
    return {"message": "Partners section updated successfully", "partners_section": payload}

@router.post("/upload-partner-logo", tags=["admin-cms"])
async def upload_partner_logo(
    file: UploadFile = File(...),
    admin: dict = Depends(get_current_admin)
):
    """Uploads a partner/sponsor logo to Cloudinary storage and returns secure URL."""
    contents = await file.read()
    if not contents or len(contents) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    url = upload_image(contents, folder="eventora/partners", filename=file.filename)
    if not url:
        raise HTTPException(status_code=500, detail="Failed to upload partner logo to storage.")
    return {"url": url, "logo_url": url, "filename": file.filename}

# ════════════════════════════════════════
# GRAND SPONSOR POP-UP (WEBSITE OPENING MODAL)
# ════════════════════════════════════════
DEFAULT_SPONSOR_POPUP = {
    "is_enabled": False,
    "sponsor_name": "Platinum Sponsor",
    "badge_text": "OFFICIAL TITLE SPONSOR",
    "title": "Grand National Championship 2026",
    "subtitle": "Presented by our Mega Platinum Partner — Win Cash Prizes & National Recognition",
    "banner_image_url": "https://images.unsplash.com/photo-1547153760-18fc86324498?q=80&w=1920&auto=format&fit=crop",
    "mobile_image_url": "",
    "cta_text": "Explore Exclusive Offer",
    "cta_link": "/competitions",
    "show_mode": "once_per_session",  # "once_per_session", "once_per_day", "always"
    "auto_close_seconds": 0,
}

@router.get("/sponsor-popup", tags=["admin-cms"])
async def get_admin_sponsor_popup(admin=Depends(get_current_admin)):
    settings = await website_settings_collection.find_one({"_type": "settings"})
    popup = (settings or {}).get("sponsor_popup")
    return popup if popup and isinstance(popup, dict) else DEFAULT_SPONSOR_POPUP

@router.put("/sponsor-popup", tags=["admin-cms"])
async def update_admin_sponsor_popup(payload: Dict[str, Any], admin=Depends(get_current_admin)):
    await website_settings_collection.update_one(
        {"_type": "settings"},
        {"$set": {"sponsor_popup": payload, "updated_at": datetime.now(timezone.utc)}},
        upsert=True
    )
    await log_admin_action(admin["email"], "update", "sponsor_popup")
    return {"message": "Grand sponsor popup settings updated successfully", "sponsor_popup": payload}

@router.post("/upload-sponsor-popup-image", tags=["admin-cms"])
async def upload_sponsor_popup_image(
    file: UploadFile = File(...),
    admin: dict = Depends(get_current_admin)
):
    """Uploads a high-resolution grand sponsor popup banner to Cloudinary storage."""
    contents = await file.read()
    if not contents or len(contents) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    url = upload_image(contents, folder="eventora/sponsor-popup", filename=file.filename)
    if not url:
        raise HTTPException(status_code=500, detail="Failed to upload sponsor popup image to storage.")
    return {"url": url, "image_url": url, "filename": file.filename}


# ════════════════════════════════════════
# EVENTS
# ════════════════════════════════════════
def normalize_event_categories(doc: dict) -> dict:
    raw_cats = doc.get("categories") or []
    if isinstance(raw_cats, str):
        try:
            import json
            raw_cats = json.loads(raw_cats)
        except Exception:
            raw_cats = []
    norm_cats = []
    seen = set()
    for cat in raw_cats:
        if isinstance(cat, dict):
            name = (cat.get("name") or "").strip()
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())
            p = float(cat.get("price") if cat.get("price") is not None else (doc.get("price") or 0.0))
            c = cat.get("capacity")
            norm_cats.append({
                "name": name,
                "price": max(0.0, p),
                "capacity": int(c) if c is not None and str(c).strip() != "" else None,
                "description": cat.get("description")
            })
        elif isinstance(cat, str) and cat.strip():
            name = cat.strip()
            if name.lower() not in seen:
                seen.add(name.lower())
                norm_cats.append({
                    "name": name,
                    "price": float(doc.get("price") or 0.0),
                    "capacity": None,
                    "description": None
                })
    if not norm_cats:
        cat_name = (doc.get("category") or "General").strip()
        norm_cats.append({
            "name": cat_name,
            "price": float(doc.get("price") or 0.0),
            "capacity": int(doc.get("capacity")) if doc.get("capacity") is not None else None,
            "description": None
        })
    doc["categories"] = norm_cats
    doc["category"] = norm_cats[0]["name"]
    if not doc.get("price") and norm_cats[0]["price"] > 0.0:
        doc["price"] = norm_cats[0]["price"]
    total_cat_cap = sum(c["capacity"] for c in norm_cats if c.get("capacity"))
    if total_cat_cap > 0 and (not doc.get("capacity") or doc.get("capacity") == 0):
        doc["capacity"] = total_cat_cap
    return doc

@router.get("/events", tags=["admin-events"])
async def list_events(skip: int = 0, limit: int = 200, search: Optional[str] = None, admin=Depends(get_current_admin)):
    q = {"is_deleted": {"$ne": True}}
    if search:
        q["title"] = {"$regex": search, "$options": "i"}
    docs = await event_collection.find(q).sort([("updated_at", -1), ("created_at", -1), ("_id", -1)]).skip(skip).limit(limit).to_list(length=limit)
    for d in docs:
        ShareService.enrich_event(d)
    return clean_list(docs)

@router.post("/events", tags=["admin-events"])
async def create_event(data: EventCreate, admin=Depends(get_current_admin)):
    doc = data.model_dump()
    doc["registered_count"] = 0
    doc["created_at"] = datetime.now(timezone.utc)
    doc["created_by"] = admin["email"]
    doc = normalize_event_categories(doc)

    result = await event_collection.insert_one(doc)
    event_id = str(result.inserted_id)
    await log_admin_action(admin["email"], "create", "event", event_id)
    doc["_id"] = event_id
    ShareService.enrich_event(doc)

    # Auto-create initial ticket tiers if ticketing is enabled
    if doc.get("ticket_enabled", True):
        for idx, cat_item in enumerate(doc["categories"]):
            cat_name = cat_item["name"]
            cat_price = float(cat_item["price"])
            cat_cap = int(cat_item.get("capacity") or doc.get("capacity") or 100)
            ticket_doc = {
                "event_id": event_id,
                "type": cat_name,
                "category": cat_name,
                "price": cat_price,
                "total_quantity": cat_cap,
                "available": cat_cap,
                "sale_start": None,
                "sale_end": None,
                "is_visible": True,
                "display_order": idx,
                "created_at": datetime.now(timezone.utc)
            }
            await ticket_collection.insert_one(ticket_doc)

    return doc

@router.put("/events/{event_id}", tags=["admin-events"])
async def update_event(event_id: str, data: EventUpdate, admin=Depends(get_current_admin)):
    update = {k: v for k, v in data.model_dump().items() if v is not None}
    update["updated_at"] = datetime.now(timezone.utc)

    clean_eid = str(event_id).strip()
    ev_ids = [clean_eid]
    if ObjectId.is_valid(clean_eid):
        ev_ids.append(ObjectId(clean_eid))

    existing = await event_collection.find_one({"_id": {"$in": ev_ids}, "is_deleted": {"$ne": True}})
    if not existing:
        raise HTTPException(status_code=404, detail="Event not found")

    if "categories" in update or "category" in update:
        dummy = dict(existing)
        dummy.update(update)
        normalized = normalize_event_categories(dummy)
        update["categories"] = normalized["categories"]
        update["category"] = normalized["category"]
        if "price" not in update:
            update["price"] = normalized["price"]

    result = await event_collection.update_one({"_id": existing["_id"], "is_deleted": {"$ne": True}}, {"$set": update})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Event not found")
    await log_admin_action(admin["email"], "update", "event", clean_eid)
    doc = await event_collection.find_one({"_id": existing["_id"]})

    # Keep ticket tiers synced with event categories/price/capacity
    if "categories" in update or "price" in update or "capacity" in update:
        existing_tiers = await ticket_collection.find({"event_id": {"$in": ev_ids}}).to_list(length=100)
        cats = doc.get("categories", [])
        if cats:
            existing_by_name = {t.get("type", "").strip().lower(): t for t in existing_tiers}
            existing_by_cat = {t.get("category", "").strip().lower(): t for t in existing_tiers if t.get("category")}

            active_cat_names = set()
            for idx, c in enumerate(cats):
                c_name = c["name"].strip()
                active_cat_names.add(c_name.lower())
                c_price = float(c.get("price", 0.0))
                c_cap = int(c.get("capacity") or doc.get("capacity") or 100)
                c_desc = c.get("description")
                matched = existing_by_name.get(c_name.lower()) or existing_by_cat.get(c_name.lower())
                if matched:
                    sold = max(0, int(matched.get("total_quantity", c_cap)) - int(matched.get("available", c_cap)))
                    update_dict = {
                        "price": c_price,
                        "type": c_name,
                        "category": c_name,
                        "total_quantity": c_cap,
                        "available": max(0, c_cap - sold),
                        "display_order": idx,
                        "is_visible": True,
                        "updated_at": datetime.now(timezone.utc)
                    }
                    if c_desc:
                        update_dict["description"] = c_desc
                    await ticket_collection.update_one(
                        {"_id": matched["_id"]},
                        {"$set": update_dict}
                    )
                elif doc.get("ticket_enabled", True):
                    new_tier = {
                        "event_id": clean_eid,
                        "type": c_name,
                        "category": c_name,
                        "description": c_desc or "",
                        "price": c_price,
                        "total_quantity": c_cap,
                        "available": c_cap,
                        "sale_start": None,
                        "sale_end": None,
                        "is_visible": True,
                        "display_order": idx,
                        "created_at": datetime.now(timezone.utc)
                    }
                    await ticket_collection.insert_one(new_tier)

            # Purge or hide orphaned tiers that no longer exist in new categories
            for t in existing_tiers:
                t_type = (t.get("type") or "").strip().lower()
                t_cat = (t.get("category") or "").strip().lower()
                if t_type not in active_cat_names and t_cat not in active_cat_names:
                    sold = max(0, int(t.get("total_quantity", 0)) - int(t.get("available", 0)))
                    if sold > 0:
                        await ticket_collection.update_one({"_id": t["_id"]}, {"$set": {"is_visible": False}})
                    else:
                        await ticket_collection.delete_one({"_id": t["_id"]})
        elif len(existing_tiers) == 1 and existing_tiers[0].get("type", "").lower() in ["general", "general admission"]:
            tier_update = {}
            if "price" in update:
                tier_update["price"] = float(update["price"])
            if "capacity" in update:
                new_cap = int(update["capacity"])
                tier_update["total_quantity"] = new_cap
                tier_update["available"] = max(0, new_cap - int(doc.get("registered_count") or 0))
            if tier_update:
                tier_update["updated_at"] = datetime.now(timezone.utc)
                await ticket_collection.update_one({"_id": existing_tiers[0]["_id"]}, {"$set": tier_update})

    if doc:
        ShareService.enrich_event(doc)
    return clean(doc)

@router.delete("/events/{event_id}", tags=["admin-events"])
async def delete_event(event_id: str, admin=Depends(get_current_admin)):
    result = await event_collection.update_one({"_id": oid(event_id)}, {"$set": {"is_deleted": True, "deleted_at": datetime.now(timezone.utc)}})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Event not found")
    await log_admin_action(admin["email"], "soft_delete", "event", event_id)
    return {"message": "Event deleted"}

@router.patch("/events/{event_id}/publish", tags=["admin-events"])
async def toggle_event_publish(event_id: str, data: PublishToggle, admin=Depends(get_current_admin)):
    await event_collection.update_one({"_id": oid(event_id)}, {"$set": {"is_published": data.is_published, "published_at": datetime.now(timezone.utc) if data.is_published else None, "published_by": admin["email"] if data.is_published else None}})
    await log_admin_action(admin["email"], "publish" if data.is_published else "unpublish", "event", event_id)
    return {"message": f"Event {'published' if data.is_published else 'unpublished'}"}

@router.patch("/events/{event_id}/visibility", tags=["admin-events"])
async def toggle_event_visibility(event_id: str, data: VisibilityToggle, admin=Depends(get_current_admin)):
    await event_collection.update_one({"_id": oid(event_id)}, {"$set": {"is_visible": data.is_visible}})
    await log_admin_action(admin["email"], "visibility", "event", event_id, metadata={"visible": data.is_visible})
    return {"message": f"Event visibility set to {data.is_visible}"}

@router.patch("/events/{event_id}/reorder", tags=["admin-events"])
async def reorder_event(event_id: str, data: ReorderReq, admin=Depends(get_current_admin)):
    await event_collection.update_one({"_id": oid(event_id)}, {"$set": {"display_order": data.display_order}})
    return {"message": f"Display order set to {data.display_order}"}

# ════════════════════════════════════════
# LIVE EVENTS
# ════════════════════════════════════════
@router.get("/live-events", tags=["admin-live-events"])
async def list_live_events(admin=Depends(get_current_admin)):
    docs = await live_event_collection.find().sort("display_order", 1).to_list(length=50)
    return clean_list(docs)

@router.post("/live-events", tags=["admin-live-events"])
async def create_live_event(data: LiveEventCreate, admin=Depends(get_current_admin)):
    doc = data.model_dump()
    doc["created_at"] = datetime.now(timezone.utc)
    result = await live_event_collection.insert_one(doc)
    await log_admin_action(admin["email"], "create", "live_event", str(result.inserted_id))
    doc["_id"] = str(result.inserted_id)
    return doc

@router.put("/live-events/{le_id}", tags=["admin-live-events"])
async def update_live_event(le_id: str, data: LiveEventUpdate, admin=Depends(get_current_admin)):
    update = {k: v for k, v in data.model_dump().items() if v is not None}
    update["updated_at"] = datetime.now(timezone.utc)
    result = await live_event_collection.update_one({"_id": oid(le_id)}, {"$set": update})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Live event not found")
    await log_admin_action(admin["email"], "update", "live_event", le_id)
    doc = await live_event_collection.find_one({"_id": oid(le_id)})
    return clean(doc)

@router.delete("/live-events/{le_id}", tags=["admin-live-events"])
async def delete_live_event(le_id: str, admin=Depends(get_current_admin)):
    result = await live_event_collection.delete_one({"_id": oid(le_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Live event not found")
    await log_admin_action(admin["email"], "delete", "live_event", le_id)
    return {"message": "Live event deleted"}

@router.patch("/live-events/{le_id}/publish", tags=["admin-live-events"])
async def toggle_live_event_publish(le_id: str, data: PublishToggle, admin=Depends(get_current_admin)):
    await live_event_collection.update_one({"_id": oid(le_id)}, {"$set": {"is_published": data.is_published}})
    await log_admin_action(admin["email"], "publish" if data.is_published else "unpublish", "live_event", le_id)
    return {"message": f"Live event {'published' if data.is_published else 'unpublished'}"}

# ════════════════════════════════════════
# BANNERS
# ════════════════════════════════════════
# BANNERS (Hero Slider & Sponsors)
# ════════════════════════════════════════
@router.post("/banners/upload-image", tags=["admin-banners"])
async def upload_banner_image(
    file: UploadFile = File(...),
    ratio: Optional[str] = Query("16:9", description="Banner aspect ratio: '16:9' (desktop) or '9:16' (mobile)"),
    admin: dict = Depends(get_current_admin)
):
    """Uploads banner image to Cloudinary (separated into web 16:9 or mobile 9:16 folder)."""
    contents = await file.read()
    if not contents or len(contents) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    folder = "eventora/banners/mobile" if ratio == "9:16" else "eventora/banners/desktop"
    url = upload_image(contents, folder=folder, filename=file.filename)
    if not url:
        raise HTTPException(status_code=500, detail="Failed to upload banner image to storage.")
    return {"url": url, "ratio": ratio, "filename": file.filename}

@router.get("/banners", tags=["admin-banners"])
async def list_banners(admin=Depends(get_current_admin)):
    docs = await banner_collection.find({"is_deleted": {"$ne": True}}).sort("display_order", 1).to_list(length=100)
    for d in docs:
        if not d.get("image_url_desktop"):
            d["image_url_desktop"] = d.get("image_url")
        if not d.get("image_url") and d.get("image_url_desktop"):
            d["image_url"] = d.get("image_url_desktop")
        if not d.get("image_url_mobile") and d.get("mobile_image_url"):
            d["image_url_mobile"] = d.get("mobile_image_url")
    return clean_list(docs)

@router.post("/banners", tags=["admin-banners"])
async def create_banner(data: BannerCreate, admin=Depends(get_current_admin)):
    doc = data.model_dump()
    # Normalize desktop image
    desktop_img = doc.get("image_url_desktop") or doc.get("image_url")
    if desktop_img:
        doc["image_url"] = desktop_img
        doc["image_url_desktop"] = desktop_img
    # Normalize mobile image
    mobile_img = doc.get("image_url_mobile") or doc.get("mobile_image_url")
    if mobile_img:
        doc["image_url_mobile"] = mobile_img
        doc["mobile_image_url"] = mobile_img
    doc["created_at"] = datetime.now(timezone.utc)
    result = await banner_collection.insert_one(doc)
    await log_admin_action(admin["email"], "create", "banner", str(result.inserted_id))
    doc["_id"] = str(result.inserted_id)
    return doc

@router.put("/banners/{banner_id}", tags=["admin-banners"])
async def update_banner(banner_id: str, data: BannerUpdate, admin=Depends(get_current_admin)):
    update = {k: v for k, v in data.model_dump().items() if v is not None}
    desktop_img = update.get("image_url_desktop") or update.get("image_url")
    if desktop_img:
        update["image_url"] = desktop_img
        update["image_url_desktop"] = desktop_img
    mobile_img = update.get("image_url_mobile") or update.get("mobile_image_url")
    if mobile_img:
        update["image_url_mobile"] = mobile_img
        update["mobile_image_url"] = mobile_img
    update["updated_at"] = datetime.now(timezone.utc)
    result = await banner_collection.update_one({"_id": oid(banner_id), "is_deleted": {"$ne": True}}, {"$set": update})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Banner not found")
    await log_admin_action(admin["email"], "update", "banner", banner_id)
    doc = await banner_collection.find_one({"_id": oid(banner_id)})
    return clean(doc)

@router.delete("/banners/{banner_id}", tags=["admin-banners"])
async def delete_banner(banner_id: str, admin=Depends(get_current_admin)):
    result = await banner_collection.update_one({"_id": oid(banner_id)}, {"$set": {"is_deleted": True}})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Banner not found")
    await log_admin_action(admin["email"], "soft_delete", "banner", banner_id)
    return {"message": "Banner deleted"}

@router.patch("/banners/{banner_id}/publish", tags=["admin-banners"])
async def toggle_banner_publish(banner_id: str, data: PublishToggle, admin=Depends(get_current_admin)):
    await banner_collection.update_one({"_id": oid(banner_id)}, {"$set": {"is_published": data.is_published}})
    await log_admin_action(admin["email"], "publish" if data.is_published else "unpublish", "banner", banner_id)
    return {"message": f"Banner {'published' if data.is_published else 'unpublished'}"}

# ════════════════════════════════════════
# TESTIMONIALS
# ════════════════════════════════════════
@router.get("/testimonials", tags=["admin-testimonials"])
async def list_testimonials(admin=Depends(get_current_admin)):
    docs = await testimonial_collection.find().sort("display_order", 1).to_list(length=100)
    return clean_list(docs)

@router.post("/testimonials", tags=["admin-testimonials"])
async def create_testimonial(data: TestimonialCreate, admin=Depends(get_current_admin)):
    doc = data.model_dump()
    doc["created_at"] = datetime.now(timezone.utc)
    result = await testimonial_collection.insert_one(doc)
    await log_admin_action(admin["email"], "create", "testimonial", str(result.inserted_id))
    doc["_id"] = str(result.inserted_id)
    return doc

@router.put("/testimonials/{tid}", tags=["admin-testimonials"])
async def update_testimonial(tid: str, data: TestimonialUpdate, admin=Depends(get_current_admin)):
    update = {k: v for k, v in data.model_dump().items() if v is not None}
    update["updated_at"] = datetime.now(timezone.utc)
    result = await testimonial_collection.update_one({"_id": oid(tid)}, {"$set": update})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Testimonial not found")
    await log_admin_action(admin["email"], "update", "testimonial", tid)
    doc = await testimonial_collection.find_one({"_id": oid(tid)})
    return clean(doc)

@router.delete("/testimonials/{tid}", tags=["admin-testimonials"])
async def delete_testimonial(tid: str, admin=Depends(get_current_admin)):
    result = await testimonial_collection.delete_one({"_id": oid(tid)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Testimonial not found")
    await log_admin_action(admin["email"], "delete", "testimonial", tid)
    return {"message": "Testimonial deleted"}

@router.patch("/testimonials/{tid}/approve", tags=["admin-testimonials"])
async def toggle_testimonial_approval(tid: str, data: ApprovalToggle, admin=Depends(get_current_admin)):
    await testimonial_collection.update_one({"_id": oid(tid)}, {"$set": {"is_approved": data.is_approved}})
    await log_admin_action(admin["email"], "approve" if data.is_approved else "reject", "testimonial", tid)
    return {"message": f"Testimonial {'approved' if data.is_approved else 'rejected'}"}

@router.patch("/testimonials/{tid}/publish", tags=["admin-testimonials"])
async def toggle_testimonial_publish(tid: str, data: PublishToggle, admin=Depends(get_current_admin)):
    await testimonial_collection.update_one({"_id": oid(tid)}, {"$set": {"is_published": data.is_published}})
    await log_admin_action(admin["email"], "publish" if data.is_published else "unpublish", "testimonial", tid)
    return {"message": f"Testimonial {'published' if data.is_published else 'unpublished'}"}

# ════════════════════════════════════════
# TICKETS
# ════════════════════════════════════════
@router.get("/tickets", tags=["admin-tickets"])
async def list_tickets(event_id: Optional[str] = None, admin=Depends(get_current_admin)):
    q = {}
    if event_id:
        q["event_id"] = event_id
    docs = await ticket_collection.find(q).sort("display_order", 1).to_list(length=100)

    # If an event is selected and has no tiers yet, auto-provision default tier
    if event_id and not docs:
        ev = None
        if ObjectId.is_valid(event_id):
            ev = await event_collection.find_one({"_id": ObjectId(event_id), "is_deleted": {"$ne": True}})
        if not ev:
            ev = await event_collection.find_one({"_id": event_id, "is_deleted": {"$ne": True}})
        if ev and ev.get("ticket_enabled", True) and not ev.get("is_deleted", False):
            cap = int(ev.get("capacity") or 100)
            price = float(ev.get("price") or 0.0)
            registered = int(ev.get("registered_count") or 0)
            default_ticket = {
                "event_id": str(event_id),
                "type": "General Admission",
                "price": price,
                "total_quantity": cap,
                "available": max(0, cap - registered),
                "sale_start": None,
                "sale_end": None,
                "is_visible": True,
                "display_order": 0,
                "created_at": datetime.now(timezone.utc)
            }
            res = await ticket_collection.insert_one(default_ticket)
            default_ticket["_id"] = str(res.inserted_id)
            docs = [default_ticket]

    return [TicketService.normalize_ticket_data(clean(d)) for d in docs]

@router.post("/tickets", tags=["admin-tickets"])
async def create_ticket(data: TicketCreate, admin=Depends(get_current_admin)):
    doc = data.model_dump()
    if doc.get("categories"):
        doc["categories"] = [c.get("name") if isinstance(c, dict) else str(c) for c in doc["categories"]]
        if not doc.get("category"):
            doc["category"] = doc["categories"][0]
    elif doc.get("category"):
        doc["categories"] = [doc["category"]]
    if doc.get("available") is None:
        doc["available"] = doc["total_quantity"]
    doc["created_at"] = datetime.now(timezone.utc)
    result = await ticket_collection.insert_one(doc)
    await log_admin_action(admin["email"], "create", "ticket", str(result.inserted_id))
    doc["_id"] = str(result.inserted_id)
    return TicketService.normalize_ticket_data(doc)

@router.put("/tickets/{tid}", tags=["admin-tickets"])
async def update_ticket(tid: str, data: TicketUpdate, admin=Depends(get_current_admin)):
    existing = await ticket_collection.find_one({"_id": oid(tid)})
    if not existing:
        raise HTTPException(status_code=404, detail="Ticket not found")

    update = {k: v for k, v in data.model_dump().items() if v is not None}
    if "categories" in update and update["categories"]:
        update["categories"] = [c.get("name") if isinstance(c, dict) else str(c) for c in update["categories"]]
        if "category" not in update:
            update["category"] = update["categories"][0]
    elif "category" in update and update["category"]:
        if "categories" not in update:
            update["categories"] = [update["category"]]
    
    # Keep available in sync when total_quantity is modified without explicit available
    if "total_quantity" in update and "available" not in update:
        old_total = int(existing.get("total_quantity") or existing.get("quantity") or 0)
        old_avail = int(existing.get("available") if existing.get("available") is not None else old_total)
        sold = max(0, old_total - old_avail)
        update["available"] = max(0, int(update["total_quantity"]) - sold)

    update["updated_at"] = datetime.now(timezone.utc)
    await ticket_collection.update_one({"_id": oid(tid)}, {"$set": update})
    await log_admin_action(admin["email"], "update", "ticket", tid)
    doc = await ticket_collection.find_one({"_id": oid(tid)})
    return TicketService.normalize_ticket_data(clean(doc))

@router.delete("/tickets/{tid}", tags=["admin-tickets"])
async def delete_ticket(tid: str, admin=Depends(get_current_admin)):
    result = await ticket_collection.delete_one({"_id": oid(tid)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Ticket not found")
    await log_admin_action(admin["email"], "delete", "ticket", tid)
    return {"message": "Ticket deleted"}

# ════════════════════════════════════════
# CONTEST / VOTING PROMOTIONS
# ════════════════════════════════════════
@router.get("/contest-promotions", tags=["admin-contest"])
async def list_contest_promotions(admin=Depends(get_current_admin)):
    docs = await contest_promotion_collection.find().sort("display_order", 1).to_list(length=50)
    return clean_list(docs)

@router.post("/contest-promotions", tags=["admin-contest"])
async def create_contest_promotion(data: ContestPromotionCreate, admin=Depends(get_current_admin)):
    doc = data.model_dump()
    doc["created_at"] = datetime.now(timezone.utc)
    result = await contest_promotion_collection.insert_one(doc)
    await log_admin_action(admin["email"], "create", "contest_promotion", str(result.inserted_id))
    doc["_id"] = str(result.inserted_id)
    return doc

@router.put("/contest-promotions/{pid}", tags=["admin-contest"])
async def update_contest_promotion(pid: str, data: ContestPromotionUpdate, admin=Depends(get_current_admin)):
    update = {k: v for k, v in data.model_dump().items() if v is not None}
    update["updated_at"] = datetime.now(timezone.utc)
    result = await contest_promotion_collection.update_one({"_id": oid(pid)}, {"$set": update})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Promotion not found")
    await log_admin_action(admin["email"], "update", "contest_promotion", pid)
    doc = await contest_promotion_collection.find_one({"_id": oid(pid)})
    return clean(doc)

@router.delete("/contest-promotions/{pid}", tags=["admin-contest"])
async def delete_contest_promotion(pid: str, admin=Depends(get_current_admin)):
    result = await contest_promotion_collection.delete_one({"_id": oid(pid)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Promotion not found")
    await log_admin_action(admin["email"], "delete", "contest_promotion", pid)
    return {"message": "Promotion deleted"}

@router.patch("/contest-promotions/{pid}/publish", tags=["admin-contest"])
async def toggle_contest_publish(pid: str, data: PublishToggle, admin=Depends(get_current_admin)):
    await contest_promotion_collection.update_one({"_id": oid(pid)}, {"$set": {"is_published": data.is_published}})
    await log_admin_action(admin["email"], "publish" if data.is_published else "unpublish", "contest_promotion", pid)
    return {"message": f"Promotion {'published' if data.is_published else 'unpublished'}"}

# ════════════════════════════════════════
# APP PROMOTION (singleton)
# ════════════════════════════════════════
@router.get("/app-promotion", tags=["admin-app-promo"])
async def get_app_promotion(admin=Depends(get_current_admin)):
    doc = await app_promotion_collection.find_one({"_type": "app_promo"})
    return clean(doc) if doc else {}

@router.put("/app-promotion", tags=["admin-app-promo"])
async def update_app_promotion(data: AppPromotionUpdate, admin=Depends(get_current_admin)):
    update = {k: v for k, v in data.model_dump().items() if v is not None}
    update["updated_at"] = datetime.now(timezone.utc)
    await app_promotion_collection.update_one({"_type": "app_promo"}, {"$set": update}, upsert=True)
    await log_admin_action(admin["email"], "update", "app_promotion")
    doc = await app_promotion_collection.find_one({"_type": "app_promo"})
    return clean(doc)

# ════════════════════════════════════════
# CONTACT INFO (part of website settings)
# ════════════════════════════════════════
@router.get("/contact-info", tags=["admin-contact"])
async def get_contact_info(admin=Depends(get_current_admin)):
    doc = await website_settings_collection.find_one({"_type": "settings"})
    if not doc:
        return {}
    return {
        "contact_email": doc.get("contact_email"),
        "contact_phone": doc.get("contact_phone"),
        "office_address": doc.get("office_address"),
        "support_url": doc.get("support_url"),
        "privacy_policy_url": doc.get("privacy_policy_url"),
        "terms_url": doc.get("terms_url"),
        "social_media": doc.get("social_media"),
    }

@router.put("/contact-info", tags=["admin-contact"])
async def update_contact_info(data: WebsiteSettingsUpdate, VotingPowerSettings, admin=Depends(get_current_admin)):
    update = {k: v for k, v in data.model_dump().items() if v is not None}
    if "social_media" in update and update["social_media"]:
        update["social_media"] = {k: v for k, v in update["social_media"].items() if v is not None}
    update["updated_at"] = datetime.now(timezone.utc)
    await website_settings_collection.update_one({"_type": "settings"}, {"$set": update}, upsert=True)
    await log_admin_action(admin["email"], "update", "contact_info")
    return {"message": "Contact info updated"}

# ════════════════════════════════════════
# IMPORTANT LINKS
# ════════════════════════════════════════
@router.get("/important-links", tags=["admin-links"])
async def list_links(admin=Depends(get_current_admin)):
    docs = await important_link_collection.find().sort("display_order", 1).to_list(length=100)
    return clean_list(docs)

@router.post("/important-links", tags=["admin-links"])
async def create_link(data: ImportantLinkCreate, admin=Depends(get_current_admin)):
    doc = data.model_dump()
    doc["created_at"] = datetime.now(timezone.utc)
    result = await important_link_collection.insert_one(doc)
    await log_admin_action(admin["email"], "create", "important_link", str(result.inserted_id))
    doc["_id"] = str(result.inserted_id)
    return doc

@router.put("/important-links/{lid}", tags=["admin-links"])
async def update_link(lid: str, data: ImportantLinkUpdate, admin=Depends(get_current_admin)):
    update = {k: v for k, v in data.model_dump().items() if v is not None}
    update["updated_at"] = datetime.now(timezone.utc)
    result = await important_link_collection.update_one({"_id": oid(lid)}, {"$set": update})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Link not found")
    await log_admin_action(admin["email"], "update", "important_link", lid)
    doc = await important_link_collection.find_one({"_id": oid(lid)})
    return clean(doc)

@router.delete("/important-links/{lid}", tags=["admin-links"])
async def delete_link(lid: str, admin=Depends(get_current_admin)):
    result = await important_link_collection.delete_one({"_id": oid(lid)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Link not found")
    await log_admin_action(admin["email"], "delete", "important_link", lid)
    return {"message": "Link deleted"}

# ════════════════════════════════════════
# AUDIT LOGS (read-only for admin)
# ════════════════════════════════════════
@router.get("/audit-logs", tags=["admin-audit"])
async def list_audit_logs(skip: int = 0, limit: int = 50, action: Optional[str] = None, resource_type: Optional[str] = None, admin=Depends(get_current_admin)):
    q = {}
    if action:
        q["action"] = action
    if resource_type:
        q["resource_type"] = resource_type
    docs = await audit_log_collection.find(q).sort("timestamp", -1).skip(skip).limit(limit).to_list(length=limit)
    return clean_list(docs)

#  Voting Power Settings 
@router.get("/voting-settings")
async def get_voting_settings(admin: dict = Depends(get_current_admin)):
    settings = await website_settings_collection.find_one({"_id": "voting_settings"})
    if not settings:
        return {"voting_power_website": 1, "voting_power_mobile": 5, "voting_power_paid": 10}
    return clean(settings)

@router.put("/voting-settings")
async def update_voting_settings(req: VotingPowerSettings, admin: dict = Depends(get_current_admin)):
    update_data = req.model_dump()
    update_data["updated_at"] = datetime.now(timezone.utc)
    
    await website_settings_collection.update_one(
        {"_id": "voting_settings"},
        {"$set": update_data},
        upsert=True
    )
    return {"message": "Voting settings updated successfully"}

# ════════════════════════════════════════
# EVENT TICKET BOOKINGS & ATTENDEES (Admin Control)
# ════════════════════════════════════════

async def _fetch_admin_bookings(
    event_id: Optional[str] = None,
    status: Optional[str] = None,
    payment_status: Optional[str] = None,
    payment_method: Optional[str] = None,
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 100
):
    query: Dict[str, Any] = {}
    if event_id:
        query["event_id"] = str(event_id)
    if status:
        query["booking_status"] = status
    if payment_status:
        query["payment_status"] = payment_status.upper()
    if payment_method:
        query["payment_method"] = payment_method.upper()

    matched_emails = []
    if search and search.strip():
        s_term = search.strip()
        s_reg = {"$regex": re.escape(s_term), "$options": "i"}

        # Search user_collection for attendee name, full_name, phone, email
        matched_users = await user_collection.find({
            "$or": [
                {"name": s_reg},
                {"full_name": s_reg},
                {"phone_number": s_reg},
                {"phone": s_reg},
                {"email": s_reg}
            ]
        }).to_list(length=300)
        matched_emails = [u.get("email") for u in matched_users if u.get("email")]

        search_conditions = [
            {"booking_reference": s_reg},
            {"user_email": s_reg},
            {"user_name": s_reg},
            {"user_phone": s_reg},
            {"holder_names": s_reg},
        ]
        if matched_emails:
            search_conditions.append({"user_email": {"$in": matched_emails}})

        query["$or"] = search_conditions

    total = await booking_collection.count_documents(query)
    raw_bookings = await booking_collection.find(query).sort("booked_at", -1).skip(skip).limit(limit).to_list(length=limit)

    event_ids = {b.get("event_id") for b in raw_bookings if b.get("event_id")}
    ticket_ids = {b.get("ticket_id") for b in raw_bookings if b.get("ticket_id")}
    user_emails = {b.get("user_email") for b in raw_bookings if b.get("user_email")}
    booking_id_strs = [str(b["_id"]) for b in raw_bookings]

    events_map = {}
    if event_ids:
        oids = []
        for eid in event_ids:
            try:
                oids.append(ObjectId(eid))
            except Exception:
                pass
        if oids:
            evs = await event_collection.find({"_id": {"$in": oids}}).to_list(length=len(oids))
            events_map = {str(e["_id"]): e for e in evs}

    tickets_map = {}
    if ticket_ids:
        toids = []
        for tid in ticket_ids:
            try:
                toids.append(ObjectId(tid))
            except Exception:
                pass
        if toids:
            tks = await ticket_collection.find({"_id": {"$in": toids}}).to_list(length=len(toids))
            tickets_map = {str(t["_id"]): t for t in tks}

    users_map = {}
    if user_emails:
        usrs = await user_collection.find({"email": {"$in": list(user_emails)}}).to_list(length=len(user_emails))
        users_map = {u.get("email"): u for u in usrs}

    issued_map = {}
    booking_refs = [b.get("booking_reference") for b in raw_bookings if b.get("booking_reference")]
    booking_match = []
    if booking_id_strs:
        booking_match.append({"booking_id": {"$in": booking_id_strs}})
    if booking_refs:
        booking_match.append({"booking_ref": {"$in": booking_refs}})

    if booking_match:
        all_tix = await issued_ticket_collection.find({"$or": booking_match} if len(booking_match) > 1 else booking_match[0]).to_list(length=2000)
        for t in all_tix:
            t["_id"] = str(t["_id"])
            b_id = t.get("booking_id")
            b_ref = t.get("booking_ref")

            t_cash_done = bool(
                t.get("cash_collected")
                or t.get("cash_collect_done")
                or t.get("cash_status") in ("COLLECTED", "APPROVED")
                or t.get("cash_approved")
            )
            is_cash_t = (str(t.get("payment_method", "")).upper() == "CASH" or bool(t.get("cash_payment")))
            t_cash_status = "COLLECTED" if t_cash_done else (t.get("cash_status") or ("PENDING" if is_cash_t else None))
            t_cash_display = "Cash Collect Done" if t_cash_done else ("Cash Pending" if is_cash_t else None)

            t_is_used = bool(t.get("is_checked_in")) or (t.get("status") == "USED")
            ticket_entry = {
                "ticket_ref": t.get("ticket_ref"),
                "holder_name": t.get("holder_name"),
                "holder_email": t.get("holder_email"),
                "ticket_type": t.get("ticket_type"),
                "payment_status": "PAID" if t_cash_done else t.get("payment_status", "PAID"),
                "payment_method": t.get("payment_method", "ONLINE"),
                "is_checked_in": t_is_used,
                "is_fully_used": t_is_used,
                "status": "USED" if t_is_used else t.get("status", "ACTIVE"),
                "status_display": "ALREADY_USED" if t_is_used else "ACTIVE",
                "can_download": not t_is_used,
                "check_in_time": t.get("check_in_time") or t.get("checked_in_at"),
                "checked_in_at": t.get("checked_in_at") or t.get("check_in_time"),
                "checked_in_by": t.get("checked_in_by") or t.get("scanned_by"),
                "checked_in_by_name": t.get("checked_in_by_name"),
                "checked_in_by_role": t.get("checked_in_by_role"),
                "scanned_by": t.get("scanned_by") or t.get("checked_in_by"),
                "cash_status": t_cash_status,
                "cash_collected": t_cash_done,
                "cash_collect_done": t_cash_done,
                "cash_collected_display": t_cash_display,
                "cash_collected_by": t.get("cash_collected_by"),
                "cash_collected_by_name": t.get("cash_collected_by_name"),
                "cash_collected_at": t.get("cash_collected_at"),
            }

            if b_id:
                if b_id not in issued_map:
                    issued_map[b_id] = []
                issued_map[b_id].append(ticket_entry)
            if b_ref:
                if b_ref not in issued_map:
                    issued_map[b_ref] = []
                if not b_id or b_ref != b_id:
                    issued_map[b_ref].append(ticket_entry)

    stats_query = {"event_id": str(event_id)} if event_id else {}
    all_for_stats = await booking_collection.find(stats_query).to_list(length=2000)
    total_tickets = sum(int(b.get("quantity", 1)) for b in all_for_stats)
    total_revenue = sum(
        float(b.get("total_amount", 0.0))
        for b in all_for_stats
        if (b.get("booking_status") == "CONFIRMED" and (b.get("payment_status") in ("PAID", "NOT_REQUIRED", "SUCCESS") or b.get("cash_status") in ("COLLECTED", "APPROVED") or b.get("cash_approved")))
    )
    
    tix_query: Dict[str, Any] = {"event_id": str(event_id)} if event_id else {}
    tix_query["is_checked_in"] = True
    checked_in_count = await issued_ticket_collection.count_documents(tix_query)

    bookings_res = []
    for b in raw_bookings:
        b_id = str(b["_id"])
        ev = events_map.get(str(b.get("event_id")), {})
        tk = tickets_map.get(str(b.get("ticket_id")), {})
        usr = users_map.get(b.get("user_email"), {})
        b_ref = b.get("booking_reference")
        b_tix = issued_map.get(b_id, []) or (issued_map.get(b_ref, []) if b_ref else [])

        raw_p_status = (b.get("payment_status") or "PENDING").upper()
        raw_p_method = (b.get("payment_method") or ("CASH" if b.get("is_cash") or b.get("cash_payment") else "ONLINE")).upper()
        
        b_cash_done = bool(
            b.get("cash_collected")
            or b.get("cash_collect_done")
            or b.get("cash_status") in ("COLLECTED", "APPROVED")
            or b.get("cash_approved")
            or any(t.get("cash_collect_done") for t in b_tix)
        )
        is_cash_b = (raw_p_method == "CASH" or bool(b.get("cash_payment")))
        is_paid = (raw_p_status in ["PAID", "SUCCESS", "NOT_REQUIRED"] or b_cash_done)
        p_status = "PAID" if is_paid else "PENDING"
        p_display = "SUCCESS" if is_paid else "PENDING"

        # Volunteer Check-in tracking
        b_checked_in = bool(b.get("is_checked_in") or any(t.get("is_checked_in") for t in b_tix))
        first_t = b_tix[0] if b_tix else {}
        b_checked_by = b.get("checked_in_by") or b.get("scanned_by") or first_t.get("checked_in_by")
        b_checked_by_name = b.get("checked_in_by_name") or first_t.get("checked_in_by_name")
        b_checked_by_role = b.get("checked_in_by_role") or first_t.get("checked_in_by_role")
        b_check_time = b.get("last_check_in_time") or b.get("checked_in_at") or first_t.get("check_in_time")

        b_cash_by = b.get("cash_collected_by") or first_t.get("cash_collected_by")
        b_cash_by_name = b.get("cash_collected_by_name") or first_t.get("cash_collected_by_name")
        b_cash_at = b.get("cash_collected_at") or first_t.get("cash_collected_at")
        b_cash_status = "COLLECTED" if b_cash_done else (b.get("cash_status") or ("PENDING" if is_cash_b else None))
        b_cash_display = "Cash Collect Done" if b_cash_done else ("Cash Pending" if is_cash_b else None)

        bookings_res.append({
            "_id": b_id,
            "booking_id": b_id,
            "booking_reference": b.get("booking_reference", f"BKG-{b_id[:8]}"),
            "event_id": str(b.get("event_id", "")),
            "event_title": ev.get("title") or b.get("event_title") or "Event",
            "event_date": ev.get("date"),
            "ticket_id": str(b.get("ticket_id", "")),
            "ticket_type": tk.get("type") or b.get("ticket_type") or "General",
            "user_email": b.get("user_email", ""),
            "user_name": usr.get("name") or usr.get("full_name") or b.get("user_name") or "Attendee",
            "user_phone": usr.get("phone_number") or usr.get("phone") or b.get("user_phone") or "",
            "quantity": int(b.get("quantity", 1)),
            "unit_price": float(b.get("unit_price", 0.0)),
            "total_amount": float(b.get("total_amount", 0.0)),
            "booking_status": b.get("booking_status", "CONFIRMED"),
            "payment_status": p_status,
            "payment_status_display": p_display,
            "payment_method": raw_p_method,
            "is_cash": is_cash_b,
            "can_approve_cash": (is_cash_b and not is_paid),
            "cash_approved": bool(b.get("cash_approved") or b_cash_done),
            "cash_status": b_cash_status,
            "cash_collected": b_cash_done,
            "cash_collect_done": b_cash_done,
            "cash_collected_display": b_cash_display,
            "cash_collected_by": b_cash_by,
            "cash_collected_by_name": b_cash_by_name,
            "cash_collected_at": b_cash_at,
            "is_checked_in": b_checked_in,
            "checked_in_by": b_checked_by,
            "checked_in_by_name": b_checked_by_name,
            "checked_in_by_role": b_checked_by_role,
            "scanned_by": b_checked_by,
            "checked_in_at": b_check_time,
            "check_in_time": b_check_time,
            "approved_by": b.get("approved_by") or b_cash_by,
            "approved_at": b.get("approved_at") or b_cash_at,
            "paid_at": b.get("paid_at") or b_cash_at,
            "payment_reference": b.get("payment_reference"),
            "booked_at": b.get("booked_at"),
            "holder_names": b.get("holder_names", []),
            "tickets": b_tix,
        })

    # Also include direct event registrations from registration_collection if querying a specific event or search
    if event_id:
        reg_query: Dict[str, Any] = {"activity_id": str(event_id), "activity_type": "event"}
        if status:
            reg_query["status"] = status
        if payment_status:
            reg_query["payment_status"] = payment_status.upper()
        if payment_method:
            reg_query["payment_method"] = payment_method.upper()
        if search and search.strip():
            s_reg = {"$regex": re.escape(search.strip()), "$options": "i"}
            reg_conds = [
                {"participant_name": s_reg},
                {"participant_email": s_reg},
                {"email": s_reg},
                {"order_reference": s_reg}
            ]
            if matched_emails:
                reg_conds.append({"email": {"$in": matched_emails}})
            reg_query["$or"] = reg_conds

        direct_regs = await registration_collection.find(reg_query).sort("created_at", -1).to_list(length=100)
        existing_refs = {b["booking_reference"] for b in bookings_res}
        for dr in direct_regs:
            r_ref = dr.get("order_reference", f"REG-{str(dr['_id'])[:8]}")
            if r_ref in existing_refs:
                continue
            dr_id = str(dr["_id"])
            dr_user = users_map.get(dr.get("email"), {})
            dr_p_status = (dr.get("payment_status") or "PENDING").upper()
            dr_p_method = (dr.get("payment_method") or ("CASH" if dr.get("cash_payment") else "ONLINE")).upper()
            dr_cash_done = bool(dr.get("cash_collected") or dr.get("cash_collect_done") or dr.get("cash_status") in ("COLLECTED", "APPROVED") or dr.get("cash_approved"))
            dr_is_paid = (dr_p_status in ["PAID", "SUCCESS", "NOT_REQUIRED"] or dr_cash_done)
            dr_checked_in = bool(dr.get("is_present") or dr.get("attendance_status") == "PRESENT" or dr.get("is_checked_in"))
            dr_checked_by = dr.get("checked_in_by") or dr.get("scanned_by") or dr.get("attended_by")
            dr_check_time = dr.get("checked_in_at") or dr.get("last_check_in_time") or dr.get("attended_at")
            dr_cash_by = dr.get("cash_collected_by") or dr.get("approved_by") or dr_checked_by
            dr_cash_at = dr.get("cash_collected_at") or dr.get("approved_at") or dr.get("paid_at") or dr_check_time

            bookings_res.append({
                "_id": dr_id,
                "booking_id": dr_id,
                "booking_reference": r_ref,
                "event_id": str(event_id),
                "event_title": events_map.get(str(event_id), {}).get("title", "Event"),
                "event_date": events_map.get(str(event_id), {}).get("date"),
                "ticket_id": "",
                "ticket_type": "Event Registration",
                "user_email": dr.get("email") or dr.get("participant_email", ""),
                "user_name": dr.get("participant_name") or dr_user.get("name") or dr_user.get("full_name") or "Participant",
                "user_phone": dr_user.get("phone_number") or dr_user.get("phone") or "",
                "quantity": 1,
                "unit_price": float(dr.get("payable_amount", 0.0)),
                "total_amount": float(dr.get("payable_amount", 0.0)),
                "booking_status": dr.get("status", "CONFIRMED"),
                "payment_status": "PAID" if dr_is_paid else "PENDING",
                "payment_status_display": "SUCCESS" if dr_is_paid else "PENDING",
                "payment_method": dr_p_method,
                "is_cash": (dr_p_method == "CASH"),
                "can_approve_cash": (dr_p_method == "CASH" and not dr_is_paid),
                "cash_approved": bool(dr.get("cash_approved") or dr_cash_done),
                "cash_status": "COLLECTED" if dr_cash_done else (dr.get("cash_status") or ("PENDING" if dr_p_method == "CASH" else None)),
                "cash_collected": dr_cash_done,
                "cash_collect_done": dr_cash_done,
                "cash_collected_display": "Cash Collect Done" if dr_cash_done else ("Cash Pending" if dr_p_method == "CASH" else None),
                "cash_collected_by": dr_cash_by,
                "cash_collected_by_name": dr.get("cash_collected_by_name"),
                "cash_collected_at": dr_cash_at,
                "is_checked_in": dr_checked_in,
                "checked_in_by": dr_checked_by,
                "checked_in_by_name": dr.get("checked_in_by_name"),
                "checked_in_by_role": dr.get("checked_in_by_role"),
                "scanned_by": dr_checked_by,
                "checked_in_at": dr_check_time,
                "check_in_time": dr_check_time,
                "approved_by": dr.get("approved_by") or dr_cash_by,
                "approved_at": dr.get("approved_at") or dr_cash_at,
                "paid_at": dr.get("paid_at") or dr_cash_at,
                "payment_reference": dr.get("payment_reference"),
                "booked_at": dr.get("created_at"),
                "holder_names": [dr.get("participant_name") or "Participant"],
                "tickets": [],
            })

    return {
        "total": len(bookings_res) if event_id else total,
        "total_tickets": total_tickets,
        "total_revenue": total_revenue,
        "checked_in_count": checked_in_count,
        "skip": skip,
        "limit": limit,
        "bookings": bookings_res,
    }

@router.get("/events/{event_id}/bookings", tags=["admin-bookings"])
@router.get("/events/{event_id}/registrations", tags=["admin-bookings"])
@router.get("/events/{event_id}/participants", tags=["admin-bookings"])
@router.get("/events/{event_id}/attendees", tags=["admin-bookings"])
async def list_admin_event_bookings(
    event_id: str,
    status: Optional[str] = None,
    payment_status: Optional[str] = None,
    payment_method: Optional[str] = None,
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    admin=Depends(get_current_admin)
):
    """Super Admin retrieves participants and ticket bookings for a specific event with customer and attendee details, searchable by name, email, phone, reference."""
    return await _fetch_admin_bookings(
        event_id=event_id,
        status=status,
        payment_status=payment_status,
        payment_method=payment_method,
        search=search,
        skip=skip,
        limit=limit
    )

@router.get("/bookings", tags=["admin-bookings"])
@router.get("/registrations", tags=["admin-bookings"])
@router.get("/participants", tags=["admin-bookings"])
async def list_all_admin_bookings(
    event_id: Optional[str] = None,
    status: Optional[str] = None,
    payment_status: Optional[str] = None,
    payment_method: Optional[str] = None,
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    admin=Depends(get_current_admin)
):
    """Super Admin retrieves all ticket bookings and registrations across events with metrics, filters, and search."""
    return await _fetch_admin_bookings(
        event_id=event_id,
        status=status,
        payment_status=payment_status,
        payment_method=payment_method,
        search=search,
        skip=skip,
        limit=limit
    )

@router.get("/bookings/{booking_id}", tags=["admin-bookings"])
async def get_admin_booking_detail(booking_id: str, admin=Depends(get_current_admin)):
    """Super Admin retrieves comprehensive details for an individual ticket booking."""
    b = None
    try:
        b = await booking_collection.find_one({"_id": ObjectId(booking_id)})
    except Exception:
        pass
    if not b:
        b = await booking_collection.find_one({"$or": [{"_id": booking_id}, {"booking_reference": booking_id}]})
    if not b:
        raise HTTPException(status_code=404, detail="Booking not found")

    b_id_str = str(b["_id"])
    b["_id"] = b_id_str
    b["booking_id"] = b_id_str

    user = await user_collection.find_one({"email": b.get("user_email")})
    b["user_name"] = (user.get("name") if user else None) or b.get("user_name") or "Attendee"
    b["user_phone"] = (user.get("phone_number") or user.get("phone") if user else None) or b.get("user_phone") or ""

    if b.get("event_id"):
        try:
            ev = await event_collection.find_one({"_id": ObjectId(b["event_id"])})
            if ev:
                b["event_title"] = ev.get("title")
                b["event_date"] = ev.get("date")
                b["event_venue"] = ev.get("venue")
        except Exception:
            pass

    if b.get("ticket_id"):
        try:
            tk = await ticket_collection.find_one({"_id": ObjectId(b["ticket_id"])})
            if tk:
                b["ticket_type"] = tk.get("type")
        except Exception:
            pass

    tix = await issued_ticket_collection.find({"booking_id": b_id_str}).to_list(length=100)
    for t in tix:
        t["_id"] = str(t["_id"])
        t_cash_done = bool(
            t.get("cash_collected")
            or t.get("cash_collect_done")
            or t.get("cash_status") in ("COLLECTED", "APPROVED")
            or t.get("cash_approved")
        )
        is_cash_t = (str(t.get("payment_method", "")).upper() == "CASH" or bool(t.get("cash_payment")))
        t["cash_status"] = "COLLECTED" if t_cash_done else (t.get("cash_status") or ("PENDING" if is_cash_t else None))
        t["cash_collected"] = t_cash_done
        t["cash_collect_done"] = t_cash_done
        t["cash_collected_display"] = "Cash Collect Done" if t_cash_done else ("Cash Pending" if is_cash_t else None)
        t["checked_in_by"] = t.get("checked_in_by") or t.get("scanned_by")
        t["scanned_by"] = t.get("scanned_by") or t.get("checked_in_by")
    b["tickets"] = tix

    raw_p_status = (b.get("payment_status") or "PENDING").upper()
    raw_p_method = (b.get("payment_method") or ("CASH" if b.get("is_cash") or b.get("cash_payment") else "ONLINE")).upper()
    b_cash_done = bool(
        b.get("cash_collected")
        or b.get("cash_collect_done")
        or b.get("cash_status") in ("COLLECTED", "APPROVED")
        or b.get("cash_approved")
        or any(t.get("cash_collect_done") for t in tix)
    )
    is_cash_b = (raw_p_method == "CASH" or bool(b.get("cash_payment")))
    is_paid = (raw_p_status in ["PAID", "SUCCESS", "NOT_REQUIRED"] or b_cash_done)
    b["payment_status"] = "PAID" if is_paid else "PENDING"
    b["payment_status_display"] = "SUCCESS" if is_paid else "PENDING"
    b["payment_method"] = raw_p_method
    b["is_cash"] = is_cash_b
    b["can_approve_cash"] = (is_cash_b and not is_paid)
    b["cash_approved"] = bool(b.get("cash_approved") or b_cash_done)
    b["cash_status"] = "COLLECTED" if b_cash_done else (b.get("cash_status") or ("PENDING" if is_cash_b else None))
    b["cash_collected"] = b_cash_done
    b["cash_collect_done"] = b_cash_done
    b["cash_collected_display"] = "Cash Collect Done" if b_cash_done else ("Cash Pending" if is_cash_b else None)

    b_checked_in = bool(b.get("is_checked_in") or any(t.get("is_checked_in") for t in tix))
    first_t = tix[0] if tix else {}
    b["is_checked_in"] = b_checked_in
    b["checked_in_by"] = b.get("checked_in_by") or b.get("scanned_by") or first_t.get("checked_in_by")
    b["checked_in_by_name"] = b.get("checked_in_by_name") or first_t.get("checked_in_by_name")
    b["checked_in_by_role"] = b.get("checked_in_by_role") or first_t.get("checked_in_by_role")
    b["scanned_by"] = b["checked_in_by"]
    b["checked_in_at"] = b.get("checked_in_at") or b.get("last_check_in_time") or first_t.get("check_in_time")
    b["check_in_time"] = b["checked_in_at"]
    b["cash_collected_by"] = b.get("cash_collected_by") or first_t.get("cash_collected_by")
    b["cash_collected_by_name"] = b.get("cash_collected_by_name") or first_t.get("cash_collected_by_name")
    b["cash_collected_at"] = b.get("cash_collected_at") or first_t.get("cash_collected_at")

    return b


# ════════════════════════════════════════════════════════════
# ADMIN CASH PAYMENT APPROVAL CONTROLS (SPOT / EVENT-DAY CASH)
# ════════════════════════════════════════════════════════════

class CashApprovalPayload(BaseModel):
    notes: Optional[str] = None
    amount_received: Optional[float] = None


@router.post("/bookings/{booking_id}/approve-cash", tags=["admin-bookings"])
@router.patch("/bookings/{booking_id}/approve-cash", tags=["admin-bookings"])
@router.post("/events/{event_id}/bookings/{booking_id}/approve-cash", tags=["admin-bookings"])
@router.patch("/events/{event_id}/bookings/{booking_id}/approve-cash", tags=["admin-bookings"])
@router.post("/registrations/{booking_id}/approve-cash", tags=["admin-bookings"])
@router.patch("/registrations/{booking_id}/approve-cash", tags=["admin-bookings"])
@router.post("/events/{event_id}/registrations/{booking_id}/approve-cash", tags=["admin-bookings"])
@router.patch("/events/{event_id}/registrations/{booking_id}/approve-cash", tags=["admin-bookings"])
async def approve_admin_cash_payment(
    booking_id: str,
    event_id: Optional[str] = None,
    payload: Optional[CashApprovalPayload] = None,
    admin: dict = Depends(get_current_admin)
):
    """
    Super Admin verifies and approves an offline cash payment for an attendee on event day.
    Updates payment_status to 'PAID' (display: 'SUCCESS'), payment_method to 'CASH', records
    audit metadata (admin email, timestamp), updates digital tickets, and dispatches confirmation notification.
    """
    notes = payload.notes if payload else None
    admin_email = admin.get("email", "admin")
    now_utc = datetime.now(timezone.utc)

    # 1. Check in booking_collection
    b = None
    try:
        b = await booking_collection.find_one({"_id": ObjectId(booking_id)})
    except Exception:
        pass
    if not b:
        b = await booking_collection.find_one({"$or": [{"_id": booking_id}, {"booking_reference": booking_id}]})

    if b:
        payment_ref = f"CASH-{admin_email.split('@')[0]}-{int(now_utc.timestamp())}"
        await booking_collection.update_one(
            {"_id": b["_id"]},
            {"$set": {
                "payment_status": "PAID",
                "payment_status_display": "SUCCESS",
                "payment_method": "CASH",
                "booking_status": "CONFIRMED",
                "cash_status": "APPROVED",
                "cash_approved": True,
                "cash_received": True,
                "paid_at": now_utc,
                "approved_at": now_utc,
                "approved_by": admin_email,
                "payment_reference": payment_ref,
                "admin_notes": notes
            }}
        )
        # Update all issued tickets for this booking
        await issued_ticket_collection.update_many(
            {"booking_id": str(b["_id"])},
            {"$set": {
                "payment_status": "PAID",
                "payment_method": "CASH",
                "is_paid": True,
                "cash_approved": True
            }}
        )

        # Dispatch confirmation notification
        try:
            from app.services.notification_service import NotificationService
            from app.core.notification_types import NotificationType, NotificationPriority, SoundCategory
            from app.schemas.notification import NotificationCreate

            total_amt = float(b.get("total_amount", 0.0))
            await NotificationService.create_notification(NotificationCreate(
                recipient_user_id=b.get("user_email"),
                notification_type=NotificationType.PAYMENT_CONFIRMATION,
                title="Cash Payment Approved",
                message=f"Your cash payment of ₹{total_amt:.2f} for booking {b.get('booking_reference')} has been verified and approved.",
                resource_type="booking",
                resource_id=str(b["_id"]),
                action_url="/dashboard?tab=events",
                priority=NotificationPriority.HIGH,
                sound_category=SoundCategory.SUCCESS,
                deduplication_key=f"cash_appr_{b['_id']}",
                source_module="bookings",
                source_event_id=b.get("booking_reference")
            ))
        except Exception:
            pass

        return {
            "message": "Cash payment approved successfully",
            "booking_id": str(b["_id"]),
            "booking_reference": b.get("booking_reference"),
            "payment_status": "PAID",
            "payment_status_display": "SUCCESS",
            "payment_method": "CASH",
            "total_amount": float(b.get("total_amount", 0.0)),
            "approved_by": admin_email,
            "approved_at": now_utc.isoformat(),
            "cash_approved": True
        }

    # 2. Check in registration_collection
    reg = None
    try:
        reg = await registration_collection.find_one({"_id": ObjectId(booking_id)})
    except Exception:
        pass
    if not reg:
        reg = await registration_collection.find_one({"$or": [{"_id": booking_id}, {"order_reference": booking_id}]})

    if reg:
        payment_ref = f"CASH-{admin_email.split('@')[0]}-{int(now_utc.timestamp())}"
        await registration_collection.update_one(
            {"_id": reg["_id"]},
            {"$set": {
                "payment_status": "PAID",
                "payment_status_display": "SUCCESS",
                "payment_method": "CASH",
                "status": "CONFIRMED",
                "cash_status": "APPROVED",
                "cash_approved": True,
                "cash_received": True,
                "paid_at": now_utc,
                "approved_at": now_utc,
                "approved_by": admin_email,
                "payment_reference": payment_ref,
                "admin_notes": notes
            }}
        )

        try:
            from app.services.wallet_service import WalletService
            await WalletService.commit_reserved_coins(reg.get("order_reference"))
        except Exception:
            pass

        # Dispatch notification
        try:
            from app.services.notification_service import NotificationService
            from app.core.notification_types import NotificationType, NotificationPriority, SoundCategory
            from app.schemas.notification import NotificationCreate

            total_amt = float(reg.get("payable_amount", 0.0))
            await NotificationService.create_notification(NotificationCreate(
                recipient_user_id=reg.get("email"),
                notification_type=NotificationType.PAYMENT_CONFIRMATION,
                title="Cash Payment Approved",
                message=f"Your cash payment of ₹{total_amt:.2f} for registration has been verified and approved.",
                resource_type="registration",
                resource_id=str(reg["_id"]),
                action_url="/dashboard",
                priority=NotificationPriority.HIGH,
                sound_category=SoundCategory.SUCCESS,
                deduplication_key=f"cash_appr_reg_{reg['_id']}",
                source_module="registrations",
                source_event_id=reg.get("order_reference")
            ))
        except Exception:
            pass

        return {
            "message": "Cash payment approved successfully",
            "registration_id": str(reg["_id"]),
            "order_reference": reg.get("order_reference"),
            "payment_status": "PAID",
            "payment_status_display": "SUCCESS",
            "payment_method": "CASH",
            "total_amount": float(reg.get("payable_amount", 0.0)),
            "approved_by": admin_email,
            "approved_at": now_utc.isoformat(),
            "cash_approved": True
        }

    # 3. Check in competition_registration_collection
    creg = None
    try:
        creg = await competition_registration_collection.find_one({"_id": ObjectId(booking_id)})
    except Exception:
        pass
    if not creg:
        creg = await competition_registration_collection.find_one({"$or": [{"_id": booking_id}, {"registration_reference": booking_id}]})

    if creg:
        payment_ref = f"CASH-{admin_email.split('@')[0]}-{int(now_utc.timestamp())}"
        await competition_registration_collection.update_one(
            {"_id": creg["_id"]},
            {"$set": {
                "payment_status": "PAID",
                "payment_status_display": "SUCCESS",
                "payment_method": "CASH",
                "status": "CONFIRMED",
                "cash_status": "APPROVED",
                "cash_approved": True,
                "cash_received": True,
                "paid_at": now_utc,
                "approved_at": now_utc,
                "approved_by": admin_email,
                "payment_reference": payment_ref,
                "admin_notes": notes
            }}
        )
        return {
            "message": "Cash payment approved successfully",
            "registration_id": str(creg["_id"]),
            "registration_reference": creg.get("registration_reference"),
            "payment_status": "PAID",
            "payment_status_display": "SUCCESS",
            "payment_method": "CASH",
            "total_amount": float(creg.get("fee", 0.0)),
            "approved_by": admin_email,
            "approved_at": now_utc.isoformat(),
            "cash_approved": True
        }

    raise HTTPException(status_code=404, detail="Registration or booking not found")


# ════════════════════════════════════════════════════════════
# OFFLINE EVENT ATTENDANCE & PARTICIPANT PRESENCE MANAGEMENT
# ════════════════════════════════════════════════════════════

@router.get("/events/{event_id}/attendance", tags=["admin-attendance"], response_model=EventAttendanceDashboardResponse)
async def get_event_attendance_dashboard(event_id: str, admin=Depends(get_current_admin)):
    """
    Returns the complete attendance roster for an offline event.
    Displays all registered attendees with presence tick status,
    total registered, present count, absent count, and final present list.
    """
    clean_eid = str(event_id).strip()
    ev = None
    if ObjectId.is_valid(clean_eid):
        ev = await event_collection.find_one({"_id": ObjectId(clean_eid), "is_deleted": {"$ne": True}})
    if not ev:
        ev = await event_collection.find_one({"$or": [{"_id": clean_eid}, {"id": clean_eid}], "is_deleted": {"$ne": True}})
    if not ev:
        raise HTTPException(status_code=404, detail="Event not found")

    ev_match = [clean_eid]
    if ObjectId.is_valid(clean_eid):
        ev_match.append(ObjectId(clean_eid))

    # Fetch all bookings for this event
    bookings = await booking_collection.find({"event_id": {"$in": ev_match}}).sort("booked_at", -1).to_list(length=3000)

    # Also fetch direct registrations
    direct_regs = await registration_collection.find({
        "activity_id": {"$in": ev_match},
        "activity_type": "event"
    }).sort("created_at", -1).to_list(length=1000)

    emails = list({
        (b.get("user_email") or b.get("email") or "").strip().lower()
        for b in (bookings + direct_regs)
        if (b.get("user_email") or b.get("email"))
    })
    user_map = {}
    if emails:
        usrs = await user_collection.find({"email": {"$in": emails}}).to_list(length=len(emails) * 2)
        user_map = {(u.get("email") or "").strip().lower(): u for u in usrs}

    all_attendees: List[Dict[str, Any]] = []
    final_present: List[Dict[str, Any]] = []
    seen_refs = set()

    for b in bookings:
        b_id = str(b["_id"])
        ref = b.get("booking_reference") or f"BKG-{b_id[:8]}"
        if ref in seen_refs:
            continue
        seen_refs.add(ref)

        em = (b.get("user_email") or "").strip().lower()
        usr = user_map.get(em, {})
        name = usr.get("full_name") or usr.get("name") or b.get("user_name") or (em.split("@")[0].title() if em else "Attendee")
        phone = usr.get("phone_number") or usr.get("phone") or b.get("user_phone") or ""

        is_p = bool(b.get("is_present", False) or b.get("attendance_status") == "PRESENT" or b.get("is_checked_in"))
        stat = "PRESENT" if is_p else b.get("attendance_status", "ABSENT")
        b_cash_done = bool(b.get("cash_collected") or b.get("cash_collect_done") or b.get("cash_status") in ("COLLECTED", "APPROVED") or b.get("cash_approved"))
        is_cash_b = (str(b.get("payment_method", "")).upper() == "CASH" or bool(b.get("cash_payment")))
        b_checked_by = b.get("checked_in_by") or b.get("scanned_by") or b.get("attended_by")
        b_check_time = b.get("checked_in_at") or b.get("last_check_in_time") or b.get("attended_at")
        b_cash_by = b.get("cash_collected_by") or b_checked_by
        b_cash_at = b.get("cash_collected_at") or b.get("paid_at") or b_check_time

        item = {
            "booking_id": b_id,
            "registration_id": b_id,
            "booking_reference": ref,
            "event_id": clean_eid,
            "event_title": ev.get("title", "Event"),
            "user_email": em,
            "user_name": name,
            "user_phone": phone,
            "ticket_type": b.get("ticket_type") or b.get("category") or "General",
            "quantity": int(b.get("quantity", 1)),
            "payment_status": "PAID" if b_cash_done else b.get("payment_status", "PAID"),
            "payment_method": b.get("payment_method", "ONLINE"),
            "is_present": is_p,
            "attendance_status": stat,
            "is_checked_in": bool(b.get("is_checked_in")),
            "checked_in_by": b_checked_by,
            "checked_in_by_name": b.get("checked_in_by_name"),
            "checked_in_by_role": b.get("checked_in_by_role"),
            "scanned_by": b_checked_by,
            "checked_in_at": b_check_time,
            "check_in_time": b_check_time,
            "attended_at": b.get("attended_at") or b_check_time,
            "attended_by": b.get("attended_by") or b_checked_by,
            "cash_status": "COLLECTED" if b_cash_done else (b.get("cash_status") or ("PENDING" if is_cash_b else None)),
            "cash_collected": b_cash_done,
            "cash_collect_done": b_cash_done,
            "cash_collected_display": "Cash Collect Done" if b_cash_done else ("Cash Pending" if is_cash_b else None),
            "cash_collected_by": b_cash_by,
            "cash_collected_by_name": b.get("cash_collected_by_name"),
            "cash_collected_at": b_cash_at,
            "booked_at": b.get("booked_at") or b.get("created_at")
        }
        all_attendees.append(item)
        if is_p:
            final_present.append(item)

    for dr in direct_regs:
        dr_id = str(dr["_id"])
        ref = dr.get("order_reference") or f"REG-{dr_id[:8]}"
        if ref in seen_refs:
            continue
        seen_refs.add(ref)

        em = (dr.get("email") or dr.get("participant_email") or "").strip().lower()
        usr = user_map.get(em, {})
        name = dr.get("participant_name") or usr.get("full_name") or usr.get("name") or (em.split("@")[0].title() if em else "Participant")
        phone = usr.get("phone_number") or usr.get("phone") or ""

        is_p = bool(dr.get("is_present", False) or dr.get("attendance_status") == "PRESENT" or dr.get("is_checked_in"))
        stat = "PRESENT" if is_p else dr.get("attendance_status", "ABSENT")
        dr_cash_done = bool(dr.get("cash_collected") or dr.get("cash_collect_done") or dr.get("cash_status") in ("COLLECTED", "APPROVED") or dr.get("cash_approved"))
        is_cash_dr = (str(dr.get("payment_method", "")).upper() == "CASH" or bool(dr.get("cash_payment")))
        dr_checked_by = dr.get("checked_in_by") or dr.get("scanned_by") or dr.get("attended_by")
        dr_check_time = dr.get("checked_in_at") or dr.get("last_check_in_time") or dr.get("attended_at")
        dr_cash_by = dr.get("cash_collected_by") or dr_checked_by
        dr_cash_at = dr.get("cash_collected_at") or dr.get("paid_at") or dr_check_time

        item = {
            "booking_id": dr_id,
            "registration_id": dr_id,
            "booking_reference": ref,
            "event_id": clean_eid,
            "event_title": ev.get("title", "Event"),
            "user_email": em,
            "user_name": name,
            "user_phone": phone,
            "ticket_type": dr.get("category") or "Event Registration",
            "quantity": 1,
            "payment_status": "PAID" if dr_cash_done else dr.get("payment_status", "PAID"),
            "payment_method": dr.get("payment_method", "ONLINE"),
            "is_present": is_p,
            "attendance_status": stat,
            "is_checked_in": bool(dr.get("is_checked_in")),
            "checked_in_by": dr_checked_by,
            "checked_in_by_name": dr.get("checked_in_by_name"),
            "checked_in_by_role": dr.get("checked_in_by_role"),
            "scanned_by": dr_checked_by,
            "checked_in_at": dr_check_time,
            "check_in_time": dr_check_time,
            "attended_at": dr.get("attended_at") or dr_check_time,
            "attended_by": dr.get("attended_by") or dr_checked_by,
            "cash_status": "COLLECTED" if dr_cash_done else (dr.get("cash_status") or ("PENDING" if is_cash_dr else None)),
            "cash_collected": dr_cash_done,
            "cash_collect_done": dr_cash_done,
            "cash_collected_display": "Cash Collect Done" if dr_cash_done else ("Cash Pending" if is_cash_dr else None),
            "cash_collected_by": dr_cash_by,
            "cash_collected_by_name": dr.get("cash_collected_by_name"),
            "cash_collected_at": dr_cash_at,
            "booked_at": dr.get("created_at")
        }
        all_attendees.append(item)
        if is_p:
            final_present.append(item)

    def _parse_ts(val: Any) -> float:
        if not val:
            return float('inf')
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, str):
            try:
                val = datetime.fromisoformat(val.replace("Z", "+00:00"))
            except Exception:
                return float('inf')
        if hasattr(val, "timestamp"):
            try:
                return val.timestamp()
            except Exception:
                return float('inf')
        return float('inf')

    final_present.sort(key=lambda x: (
        _parse_ts(x.get("attended_at")),
        _parse_ts(x.get("booked_at"))
    ))

    present_count = len(final_present)
    absent_count = len(all_attendees) - present_count

    return EventAttendanceDashboardResponse(
        event_id=clean_eid,
        event_title=ev.get("title", "Event"),
        venue=ev.get("venue"),
        total_registered=len(all_attendees),
        present_count=present_count,
        absent_count=absent_count,
        final_present_list=final_present,
        all_attendees=all_attendees
    )


@router.patch("/events/{event_id}/attendance/{booking_id}", tags=["admin-attendance"])
@router.patch("/bookings/{booking_id}/attendance", tags=["admin-attendance"])
async def update_event_attendance(
    booking_id: str,
    req: AttendanceUpdateReq,
    event_id: Optional[str] = None,
    admin=Depends(get_current_admin)
):
    """
    Admin toggles presence tick for an event attendee on event day.
    Marks attendee as PRESENT (tick) or ABSENT (untick).
    """
    clean_bid = str(booking_id).strip()
    is_present = bool(req.is_present)
    stat = req.attendance_status or ("PRESENT" if is_present else "ABSENT")
    now = datetime.now(timezone.utc)
    admin_email = admin.get("email", "admin@upgradetalentsindia.in")

    update_fields = {
        "is_present": is_present,
        "attendance_status": stat,
        "updated_at": now
    }
    if is_present:
        update_fields["attended_at"] = now
        update_fields["attended_by"] = admin_email
    else:
        update_fields["attended_at"] = None
        update_fields["attended_by"] = None

    if req.notes:
        update_fields["attendance_notes"] = req.notes

    # 1. Look in booking_collection
    b_query = [{"booking_reference": clean_bid}, {"user_email": clean_bid.lower()}]
    if ObjectId.is_valid(clean_bid):
        b_query.insert(0, {"_id": ObjectId(clean_bid)})
    else:
        b_query.insert(0, {"_id": clean_bid})

    b = await booking_collection.find_one({"$or": b_query})
    matched_type = "booking"

    if b:
        is_cash_booking = (
            str(b.get("payment_method", "")).upper() in ["CASH", "OFFLINE"]
            or bool(b.get("cash_payment"))
        )
        if not is_present:
            update_fields["admitted_count"] = 0
            update_fields["checked_in_by"] = None
            update_fields["scanned_by"] = None
            if is_cash_booking:
                update_fields["cash_status"] = "PENDING"
                update_fields["cash_collected"] = False
                update_fields["cash_collect_done"] = False
                update_fields["payment_status"] = "PENDING"

        await booking_collection.update_one({"_id": b["_id"]}, {"$set": update_fields})

        # Sync with issued tickets
        t_update: Dict[str, Any] = {
            "is_checked_in": is_present,
            "check_in_time": now if is_present else None,
            "checked_in_at": now if is_present else None,
            "status": "USED" if is_present else "ACTIVE",
        }
        if not is_present:
            t_update["admitted_count"] = 0
            t_update["checked_in_by"] = None
            t_update["checked_in_by_name"] = None
            t_update["scanned_by"] = None
            if is_cash_booking:
                t_update["cash_status"] = "PENDING"
                t_update["cash_collected"] = False
                t_update["cash_collect_done"] = False
                t_update["payment_status"] = "PENDING"
        else:
            t_update["admitted_count"] = int(b.get("quantity") or 1)
            t_update["checked_in_by"] = admin_email
            t_update["checked_in_by_name"] = "Admin"

        await issued_ticket_collection.update_many(
            {"booking_id": str(b["_id"])},
            {"$set": t_update}
        )
        target_doc = b
    else:
        # 2. Look in registration_collection
        reg = await registration_collection.find_one({
            "$or": [
                {"_id": ObjectId(clean_bid)} if ObjectId.is_valid(clean_bid) else {"_id": clean_bid},
                {"order_reference": clean_bid},
                {"email": clean_bid.lower()}
            ]
        })
        if not reg:
            raise HTTPException(status_code=404, detail="Attendee booking or registration not found.")
        await registration_collection.update_one({"_id": reg["_id"]}, {"$set": update_fields})
        target_doc = reg
        matched_type = "registration"

    # Audit log
    await log_admin_action(
        admin_email,
        "mark_attendance" if is_present else "unmark_attendance",
        "event_booking",
        str(target_doc["_id"]),
        metadata={"is_present": is_present, "status": stat}
    )

    p_name = target_doc.get("user_name") or target_doc.get("participant_name") or (target_doc.get("user_email") or target_doc.get("email") or "").split("@")[0].title()
    status_label = "marked PRESENT (tick)" if is_present else "marked ABSENT (untick)"

    return {
        "success": True,
        "message": f"Attendee {p_name} {status_label} successfully.",
        "booking_id": str(target_doc["_id"]),
        "is_present": is_present,
        "attendance_status": stat,
        "attended_at": now.isoformat() if is_present else None,
        "attended_by": admin_email if is_present else None
    }


@router.post("/events/{event_id}/attendance/bulk", tags=["admin-attendance"])
async def bulk_update_event_attendance(
    event_id: str,
    req: BulkAttendanceUpdateReq,
    admin=Depends(get_current_admin)
):
    """
    Bulk marks attendance for multiple event attendees.
    """
    clean_eid = str(event_id).strip()
    is_present = bool(req.is_present)
    stat = req.attendance_status or ("PRESENT" if is_present else "ABSENT")
    now = datetime.now(timezone.utc)
    admin_email = admin.get("email", "admin@upgradetalentsindia.in")

    target_ids = []
    for rid in req.registration_ids:
        s_rid = str(rid).strip()
        target_ids.append(s_rid)
        if ObjectId.is_valid(s_rid):
            target_ids.append(ObjectId(s_rid))

    update_fields = {
        "is_present": is_present,
        "attendance_status": stat,
        "updated_at": now
    }
    if is_present:
        update_fields["attended_at"] = now
        update_fields["attended_by"] = admin_email
    else:
        update_fields["attended_at"] = None
        update_fields["attended_by"] = None

    ev_match = [clean_eid]
    if ObjectId.is_valid(clean_eid):
        ev_match.append(ObjectId(clean_eid))

    res1 = await booking_collection.update_many(
        {
            "event_id": {"$in": ev_match},
            "$or": [
                {"_id": {"$in": target_ids}},
                {"booking_reference": {"$in": [str(x) for x in req.registration_ids]}},
                {"user_email": {"$in": [str(x).lower() for x in req.registration_ids]}}
            ]
        },
        {"$set": update_fields}
    )

    res2 = await registration_collection.update_many(
        {
            "activity_id": {"$in": ev_match},
            "$or": [
                {"_id": {"$in": target_ids}},
                {"order_reference": {"$in": [str(x) for x in req.registration_ids]}},
                {"email": {"$in": [str(x).lower() for x in req.registration_ids]}}
            ]
        },
        {"$set": update_fields}
    )

    total_modified = res1.modified_count + res2.modified_count

    return {
        "success": True,
        "message": f"Bulk attendance updated: {total_modified} attendees marked {stat}.",
        "updated_count": total_modified,
        "is_present": is_present,
        "attendance_status": stat
    }


# ── EVENT VOLUNTEERS INTEGRATION ────────────────────────────────────

@router.get("/events/{event_id}/volunteers", tags=["admin-volunteers"])
async def cms_get_event_volunteers(event_id: str, admin=Depends(get_current_admin)):
    """Retrieve all assigned and available volunteers for an event."""
    from app.api.routes.admin_volunteers import get_event_volunteers
    return await get_event_volunteers(event_id, admin)


@router.post("/events/{event_id}/volunteers", tags=["admin-volunteers"])
async def cms_assign_event_volunteers(
    event_id: str,
    req: dict,
    admin=Depends(get_current_admin)
):
    """Assign volunteers to an event and email them their credentials."""
    from app.api.routes.admin_volunteers import assign_event_volunteers, AssignEventVolunteersReq
    parsed_req = AssignEventVolunteersReq(**req)
    return await assign_event_volunteers(event_id, parsed_req, admin)


@router.delete("/events/{event_id}/volunteers/{email}", tags=["admin-volunteers"])
async def cms_remove_event_volunteer(
    event_id: str,
    email: str,
    admin=Depends(get_current_admin)
):
    """Unassign volunteer from an event."""
    from app.api.routes.admin_volunteers import remove_event_volunteer
    return await remove_event_volunteer(event_id, email, admin)


@router.post("/events/{event_id}/volunteers/{email}/resend-credentials", tags=["admin-volunteers"])
async def cms_resend_event_volunteer_credentials(
    event_id: str,
    email: str,
    admin=Depends(get_current_admin)
):
    """Resend credentials and gate pass to an assigned volunteer."""
    from app.api.routes.admin_volunteers import resend_event_volunteer_credentials
    return await resend_event_volunteer_credentials(event_id, email, admin)


@router.get("/events/{event_id}/volunteer-report", tags=["admin-volunteers"])
@router.get("/events/{event_id}/volunteers/report", tags=["admin-volunteers"])
async def cms_get_event_volunteer_report(event_id: str, admin=Depends(get_current_admin)):
    """Retrieve complete volunteer-wise entry and cash collection audit report for an event."""
    from app.api.routes.admin_volunteers import get_event_volunteer_report
    return await get_event_volunteer_report(event_id, admin)
