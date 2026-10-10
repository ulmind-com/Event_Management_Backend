from fastapi import APIRouter, Query
from typing import Optional
from datetime import datetime, timezone
from app.core.database import (
    website_settings_collection, event_collection, live_event_collection,
    ticket_collection, banner_collection, testimonial_collection,
    course_collection, contest_promotion_collection, app_promotion_collection,
    important_link_collection,
)

router = APIRouter()

def clean(doc):
    if doc and "_id" in doc:
        doc["_id"] = str(doc["_id"])
    return doc

def clean_list(docs):
    for d in docs:
        d["_id"] = str(d["_id"])
    return docs

# ── Full Homepage (all sections in one response) ──
@router.get("/homepage", tags=["public"])
async def get_homepage():
    """Returns ALL published, visible, eligible homepage content in a single structured response."""
    now = datetime.now(timezone.utc)
    now_str = now.isoformat()

    # Brand / Settings
    settings = await website_settings_collection.find_one({"_type": "settings"}, {"_id": 0, "_type": 0})

    # Upcoming Events (published + visible + not deleted)
    events = await event_collection.find({
        "is_published": True, "is_visible": True, "is_deleted": {"$ne": True}
    }).sort("display_order", 1).to_list(length=20)
    clean_list(events)

    # Live Events (published + visible, hide private stream_url)
    live_events_raw = await live_event_collection.find({
        "is_published": True, "is_visible": True
    }).sort("display_order", 1).to_list(length=10)
    live_events = []
    for le in live_events_raw:
        le["_id"] = str(le["_id"])
        le.pop("stream_url", None)  # Never expose private stream URL
        live_events.append(le)

    # Sponsored Banners (published + visible + not deleted + date-valid)
    banners = await banner_collection.find({
        "is_published": True, "is_visible": True, "is_deleted": {"$ne": True}
    }).sort("display_order", 1).to_list(length=20)
    clean_list(banners)

    # Testimonials (approved + published)
    testimonials = await testimonial_collection.find({
        "is_approved": True, "is_published": True
    }).sort("display_order", 1).to_list(length=20)
    clean_list(testimonials)

    # Courses (published + visible + not deleted + type=course)
    courses = await course_collection.find({
        "is_published": True, "is_visible": True, "is_deleted": {"$ne": True},
        "course_type": "course"
    }).sort("display_order", 1).to_list(length=20)
    clean_list(courses)

    # Workshops (published + visible + not deleted + type=workshop)
    workshops = await course_collection.find({
        "is_published": True, "is_visible": True, "is_deleted": {"$ne": True},
        "course_type": "workshop"
    }).sort("display_order", 1).to_list(length=20)
    clean_list(workshops)

    # Contest/Voting Promotions (published + visible)
    contest_promos = await contest_promotion_collection.find({
        "is_published": True, "is_visible": True
    }).sort("display_order", 1).to_list(length=10)
    clean_list(contest_promos)

    # App Promotion
    app_promo = await app_promotion_collection.find_one({"_type": "app_promo", "is_enabled": True, "is_published": True}, {"_id": 0, "_type": 0})

    # Contact Info
    contact = None
    if settings:
        contact = {
            "contact_email": settings.get("contact_email"),
            "contact_phone": settings.get("contact_phone"),
            "office_address": settings.get("office_address"),
            "social_media": settings.get("social_media"),
        }

    # Important Links
    links = await important_link_collection.find({"is_enabled": True}).sort("display_order", 1).to_list(length=50)
    clean_list(links)

    return {
        "brand": {
            "name": settings.get("brand_name") if settings else None,
            "logo": settings.get("logo_url") if settings else None,
            "favicon": settings.get("favicon_url") if settings else None,
            "tagline": settings.get("tagline") if settings else None,
            "title": settings.get("homepage_title") if settings else None,
            "description": settings.get("homepage_description") if settings else None,
        } if settings else {},
        "upcoming_events": events,
        "live_events": live_events,
        "sponsored_banners": banners,
        "testimonials": testimonials,
        "courses": courses,
        "workshops": workshops,
        "contest_promotions": contest_promos,
        "app_promotion": app_promo,
        "contact": contact,
        "important_links": links,
    }

# ── Individual Section Endpoints ──
@router.get("/website-settings", tags=["public"])
async def get_public_settings():
    doc = await website_settings_collection.find_one({"_type": "settings"}, {"_id": 0, "_type": 0})
    return doc if doc else {}

@router.get("/events/upcoming", tags=["public"])
async def get_upcoming_events(skip: int = 0, limit: int = 10, search: Optional[str] = None):
    q = {"is_published": True, "is_visible": True, "is_deleted": {"$ne": True}}
    if search:
        q["title"] = {"$regex": search, "$options": "i"}
    docs = await event_collection.find(q).sort("display_order", 1).skip(skip).limit(limit).to_list(length=limit)
    return clean_list(docs)

@router.get("/events/live", tags=["public"])
async def get_live_events():
    docs = await live_event_collection.find({"is_published": True, "is_visible": True}).sort("display_order", 1).to_list(length=10)
    for d in docs:
        d["_id"] = str(d["_id"])
        d.pop("stream_url", None)  # Hide private stream URL
    return docs

@router.get("/banners", tags=["public"])
async def get_public_banners():
    docs = await banner_collection.find({"is_published": True, "is_visible": True, "is_deleted": {"$ne": True}}).sort("display_order", 1).to_list(length=20)
    return clean_list(docs)

@router.get("/testimonials", tags=["public"])
async def get_public_testimonials():
    docs = await testimonial_collection.find({"is_approved": True, "is_published": True}).sort("display_order", 1).to_list(length=20)
    return clean_list(docs)


def calculate_dynamic_status(doc):
    now = datetime.now(timezone.utc)
    start = doc.get("registration_start_at")
    end = doc.get("registration_end_at")
    
    if start and start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end and end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
        
    if start and now < start:
        status = "UPCOMING"
        can_register = False
    elif end and now >= end:
        status = "CLOSED"
        can_register = False
    else:
        status = "OPEN"
        can_register = True
        
    doc["registration_status"] = status
    doc["can_register"] = can_register
    return doc

@router.get("/courses", tags=["public"])
async def get_public_courses(skip: int = 0, limit: int = 10):
    docs = await course_collection.find({
        "is_published": True, "is_visible": True, "is_deleted": {"$ne": True}, "course_type": "course"
    }).sort("display_order", 1).skip(skip).limit(limit).to_list(length=limit)
    return [calculate_dynamic_status(d) for d in clean_list(docs)]

@router.get("/workshops", tags=["public"])
async def get_public_workshops(skip: int = 0, limit: int = 10):
    docs = await course_collection.find({
        "is_published": True, "is_visible": True, "is_deleted": {"$ne": True}, "course_type": "workshop"
    }).sort("display_order", 1).skip(skip).limit(limit).to_list(length=limit)
    return [calculate_dynamic_status(d) for d in clean_list(docs)]


@router.get("/contest-promotions", tags=["public"])
async def get_public_contest_promotions():
    docs = await contest_promotion_collection.find({"is_published": True, "is_visible": True}).sort("display_order", 1).to_list(length=10)
    return clean_list(docs)

@router.get("/app-promotion", tags=["public"])
async def get_public_app_promotion():
    doc = await app_promotion_collection.find_one({"_type": "app_promo", "is_enabled": True, "is_published": True}, {"_id": 0, "_type": 0})
    return doc if doc else {}

@router.get("/contact-info", tags=["public"])
async def get_public_contact():
    doc = await website_settings_collection.find_one({"_type": "settings"})
    if not doc:
        return {}
    return {
        "contact_email": doc.get("contact_email"),
        "contact_phone": doc.get("contact_phone"),
        "office_address": doc.get("office_address"),
        "support_url": doc.get("support_url"),
        "social_media": doc.get("social_media"),
    }

@router.get("/important-links", tags=["public"])
async def get_public_links():
    docs = await important_link_collection.find({"is_enabled": True}).sort("display_order", 1).to_list(length=50)
    return clean_list(docs)
