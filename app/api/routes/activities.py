from fastapi import APIRouter, Query, HTTPException
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
import re
from bson import ObjectId
from app.core.database import event_collection, ticket_collection, competition_collection, course_collection, merchandise_collection
from app.services.ticket import TicketService
from app.services.competition_lifecycle import evaluate_competition_registration_status
from app.services.competition_submission_config import enrich_competition_submission_config
from app.services.share_service import ShareService
from app.schemas.share import ShareLinkResponse

router = APIRouter()

def clean_ids(docs):
    for doc in docs:
        doc["_id"] = str(doc["_id"])
    return docs

@router.get("/events")
async def get_events(
    skip: int = 0,
    limit: int = 10,
    search: Optional[str] = None,
    category: Optional[str] = None
):
    query = {"is_deleted": {"$ne": True}}
    if search:
        query["title"] = {"$regex": search, "$options": "i"}
    if category and category.strip() and category.upper() != "ALL":
        c_clean = category.strip()
        cat_regex = {"$regex": f"^{re.escape(c_clean)}$", "$options": "i"}
        query["$or"] = [
            {"category": cat_regex},
            {"categories.name": cat_regex}
        ]
    events = await event_collection.find(query).sort("display_order", 1).skip(skip).limit(limit).to_list(length=limit)
    for ev in events:
        ShareService.enrich_event(ev)
    return clean_ids(events)

@router.get("/events/by-category")
async def get_events_by_category():
    """Public API: returns all published events grouped by category."""
    events = await event_collection.find({"is_published": True, "is_deleted": {"$ne": True}}).sort("display_order", 1).to_list(length=200)
    by_category = {}
    for ev in events:
        ShareService.enrich_event(ev)
        cats = [cat.get("name") for cat in ev.get("categories", []) if isinstance(cat, dict) and cat.get("name")]
        if not cats and ev.get("category"):
            cats = [ev["category"]]
        if not cats:
            cats = ["General"]

        cleaned = clean_ids([ev])[0]
        for cat_name in cats:
            if cat_name not in by_category:
                by_category[cat_name] = []
            by_category[cat_name].append(cleaned)

    return {
        "categories": list(by_category.keys()),
        "by_category": by_category,
        "grouped": [{"category": k, "count": len(v), "events": v} for k, v in by_category.items()]
    }

@router.get("/events/{event_id}/share", response_model=ShareLinkResponse)
async def get_event_share_links(event_id: str):
    """
    Public API: returns shareable deep-link, direct ticket booking link,
    pre-composed share text, and social share links (WhatsApp, Facebook, Twitter, LinkedIn, Telegram).
    """
    ev = None
    if ObjectId.is_valid(event_id):
        ev = await event_collection.find_one({"_id": ObjectId(event_id), "is_deleted": {"$ne": True}})
    if not ev:
        ev = await event_collection.find_one({"_id": event_id, "is_deleted": {"$ne": True}})
    if not ev:
        raise HTTPException(status_code=404, detail="Event not found")
    return ShareService.build_event_share_data(ev)

@router.get("/events/{event_id}")
async def get_event_by_id(event_id: str):
    ev = None
    if ObjectId.is_valid(event_id):
        ev = await event_collection.find_one({"_id": ObjectId(event_id), "is_deleted": {"$ne": True}})
    if not ev:
        ev = await event_collection.find_one({"_id": event_id, "is_deleted": {"$ne": True}})
    if not ev:
        raise HTTPException(status_code=404, detail="Event not found")
    ev["_id"] = str(ev["_id"])
    ShareService.enrich_event(ev)
    return ev

@router.get("/tickets")
async def get_tickets(event_id: Optional[str] = None, skip: int = 0, limit: int = 20):
    query = {}
    if event_id:
        query["event_id"] = event_id
    tickets = await ticket_collection.find(query).skip(skip).limit(limit).to_list(length=limit)

    # Auto-provision a default ticket tier if event exists with ticketing enabled
    if event_id and not tickets and skip == 0:
        ev = None
        if ObjectId.is_valid(event_id):
            ev = await event_collection.find_one({"_id": ObjectId(event_id), "is_deleted": {"$ne": True}})
        if not ev:
            ev = await event_collection.find_one({"_id": event_id, "is_deleted": {"$ne": True}})

        if ev and ev.get("ticket_enabled", True) and not ev.get("is_deleted", False):
            cats = ev.get("categories", [])
            registered = int(ev.get("registered_count") or 0)
            if cats:
                new_tickets = []
                for idx, c in enumerate(cats):
                    c_name = c["name"]
                    c_price = float(c.get("price", 0.0))
                    c_cap = int(c.get("capacity") or ev.get("capacity") or 100)
                    tier_doc = {
                        "event_id": str(event_id),
                        "type": c_name,
                        "category": c_name,
                        "price": c_price,
                        "total_quantity": c_cap,
                        "available": max(0, c_cap - registered),
                        "sale_start": None,
                        "sale_end": None,
                        "is_visible": True,
                        "display_order": idx,
                        "created_at": datetime.now(timezone.utc)
                    }
                    res = await ticket_collection.insert_one(tier_doc)
                    tier_doc["_id"] = str(res.inserted_id)
                    new_tickets.append(tier_doc)
                tickets = new_tickets
            else:
                cap = int(ev.get("capacity") or 100)
                price = float(ev.get("price") or 0.0)
                default_ticket = {
                    "event_id": str(event_id),
                    "type": ev.get("category") or "General Admission",
                    "category": ev.get("category") or "General",
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
                tickets = [default_ticket]

    normalized = [TicketService.normalize_ticket_data(t) for t in tickets]
    return normalized

@router.get("/tickets/{ticket_id}")
async def get_ticket_by_id(ticket_id: str):
    try:
        t_oid = ObjectId(ticket_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid ticket ID")
    ticket = await ticket_collection.find_one({"_id": t_oid})
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return TicketService.normalize_ticket_data(ticket)

@router.get("/competitions/by-category")
async def get_competitions_by_category():
    """Public API: returns all published competitions grouped by category for the competition section."""
    comps = await competition_collection.find({"is_published": True}).sort("created_at", -1).to_list(length=200)
    by_category = {}
    for comp in comps:
        reg_eval = evaluate_competition_registration_status(comp)
        comp["registration_status"] = reg_eval["registration_status"]
        comp["is_registration_open"] = reg_eval["is_registration_open"]
        comp["can_register"] = reg_eval["can_register"]
        comp["registration_reason"] = reg_eval["reason"]
        enrich_competition_submission_config(comp)
        ShareService.enrich_competition(comp)

        cats = [cat.get("name") for cat in comp.get("categories", []) if isinstance(cat, dict) and cat.get("name")]
        if not cats and comp.get("category"):
            cats = [comp["category"]]
        if not cats:
            cats = ["General"]

        cleaned = clean_ids([comp])[0]
        for cat_name in cats:
            if cat_name not in by_category:
                by_category[cat_name] = []
            by_category[cat_name].append(cleaned)

    return {
        "categories": list(by_category.keys()),
        "by_category": by_category,
        "grouped": [{"category": k, "count": len(v), "competitions": v} for k, v in by_category.items()]
    }

@router.get("/competitions/{comp_id}/share", response_model=ShareLinkResponse)
async def get_competition_share_links(comp_id: str):
    """
    Public API: returns shareable deep-link, direct registration link,
    pre-composed share text, and social share links (WhatsApp, Facebook, Twitter, LinkedIn, Telegram).
    """
    comp = None
    if ObjectId.is_valid(comp_id):
        comp = await competition_collection.find_one({"_id": ObjectId(comp_id), "is_deleted": {"$ne": True}})
    if not comp:
        comp = await competition_collection.find_one({"_id": comp_id, "is_deleted": {"$ne": True}})
    if not comp:
        raise HTTPException(status_code=404, detail="Competition not found")
    return ShareService.build_competition_share_data(comp)

@router.get("/competitions")
async def get_competitions(
    skip: int = 0,
    limit: int = 10,
    search: Optional[str] = None,
    category: Optional[str] = None
):
    query = {}
    if search:
        query["title"] = {"$regex": search, "$options": "i"}
    if category and category.strip() and category.upper() != "ALL":
        c_clean = category.strip()
        cat_regex = {"$regex": f"^{re.escape(c_clean)}$", "$options": "i"}
        query["$or"] = [
            {"category": cat_regex},
            {"categories.name": cat_regex}
        ]
    competitions = await competition_collection.find(query).sort("created_at", -1).skip(skip).limit(limit).to_list(length=limit)
    for comp in competitions:
        reg_eval = evaluate_competition_registration_status(comp)
        comp["registration_status"] = reg_eval["registration_status"]
        comp["is_registration_open"] = reg_eval["is_registration_open"]
        comp["can_register"] = reg_eval["can_register"]
        comp["registration_reason"] = reg_eval["reason"]
        enrich_competition_submission_config(comp)
        ShareService.enrich_competition(comp)
    return clean_ids(competitions)

@router.get("/competitions/{comp_id}")
async def get_competition_activity(comp_id: str):
    try:
        c_id = ObjectId(comp_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid competition ID")
    comp = await competition_collection.find_one({"_id": c_id})
    if not comp:
        raise HTTPException(status_code=404, detail="Competition not found")
    comp["_id"] = str(comp["_id"])
    reg_eval = evaluate_competition_registration_status(comp)
    comp["registration_status"] = reg_eval["registration_status"]
    comp["is_registration_open"] = reg_eval["is_registration_open"]
    comp["can_register"] = reg_eval["can_register"]
    comp["registration_reason"] = reg_eval["reason"]
    comp = enrich_competition_submission_config(comp)
    comp["score_visible_for_public"] = bool(comp.get("score_visible_for_public", comp.get("results_published", False)) or comp.get("result_status") == "PUBLISHED")
    ShareService.enrich_competition(comp)
    return comp

@router.get("/courses")
async def get_courses(skip: int = 0, limit: int = 10, search: Optional[str] = None):
    query = {}
    if search:
        query["title"] = {"$regex": search, "$options": "i"}
    courses = await course_collection.find(query).skip(skip).limit(limit).to_list(length=limit)
    return clean_ids(courses)

@router.get("/merchandise")
async def get_merchandise(skip: int = 0, limit: int = 10, search: Optional[str] = None):
    query = {}
    if search:
        query["name"] = {"$regex": search, "$options": "i"}
    merch = await merchandise_collection.find(query).skip(skip).limit(limit).to_list(length=limit)
    return clean_ids(merch)
