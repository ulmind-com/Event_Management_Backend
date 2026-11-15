from fastapi import APIRouter, Query, Request, HTTPException, status
from fastapi.responses import RedirectResponse
import asyncio
from typing import Optional
from datetime import datetime, timezone
from bson import ObjectId
from app.core.cache import FastCache
from app.core.database import (
    website_settings_collection, event_collection, live_event_collection,
    ticket_collection, banner_collection, testimonial_collection,
    course_collection, contest_promotion_collection, app_promotion_collection,
    important_link_collection, competition_collection,
)
from app.services.share_service import ShareService
from app.schemas.share import ShareLinkResponse
from app.api.routes.admin_cms import (
    DEFAULT_STAGE_MOMENTS_SECTION,
    DEFAULT_SPOTLIGHT_SECTION,
    DEFAULT_PARTNERS_SECTION,
    DEFAULT_SPONSOR_POPUP,
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

# ── Full Homepage (all sections in one response) ──
@router.get("/homepage", tags=["public"])
async def get_homepage():
    """Returns ALL published, visible, eligible homepage content in a single structured response with concurrency protection & in-memory caching."""
    async def _fetch():
        (
            settings,
            events,
            live_events_raw,
            banners,
            testimonials,
            courses,
            workshops,
            contest_promos,
            app_promo,
            links,
            competitions
        ) = await asyncio.gather(
            website_settings_collection.find_one({"_type": "settings"}, {"_id": 0, "_type": 0}),
            event_collection.find({
                "is_published": True, "is_visible": True, "is_deleted": {"$ne": True}
            }).sort("display_order", 1).to_list(length=20),
            live_event_collection.find({
                "is_published": True, "is_visible": True
            }).sort("display_order", 1).to_list(length=10),
            banner_collection.find({
                "is_published": True, "is_visible": True, "is_deleted": {"$ne": True}
            }).sort("display_order", 1).to_list(length=30),
            testimonial_collection.find({
                "is_approved": True, "is_published": True
            }).sort("display_order", 1).to_list(length=20),
            course_collection.find({
                "is_published": True, "is_visible": True, "is_deleted": {"$ne": True},
                "course_type": "course"
            }).sort("display_order", 1).to_list(length=20),
            course_collection.find({
                "is_published": True, "is_visible": True, "is_deleted": {"$ne": True},
                "course_type": "workshop"
            }).sort("display_order", 1).to_list(length=20),
            contest_promotion_collection.find({
                "is_published": True, "is_visible": True
            }).sort("display_order", 1).to_list(length=10),
            app_promotion_collection.find_one({"_type": "app_promo", "is_enabled": True, "is_published": True}, {"_id": 0, "_type": 0}),
            important_link_collection.find({"is_enabled": True}).sort("display_order", 1).to_list(length=50),
            competition_collection.find({
                "is_published": True, "is_deleted": {"$ne": True}
            }).sort([("display_order", 1), ("start_date", 1), ("created_at", -1)]).to_list(length=20)
        )

        for ev in events:
            ShareService.enrich_event(ev)
        clean_list(events)

        live_events = []
        for le in live_events_raw:
            le["_id"] = str(le["_id"])
            le.pop("stream_url", None)  # Never expose private stream URL
            live_events.append(le)

        for b in banners:
            if not b.get("image_url_desktop"):
                b["image_url_desktop"] = b.get("image_url")
            if not b.get("image_url") and b.get("image_url_desktop"):
                b["image_url"] = b.get("image_url_desktop")
            if not b.get("image_url_mobile") and b.get("mobile_image_url"):
                b["image_url_mobile"] = b.get("mobile_image_url")
        clean_list(banners)

        clean_list(testimonials)
        clean_list(courses)
        clean_list(workshops)
        clean_list(contest_promos)
        clean_list(links)

        for c in competitions:
            c["_id"] = str(c["_id"])
            if not c.get("card_image_url") and c.get("image_url"):
                c["card_image_url"] = c.get("image_url")
            if not c.get("image_url") and c.get("card_image_url"):
                c["image_url"] = c.get("card_image_url")
            if not c.get("banner_image_url") and c.get("banner_url"):
                c["banner_image_url"] = c.get("banner_url")
        clean_list(competitions)

        contact = None
        if settings:
            contact = {
                "contact_email": settings.get("contact_email"),
                "contact_phone": settings.get("contact_phone"),
                "office_address": settings.get("office_address"),
                "social_media": settings.get("social_media"),
            }

        featured_tiles = (settings or {}).get("featured_tiles")
        if not featured_tiles or not isinstance(featured_tiles, list) or len(featured_tiles) == 0:
            featured_tiles = DEFAULT_FEATURED_TILES

        stats_list = (settings or {}).get("homepage_stats")
        if not stats_list or not isinstance(stats_list, list) or len(stats_list) == 0:
            stats_list = [
                {"label": "Live events", "value": 12, "suffix": "+", "icon": "calendar"},
                {"label": "Dancers on stage", "value": 25000, "suffix": "+", "icon": "users"},
                {"label": "Masterclasses", "value": 40, "suffix": "+", "icon": "graduation"},
                {"label": "Cities covered", "value": 28, "suffix": "", "icon": "trophy"},
            ]

        gallery_section = (settings or {}).get("gallery_section")
        stage_moments_section = (settings or {}).get("stage_moments_section")
        spotlight_section = (settings or {}).get("spotlight_section")
        partners_section = (settings or {}).get("partners_section")
        sponsor_popup = (settings or {}).get("sponsor_popup")

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
            "upcoming_competitions": competitions,
            "competitions_section": {
                "title": settings.get("competitions_section_title") or "Upcoming Competitions" if settings else "Upcoming Competitions",
                "label": settings.get("competitions_section_label") or "What's next" if settings else "What's next",
                "view_all_url": settings.get("competitions_section_link") or "/competitions" if settings else "/competitions",
            } if settings else {
                "title": "Upcoming Competitions",
                "label": "What's next",
                "view_all_url": "/competitions",
            },
            "live_stage_section": {
                "title": settings.get("live_stage_title") or "Live Stage" if settings else "Live Stage",
                "label": settings.get("live_stage_label") or "Streaming right now" if settings else "Streaming right now",
            } if settings else {
                "title": "Live Stage",
                "label": "Streaming right now",
            },
            "homepage_stats": stats_list,
            "live_events": live_events,
            "sponsored_banners": banners,
            "testimonials": testimonials,
            "courses": courses,
            "workshops": workshops,
            "contest_promotions": contest_promos,
            "app_promotion": app_promo,
            "contact": contact,
            "important_links": links,
            "featured_tiles": featured_tiles,
            "gallery_section": gallery_section if gallery_section and isinstance(gallery_section, dict) else {
                "title": "A stage worth watching",
                "label": "In motion",
                "description": "Every performance, workshop and showdown — captured and brought to life. Scroll through the moments that make Upgrade Talents unforgettable.",
                "view_all_url": "/gallery",
                "images": [],
            },
            "stage_moments_section": stage_moments_section if stage_moments_section and isinstance(stage_moments_section, dict) else DEFAULT_STAGE_MOMENTS_SECTION,
            "spotlight_section": spotlight_section if spotlight_section and isinstance(spotlight_section, dict) else DEFAULT_SPOTLIGHT_SECTION,
            "partners_section": partners_section if partners_section and isinstance(partners_section, dict) else DEFAULT_PARTNERS_SECTION,
            "sponsor_popup": sponsor_popup if sponsor_popup and isinstance(sponsor_popup, dict) else DEFAULT_SPONSOR_POPUP,
        }

    return await FastCache.cached_or_fetch("homepage", 25, _fetch)

# ── Individual Section Endpoints ──
@router.get("/sponsor-popup", tags=["public"])
async def get_public_sponsor_popup():
    settings = await website_settings_collection.find_one({"_type": "settings"})
    popup = (settings or {}).get("sponsor_popup")
    return popup if popup and isinstance(popup, dict) else DEFAULT_SPONSOR_POPUP

@router.get("/stage-moments", tags=["public"])
async def get_public_stage_moments():
    settings = await website_settings_collection.find_one({"_type": "settings"})
    stage = (settings or {}).get("stage_moments_section")
    return stage if stage and isinstance(stage, dict) else DEFAULT_STAGE_MOMENTS_SECTION

@router.get("/spotlight-section", tags=["public"])
async def get_public_spotlight_section():
    settings = await website_settings_collection.find_one({"_type": "settings"})
    spotlight = (settings or {}).get("spotlight_section")
    return spotlight if spotlight and isinstance(spotlight, dict) else DEFAULT_SPOTLIGHT_SECTION

@router.get("/partners-section", tags=["public"])
async def get_public_partners_section():
    settings = await website_settings_collection.find_one({"_type": "settings"})
    partners = (settings or {}).get("partners_section")
    return partners if partners and isinstance(partners, dict) else DEFAULT_PARTNERS_SECTION
@router.get("/competitions/upcoming", tags=["public"])
async def get_upcoming_competitions(skip: int = 0, limit: int = 20, search: Optional[str] = None):
    q = {"is_published": True, "is_deleted": {"$ne": True}}
    if search:
        q["title"] = {"$regex": search, "$options": "i"}
    docs = await competition_collection.find(q).sort([("display_order", 1), ("start_date", 1), ("created_at", -1)]).skip(skip).limit(limit).to_list(length=limit)
    for c in docs:
        c["_id"] = str(c["_id"])
        if not c.get("card_image_url") and c.get("image_url"):
            c["card_image_url"] = c.get("image_url")
        if not c.get("image_url") and c.get("card_image_url"):
            c["image_url"] = c.get("card_image_url")
        if not c.get("banner_image_url") and c.get("banner_url"):
            c["banner_image_url"] = c.get("banner_url")
    return clean_list(docs)

@router.get("/featured-tiles", tags=["public"])
async def get_public_featured_tiles():
    async def _f():
        settings = await website_settings_collection.find_one({"_type": "settings"}, {"_id": 0, "featured_tiles": 1})
        tiles = (settings or {}).get("featured_tiles")
        return tiles if tiles and isinstance(tiles, list) and len(tiles) > 0 else DEFAULT_FEATURED_TILES
    return await FastCache.cached_or_fetch("public_featured_tiles", 30, _f)

@router.get("/website-settings", tags=["public"])
async def get_public_settings():
    async def _f():
        doc = await website_settings_collection.find_one({"_type": "settings"}, {"_id": 0, "_type": 0})
        return doc if doc else {}
    return await FastCache.cached_or_fetch("public_settings", 30, _f)

@router.get("/events/upcoming", tags=["public"])
async def get_upcoming_events(skip: int = 0, limit: int = 10, search: Optional[str] = None):
    async def _f():
        q = {"is_published": True, "is_visible": True, "is_deleted": {"$ne": True}}
        if search:
            q["title"] = {"$regex": search, "$options": "i"}
        docs = await event_collection.find(q).sort("display_order", 1).skip(skip).limit(limit).to_list(length=limit)
        return clean_list(docs)
    ck = f"public_events_{skip}_{limit}_{search or ''}"
    return await FastCache.cached_or_fetch(ck, 20, _f)

@router.get("/events/live", tags=["public"])
async def get_live_events():
    async def _f():
        docs = await live_event_collection.find({"is_published": True, "is_visible": True}).sort("display_order", 1).to_list(length=10)
        for d in docs:
            d["_id"] = str(d["_id"])
            d.pop("stream_url", None)  # Hide private stream URL
        return docs
    return await FastCache.cached_or_fetch("public_live_events", 15, _f)

@router.get("/banners", tags=["public"])
async def get_public_banners():
    async def _f():
        docs = await banner_collection.find({"is_published": True, "is_visible": True, "is_deleted": {"$ne": True}}).sort("display_order", 1).to_list(length=30)
        for b in docs:
            if not b.get("image_url_desktop"):
                b["image_url_desktop"] = b.get("image_url")
            if not b.get("image_url") and b.get("image_url_desktop"):
                b["image_url"] = b.get("image_url_desktop")
            if not b.get("image_url_mobile") and b.get("mobile_image_url"):
                b["image_url_mobile"] = b.get("mobile_image_url")
        return clean_list(docs)
    return await FastCache.cached_or_fetch("public_banners", 25, _f)

@router.get("/testimonials", tags=["public"])
async def get_public_testimonials():
    async def _f():
        docs = await testimonial_collection.find({"is_approved": True, "is_published": True}).sort("display_order", 1).to_list(length=20)
        return clean_list(docs)
    return await FastCache.cached_or_fetch("public_testimonials", 60, _f)


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


@router.get("/share/event/{event_id}", response_model=ShareLinkResponse, tags=["share", "public"])
async def share_event_redirect_or_data(
    event_id: str,
    request: Request,
    json: Optional[bool] = Query(False, description="Set true to return JSON payload instead of redirecting")
):
    """
    Universal Event Share Resolver:
    - If accessed from browser, mobile link, or social app: redirects (HTTP 307) directly to event ticket booking.
    - If queried with '?json=true' or 'Accept: application/json': returns structured share links & metadata.
    """
    ev = None
    if ObjectId.is_valid(event_id):
        ev = await event_collection.find_one({"_id": ObjectId(event_id), "is_deleted": {"$ne": True}})
    if not ev:
        ev = await event_collection.find_one({"_id": event_id, "is_deleted": {"$ne": True}})
    if not ev:
        raise HTTPException(status_code=404, detail="Event not found")
        
    share_data = ShareService.build_event_share_data(ev)
    accept_header = request.headers.get("accept", "")
    if json or "application/json" in accept_header:
        return ShareLinkResponse(**share_data)
        
    return RedirectResponse(url=share_data["direct_booking_url"], status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@router.get("/share/competition/{comp_id}", response_model=ShareLinkResponse, tags=["share", "public"])
async def share_competition_redirect_or_data(
    comp_id: str,
    request: Request,
    json: Optional[bool] = Query(False, description="Set true to return JSON payload instead of redirecting")
):
    """
    Universal Competition Share Resolver:
    - If accessed from browser, mobile link, or social app: redirects (HTTP 307) directly to competition registration.
    - If queried with '?json=true' or 'Accept: application/json': returns structured share links & metadata.
    """
    comp = None
    if ObjectId.is_valid(comp_id):
        comp = await competition_collection.find_one({"_id": ObjectId(comp_id), "is_deleted": {"$ne": True}})
    if not comp:
        comp = await competition_collection.find_one({"_id": comp_id, "is_deleted": {"$ne": True}})
    if not comp:
        raise HTTPException(status_code=404, detail="Competition not found")
        
    share_data = ShareService.build_competition_share_data(comp)
    accept_header = request.headers.get("accept", "")
    if json or "application/json" in accept_header:
        return ShareLinkResponse(**share_data)
        
    return RedirectResponse(url=share_data["direct_registration_url"], status_code=status.HTTP_307_TEMPORARY_REDIRECT)
