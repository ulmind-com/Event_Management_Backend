from fastapi import APIRouter, Depends, HTTPException, status
from typing import Optional
from bson import ObjectId
from app.core.admin_auth import get_current_admin
from app.core.database import (
    website_settings_collection, event_collection, live_event_collection,
    ticket_collection, banner_collection, testimonial_collection,
    course_collection, competition_collection, contest_promotion_collection,
    app_promotion_collection, important_link_collection, audit_log_collection,
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
async def update_website_settings(data: WebsiteSettingsUpdate, VotingPowerSettings, admin=Depends(get_current_admin)):
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
    await log_admin_action(admin["email"], "create", "event", str(result.inserted_id))
    doc["_id"] = str(result.inserted_id)
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
@router.get("/banners", tags=["admin-banners"])
async def list_banners(admin=Depends(get_current_admin)):
    docs = await banner_collection.find({"is_deleted": {"$ne": True}}).sort("display_order", 1).to_list(length=50)
    return clean_list(docs)

@router.post("/banners", tags=["admin-banners"])
async def create_banner(data: BannerCreate, admin=Depends(get_current_admin)):
    doc = data.model_dump()
    doc["created_at"] = datetime.now(timezone.utc)
    result = await banner_collection.insert_one(doc)
    await log_admin_action(admin["email"], "create", "banner", str(result.inserted_id))
    doc["_id"] = str(result.inserted_id)
    return doc

@router.put("/banners/{banner_id}", tags=["admin-banners"])
async def update_banner(banner_id: str, data: BannerUpdate, admin=Depends(get_current_admin)):
    update = {k: v for k, v in data.model_dump().items() if v is not None}
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
    return clean_list(docs)

@router.post("/tickets", tags=["admin-tickets"])
async def create_ticket(data: TicketCreate, admin=Depends(get_current_admin)):
    doc = data.model_dump()
    if doc.get("available") is None:
        doc["available"] = doc["total_quantity"]
    doc["created_at"] = datetime.now(timezone.utc)
    result = await ticket_collection.insert_one(doc)
    await log_admin_action(admin["email"], "create", "ticket", str(result.inserted_id))
    doc["_id"] = str(result.inserted_id)
    return doc

@router.put("/tickets/{tid}", tags=["admin-tickets"])
async def update_ticket(tid: str, data: TicketUpdate, admin=Depends(get_current_admin)):
    update = {k: v for k, v in data.model_dump().items() if v is not None}
    update["updated_at"] = datetime.now(timezone.utc)
    result = await ticket_collection.update_one({"_id": oid(tid)}, {"$set": update})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Ticket not found")
    await log_admin_action(admin["email"], "update", "ticket", tid)
    doc = await ticket_collection.find_one({"_id": oid(tid)})
    return clean(doc)

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
