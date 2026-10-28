from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Query
from pydantic import BaseModel
import re
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
)
from app.services.audit import log_admin_action
from app.services.ticket import TicketService
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
    doc = await website_settings_collection.find_one({"_type": "settings"})
    return clean(doc)

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

# ════════════════════════════════════════
# EVENTS
# ════════════════════════════════════════
@router.get("/events", tags=["admin-events"])
async def list_events(skip: int = 0, limit: int = 50, search: Optional[str] = None, admin=Depends(get_current_admin)):
    q = {"is_deleted": {"$ne": True}}
    if search:
        q["title"] = {"$regex": search, "$options": "i"}
    docs = await event_collection.find(q).sort("display_order", 1).skip(skip).limit(limit).to_list(length=limit)
    return clean_list(docs)

@router.post("/events", tags=["admin-events"])
async def create_event(data: EventCreate, admin=Depends(get_current_admin)):
    doc = data.model_dump()
    doc["registered_count"] = 0
    doc["created_at"] = datetime.now(timezone.utc)
    doc["created_by"] = admin["email"]
    result = await event_collection.insert_one(doc)
    event_id = str(result.inserted_id)
    await log_admin_action(admin["email"], "create", "event", event_id)
    doc["_id"] = event_id

    # Auto-create initial ticket tier if ticketing is enabled
    if doc.get("ticket_enabled", True):
        cap = int(doc.get("capacity") or 100)
        price = float(doc.get("price") or 0.0)
        default_ticket = {
            "event_id": event_id,
            "type": "General Admission",
            "price": price,
            "total_quantity": cap,
            "available": cap,
            "sale_start": None,
            "sale_end": None,
            "is_visible": True,
            "display_order": 0,
            "created_at": datetime.now(timezone.utc)
        }
        await ticket_collection.insert_one(default_ticket)

    return doc

@router.put("/events/{event_id}", tags=["admin-events"])
async def update_event(event_id: str, data: EventUpdate, admin=Depends(get_current_admin)):
    update = {k: v for k, v in data.model_dump().items() if v is not None}
    update["updated_at"] = datetime.now(timezone.utc)
    result = await event_collection.update_one({"_id": oid(event_id), "is_deleted": {"$ne": True}}, {"$set": update})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Event not found")
    await log_admin_action(admin["email"], "update", "event", event_id)
    doc = await event_collection.find_one({"_id": oid(event_id)})

    # Keep default single tier synced with event price/capacity
    if "price" in update or "capacity" in update:
        tiers = await ticket_collection.find({"event_id": event_id}).to_list(length=10)
        if len(tiers) == 1 and tiers[0].get("type", "").lower() in ["general", "general admission"]:
            tier_update = {}
            if "price" in update:
                tier_update["price"] = float(update["price"])
            if "capacity" in update:
                new_cap = int(update["capacity"])
                tier_update["total_quantity"] = new_cap
                tier_update["available"] = max(0, new_cap - int(doc.get("registered_count") or 0))
            if tier_update:
                tier_update["updated_at"] = datetime.now(timezone.utc)
                await ticket_collection.update_one({"_id": tiers[0]["_id"]}, {"$set": tier_update})

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
    if booking_id_strs:
        all_tix = await issued_ticket_collection.find({"booking_id": {"$in": booking_id_strs}}).to_list(length=1000)
        for t in all_tix:
            t["_id"] = str(t["_id"])
            b_id = t.get("booking_id")
            if b_id not in issued_map:
                issued_map[b_id] = []
            issued_map[b_id].append({
                "ticket_ref": t.get("ticket_ref"),
                "holder_name": t.get("holder_name"),
                "holder_email": t.get("holder_email"),
                "ticket_type": t.get("ticket_type"),
                "payment_status": t.get("payment_status", "PAID"),
                "payment_method": t.get("payment_method", "ONLINE"),
                "is_checked_in": bool(t.get("is_checked_in")),
                "check_in_time": t.get("check_in_time"),
            })

    stats_query = {"event_id": str(event_id)} if event_id else {}
    all_for_stats = await booking_collection.find(stats_query).to_list(length=2000)
    total_tickets = sum(int(b.get("quantity", 1)) for b in all_for_stats)
    total_revenue = sum(
        float(b.get("total_amount", 0.0))
        for b in all_for_stats
        if (b.get("booking_status") == "CONFIRMED" and b.get("payment_status") in ("PAID", "NOT_REQUIRED", "SUCCESS"))
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

        raw_p_status = (b.get("payment_status") or "PENDING").upper()
        raw_p_method = (b.get("payment_method") or ("CASH" if b.get("is_cash") or b.get("cash_payment") else "ONLINE")).upper()
        
        is_paid = (raw_p_status in ["PAID", "SUCCESS", "NOT_REQUIRED"] or bool(b.get("cash_approved")))
        p_status = "PAID" if is_paid else "PENDING"
        p_display = "SUCCESS" if is_paid else "PENDING"

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
            "is_cash": (raw_p_method == "CASH"),
            "can_approve_cash": (raw_p_method == "CASH" and not is_paid),
            "cash_approved": bool(b.get("cash_approved")),
            "approved_by": b.get("approved_by"),
            "approved_at": b.get("approved_at"),
            "paid_at": b.get("paid_at"),
            "payment_reference": b.get("payment_reference"),
            "booked_at": b.get("booked_at"),
            "holder_names": b.get("holder_names", []),
            "tickets": issued_map.get(b_id, []),
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
            dr_is_paid = (dr_p_status in ["PAID", "SUCCESS", "NOT_REQUIRED"] or bool(dr.get("cash_approved")))

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
                "cash_approved": bool(dr.get("cash_approved")),
                "approved_by": dr.get("approved_by"),
                "approved_at": dr.get("approved_at"),
                "paid_at": dr.get("paid_at"),
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
    b["tickets"] = tix

    raw_p_status = (b.get("payment_status") or "PENDING").upper()
    raw_p_method = (b.get("payment_method") or ("CASH" if b.get("is_cash") or b.get("cash_payment") else "ONLINE")).upper()
    is_paid = (raw_p_status in ["PAID", "SUCCESS", "NOT_REQUIRED"] or bool(b.get("cash_approved")))
    b["payment_status"] = "PAID" if is_paid else "PENDING"
    b["payment_status_display"] = "SUCCESS" if is_paid else "PENDING"
    b["payment_method"] = raw_p_method
    b["is_cash"] = (raw_p_method == "CASH")
    b["can_approve_cash"] = (raw_p_method == "CASH" and not is_paid)

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
