from fastapi import APIRouter, Query, HTTPException
from typing import List, Optional
from bson import ObjectId
from app.core.database import event_collection, ticket_collection, competition_collection, course_collection, merchandise_collection

router = APIRouter()

def clean_ids(docs):
    for doc in docs:
        doc["_id"] = str(doc["_id"])
    return docs

@router.get("/events")
async def get_events(skip: int = 0, limit: int = 10, search: Optional[str] = None):
    query = {}
    if search:
        query["title"] = {"$regex": search, "$options": "i"}
    events = await event_collection.find(query).skip(skip).limit(limit).to_list(length=limit)
    return clean_ids(events)

@router.get("/tickets")
async def get_tickets(event_id: Optional[str] = None, skip: int = 0, limit: int = 10):
    query = {}
    if event_id:
        query["event_id"] = event_id
    tickets = await ticket_collection.find(query).skip(skip).limit(limit).to_list(length=limit)
    return clean_ids(tickets)

@router.get("/competitions")
async def get_competitions(skip: int = 0, limit: int = 10, search: Optional[str] = None):
    query = {}
    if search:
        query["title"] = {"$regex": search, "$options": "i"}
    competitions = await competition_collection.find(query).skip(skip).limit(limit).to_list(length=limit)
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
