from fastapi import APIRouter, Query, HTTPException
from datetime import datetime, timezone
from typing import List, Optional
from bson import ObjectId
from app.core.database import event_collection, ticket_collection, competition_collection, course_collection, merchandise_collection
from app.services.ticket import TicketService
from app.services.competition_lifecycle import evaluate_competition_registration_status

router = APIRouter()

def clean_ids(docs):
    for doc in docs:
        doc["_id"] = str(doc["_id"])
    return docs

@router.get("/events")
async def get_events(skip: int = 0, limit: int = 10, search: Optional[str] = None):
    query = {"is_deleted": {"$ne": True}}
    if search:
        query["title"] = {"$regex": search, "$options": "i"}
    events = await event_collection.find(query).skip(skip).limit(limit).to_list(length=limit)
    return clean_ids(events)

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

@router.get("/competitions")
async def get_competitions(skip: int = 0, limit: int = 10, search: Optional[str] = None):
    query = {}
    if search:
        query["title"] = {"$regex": search, "$options": "i"}
    competitions = await competition_collection.find(query).skip(skip).limit(limit).to_list(length=limit)
    for comp in competitions:
        reg_eval = evaluate_competition_registration_status(comp)
        comp["registration_status"] = reg_eval["registration_status"]
        comp["is_registration_open"] = reg_eval["is_registration_open"]
        comp["can_register"] = reg_eval["can_register"]
        comp["registration_reason"] = reg_eval["reason"]
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
