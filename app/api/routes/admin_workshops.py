from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from typing import Optional
from bson import ObjectId
from datetime import datetime, timezone
from app.core.admin_auth import get_current_admin
from app.core.database import course_collection, registration_collection
from app.schemas.cms import CourseCreate, CourseUpdate, PublishToggle
from app.services.audit import log_admin_action
from app.services.pdf_generator import PDFGenerator

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
    return [clean(doc) for doc in docs]

@router.get("/")
async def list_workshops(admin=Depends(get_current_admin)):
    """List all workshops."""
    docs = await course_collection.find({"is_deleted": {"$ne": True}, "course_type": "workshop"}).sort("display_order", 1).to_list(length=500)
    return clean_list(docs)

@router.post("/")
async def create_workshop(data: CourseCreate, admin=Depends(get_current_admin)):
    """Create a new workshop."""
    if data.registration_start_at >= data.registration_end_at:
        raise HTTPException(status_code=400, detail="Registration start time must be before end time.")
        
    doc = data.model_dump()
    doc["course_type"] = "workshop" # Force workshop type
    doc["enrolled_count"] = 0
    doc["created_at"] = datetime.now(timezone.utc)
    
    result = await course_collection.insert_one(doc)
    await log_admin_action(admin["email"], "create", "workshop", str(result.inserted_id))
    doc["_id"] = str(result.inserted_id)
    return doc

@router.get("/{cid}")
async def get_workshop(cid: str, admin=Depends(get_current_admin)):
    doc = await course_collection.find_one({"_id": oid(cid), "course_type": "workshop"})
    if not doc:
        raise HTTPException(status_code=404, detail="Workshop not found")
    return clean(doc)

@router.put("/{cid}")
async def update_workshop(cid: str, data: CourseUpdate, admin=Depends(get_current_admin)):
    """Update an existing workshop schedule and details."""
    current = await course_collection.find_one({"_id": oid(cid), "course_type": "workshop"})
    if not current:
        raise HTTPException(status_code=404, detail="Workshop not found")
        
    update = {k: v for k, v in data.model_dump().items() if v is not None}
    
    start_at = update.get("registration_start_at", current.get("registration_start_at"))
    end_at = update.get("registration_end_at", current.get("registration_end_at"))
    
    if start_at and end_at and start_at >= end_at:
        raise HTTPException(status_code=400, detail="Registration start time must be before end time.")
        
    update["updated_at"] = datetime.now(timezone.utc)
    
    result = await course_collection.update_one({"_id": oid(cid), "is_deleted": {"$ne": True}}, {"$set": update})
    await log_admin_action(admin["email"], "update", "workshop", cid)
    
    doc = await course_collection.find_one({"_id": oid(cid)})
    return clean(doc)

@router.patch("/{cid}/publish")
async def toggle_workshop_publish(cid: str, data: PublishToggle, admin=Depends(get_current_admin)):
    result = await course_collection.update_one(
        {"_id": oid(cid), "course_type": "workshop"}, 
        {"$set": {"is_published": data.is_published}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Workshop not found")
    await log_admin_action(admin["email"], "publish" if data.is_published else "unpublish", "workshop", cid)
    return {"message": f"Workshop {'published' if data.is_published else 'unpublished'}"}

@router.delete("/{cid}")
async def delete_workshop(cid: str, admin=Depends(get_current_admin)):
    result = await course_collection.update_one({"_id": oid(cid), "course_type": "workshop"}, {"$set": {"is_deleted": True}})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Workshop not found")
    await log_admin_action(admin["email"], "soft_delete", "workshop", cid)
    return {"message": "Workshop deleted"}

@router.get("/{cid}/registrations")
async def list_workshop_registrations(cid: str, status: Optional[str] = None, admin=Depends(get_current_admin)):
    comp = await course_collection.find_one({"_id": oid(cid), "course_type": "workshop"})
    if not comp: raise HTTPException(status_code=404, detail="Workshop not found")
    
    q = {"activity_id": cid, "activity_type": "workshop"}
    if status: q["status"] = status
    
    docs = await registration_collection.find(q).sort("created_at", -1).to_list(length=5000)
    return clean_list(docs)

@router.get("/{cid}/registration-stats")
async def get_workshop_registration_stats(cid: str, admin=Depends(get_current_admin)):
    comp = await course_collection.find_one({"_id": oid(cid), "course_type": "workshop"})
    if not comp: raise HTTPException(status_code=404, detail="Workshop not found")
    
    total = await registration_collection.count_documents({"activity_id": cid, "activity_type": "workshop"})
    confirmed = await registration_collection.count_documents({"activity_id": cid, "activity_type": "workshop", "status": "CONFIRMED"})
    pending = await registration_collection.count_documents({"activity_id": cid, "activity_type": "workshop", "status": "PENDING_PAYMENT"})
    cancelled = await registration_collection.count_documents({"activity_id": cid, "activity_type": "workshop", "status": "CANCELLED"})
    
    return {
        "course_id": cid,
        "title": comp.get("title"),
        "total_registrations": total,
        "confirmed": confirmed,
        "pending_payment": pending,
        "cancelled": cancelled,
        "available_seats": comp.get("available_seats", 0) - confirmed
    }

@router.get("/{cid}/registrations/export/pdf")
async def export_workshop_registrations_pdf(cid: str, admin=Depends(get_current_admin)):
    comp = await course_collection.find_one({"_id": oid(cid), "course_type": "workshop"})
    if not comp: raise HTTPException(status_code=404, detail="Workshop not found")
    
    docs = await registration_collection.find({"activity_id": cid, "activity_type": "workshop"}).sort("created_at", 1).to_list(length=10000)
    
    pdf_bytes = PDFGenerator.generate_course_registrations_pdf(comp, docs)
    filename = f"workshop_registrations_{cid}.pdf"
    
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )
