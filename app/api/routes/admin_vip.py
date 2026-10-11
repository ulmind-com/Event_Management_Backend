from fastapi import APIRouter, Depends, HTTPException, Query, status
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
from bson import ObjectId

from app.core.admin_auth import get_current_admin
from app.core.database import (
    vip_configuration_collection,
    vip_booking_collection,
    vip_pass_collection,
    vip_checkin_collection,
)
from app.schemas.vip import (
    VIPConfigurationCreateReq,
    VIPConfigurationUpdateReq,
    VIPConfigurationResponse,
    VIPCapacityStats,
    VIPPassResponse,
    VIPVerifyReq,
    VIPVerifyResponse,
    VIPRevokeReq,
    VIPManualIssueReq,
)
from app.services.vip_service import VIPService, serialize_mongo

router = APIRouter()

# ──────────────────────────────────────────────────────────
# VIP Configuration Management
# ──────────────────────────────────────────────────────────

@router.post("/events/{event_id}/vip-configurations", response_model=Dict[str, Any], status_code=status.HTTP_201_CREATED)
async def create_vip_configuration(
    event_id: str,
    req: VIPConfigurationCreateReq,
    admin: dict = Depends(get_current_admin)
):
    """Super Admin creates a new paid VIP pass configuration for an event."""
    return serialize_mongo(await VIPService.create_configuration(event_id, req, admin.get("email")))

@router.get("/events/{event_id}/vip-configurations", response_model=List[Dict[str, Any]])
async def list_admin_event_vip_configurations(
    event_id: str,
    admin: dict = Depends(get_current_admin)
):
    """Super Admin lists all VIP pass configurations for an event (including disabled)."""
    return [serialize_mongo(x) for x in await VIPService.list_event_configurations(event_id, admin_view=True)]

@router.get("/vip-configurations/{configuration_id}", response_model=Dict[str, Any])
async def get_vip_configuration_detail(
    configuration_id: str,
    admin: dict = Depends(get_current_admin)
):
    """Super Admin retrieves a specific VIP configuration with live capacity stats."""
    return serialize_mongo(await VIPService.get_configuration(configuration_id))

@router.put("/vip-configurations/{configuration_id}", response_model=Dict[str, Any])
async def update_vip_configuration(
    configuration_id: str,
    req: VIPConfigurationUpdateReq,
    admin: dict = Depends(get_current_admin)
):
    """Super Admin updates an existing VIP configuration."""
    return serialize_mongo(await VIPService.update_configuration(configuration_id, req, admin.get("email")))

@router.post("/vip-configurations/{configuration_id}/enable", response_model=Dict[str, Any])
async def enable_vip_configuration(
    configuration_id: str,
    admin: dict = Depends(get_current_admin)
):
    """Super Admin enables VIP pass booking for a configuration."""
    return serialize_mongo(await VIPService.toggle_configuration_status(configuration_id, enable=True, admin_email=admin.get("email")))

@router.post("/vip-configurations/{configuration_id}/disable", response_model=Dict[str, Any])
async def disable_vip_configuration(
    configuration_id: str,
    admin: dict = Depends(get_current_admin)
):
    """Super Admin disables VIP pass booking for a configuration."""
    return serialize_mongo(await VIPService.toggle_configuration_status(configuration_id, enable=False, admin_email=admin.get("email")))

# ──────────────────────────────────────────────────────────
# Capacity & Analytics
# ──────────────────────────────────────────────────────────

@router.get("/events/{event_id}/vip-capacity", response_model=Dict[str, Any])
async def get_event_vip_capacity(
    event_id: str,
    admin: dict = Depends(get_current_admin)
):
    """
    Super Admin inspects authoritative VIP capacity metrics across all configurations of an event.
    Returns total, booked, confirmed, pending payment, available capacity, and sold-out flags.
    """
    configs = await vip_configuration_collection.find({"event_id": str(event_id)}).to_list(length=50)
    
    total_cap = 0
    total_booked = 0
    total_confirmed = 0
    total_pending = 0
    total_available = 0

    config_summaries = []
    for c in configs:
        stats = await VIPService.calculate_capacity_stats(c)
        total_cap += stats.total_capacity
        total_booked += stats.booked_count
        total_confirmed += stats.confirmed_count
        total_pending += stats.pending_payment_count
        total_available += stats.available_count
        
        config_summaries.append({
            "configuration_id": str(c["_id"]),
            "name": c.get("name"),
            "category": c.get("category"),
            "price": c.get("price"),
            "currency": c.get("currency"),
            "is_enabled": c.get("is_enabled"),
            "capacity": stats
        })

    return {
        "event_id": event_id,
        "overall_capacity": {
            "total_capacity": total_cap,
            "booked_count": total_booked,
            "confirmed_count": total_confirmed,
            "pending_payment_count": total_pending,
            "available_count": total_available,
            "sold_out": (total_available <= 0)
        },
        "configurations": config_summaries
    }

@router.get("/events/{event_id}/vip-analytics", response_model=Dict[str, Any])
async def get_event_vip_analytics(
    event_id: str,
    admin: dict = Depends(get_current_admin)
):
    """
    Comprehensive VIP pass analytics including revenue, check-in conversion, gate traffic, and rejected attempts.
    """
    eid_str = str(event_id)
    bookings = await vip_booking_collection.find({"event_id": eid_str}).to_list(length=1000)
    passes = await vip_pass_collection.find({"event_id": eid_str}).to_list(length=1000)
    checkins = await vip_checkin_collection.find({"event_id": eid_str}).to_list(length=2000)

    total_revenue = sum(float(b.get("total_amount", 0.0)) for b in bookings if b.get("booking_status") == "CONFIRMED")
    currency = bookings[0].get("currency", "INR") if bookings else "INR"

    successful_entries = [c for c in checkins if c.get("outcome") == "ENTRY_APPROVED"]
    rejected_attempts = [c for c in checkins if c.get("outcome") != "ENTRY_APPROVED"]

    entries_by_gate: Dict[str, int] = {}
    entries_by_area: Dict[str, int] = {}
    for sc in successful_entries:
        g = sc.get("gate_id", "UNKNOWN")
        a = sc.get("access_area_id", "UNKNOWN")
        entries_by_gate[g] = entries_by_gate.get(g, 0) + 1
        entries_by_area[a] = entries_by_area.get(a, 0) + 1

    rejection_reasons: Dict[str, int] = {}
    for rj in rejected_attempts:
        o = rj.get("outcome", "UNKNOWN")
        rejection_reasons[o] = rejection_reasons.get(o, 0) + 1

    return {
        "event_id": eid_str,
        "total_configurations": await vip_configuration_collection.count_documents({"event_id": eid_str}),
        "bookings_summary": {
            "total_bookings": len(bookings),
            "confirmed": sum(1 for b in bookings if b.get("booking_status") == "CONFIRMED"),
            "pending_payment": sum(1 for b in bookings if b.get("booking_status") == "PENDING_PAYMENT"),
            "cancelled": sum(1 for b in bookings if b.get("booking_status") == "CANCELLED"),
            "expired": sum(1 for b in bookings if b.get("booking_status") == "EXPIRED"),
            "total_revenue": total_revenue,
            "currency": currency
        },
        "passes_summary": {
            "total_issued": len(passes),
            "valid": sum(1 for p in passes if p.get("status") == "VALID"),
            "revoked": sum(1 for p in passes if p.get("status") == "REVOKED"),
            "expired": sum(1 for p in passes if p.get("status") == "EXPIRED"),
        },
        "entry_summary": {
            "successful_entries": len(successful_entries),
            "rejected_attempts": len(rejected_attempts),
            "entries_by_gate": entries_by_gate,
            "entries_by_area": entries_by_area,
            "rejections_breakdown": rejection_reasons
        }
    }

# ──────────────────────────────────────────────────────────
# VIP Passes & Issuance
# ──────────────────────────────────────────────────────────

@router.post("/events/{event_id}/vip-passes/issue", response_model=Dict[str, Any], status_code=status.HTTP_201_CREATED)
async def manual_issue_vip_pass(
    event_id: str,
    configuration_id: str = Query(...),
    req: VIPManualIssueReq = ...,
    admin: dict = Depends(get_current_admin)
):
    """
    Super Admin manually issues a VIP Pass.
    STRICT POLICY: Requires verified payment/invoice reference and positive amount_paid. Free passes prohibited.
    """
    return serialize_mongo(await VIPService.manual_issue_vip_pass(event_id, configuration_id, req, admin.get("email")))

@router.get("/events/{event_id}/vip-passes", response_model=Dict[str, Any])
async def list_admin_event_vip_passes(
    event_id: str,
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=100),
    skip: int = Query(0, ge=0),
    admin: dict = Depends(get_current_admin)
):
    """Super Admin inspects all issued VIP passes for an event with pagination and status filters."""
    query: Dict[str, Any] = {"event_id": str(event_id)}
    if status_filter:
        query["status"] = status_filter

    total = await vip_pass_collection.count_documents(query)
    passes = await vip_pass_collection.find(query).sort("issued_at", -1).skip(skip).limit(limit).to_list(length=limit)
    for p in passes:
        p["pass_id"] = str(p["_id"])
        p["_id"] = str(p["_id"])

    return serialize_mongo({"total": total, "skip": skip, "limit": limit, "passes": passes})

@router.get("/vip-passes/{pass_id}", response_model=Dict[str, Any])
async def get_admin_vip_pass_detail(
    pass_id: str,
    admin: dict = Depends(get_current_admin)
):
    """Super Admin retrieves detailed information for an individual VIP Pass."""
    p_oid = VIPService._to_object_id(pass_id)
    doc = await vip_pass_collection.find_one({"$or": [{"_id": p_oid}, {"_id": pass_id}]}) if p_oid else await vip_pass_collection.find_one({"_id": pass_id})
    if not doc:
        raise HTTPException(status_code=404, detail="VIP Pass not found.")
    doc["pass_id"] = str(doc["_id"])
    doc["_id"] = str(doc["_id"])
    return doc

@router.post("/vip-passes/{pass_id}/revoke", response_model=Dict[str, Any])
async def revoke_vip_pass(
    pass_id: str,
    req: VIPRevokeReq,
    admin: dict = Depends(get_current_admin)
):
    """Super Admin revokes a VIP pass with an auditable reason."""
    return serialize_mongo(await VIPService.revoke_pass(pass_id, req.reason, admin.get("email")))

@router.get("/vip-passes/{pass_id}/history", response_model=Dict[str, Any])
async def get_vip_pass_scan_history(
    pass_id: str,
    admin: dict = Depends(get_current_admin)
):
    """Super Admin inspects full check-in and scan history for a specific VIP pass."""
    checkins = await vip_checkin_collection.find({"pass_id": str(pass_id)}).sort("timestamp", -1).to_list(length=100)
    for c in checkins:
        c["_id"] = str(c["_id"])
    return {"pass_id": pass_id, "total_scans": len(checkins), "history": checkins}

# ──────────────────────────────────────────────────────────
# VIP Bookings Management
# ──────────────────────────────────────────────────────────

@router.get("/events/{event_id}/vip-bookings", response_model=Dict[str, Any])
async def list_admin_event_vip_bookings(
    event_id: str,
    booking_status: Optional[str] = None,
    limit: int = Query(50, ge=1, le=100),
    skip: int = Query(0, ge=0),
    admin: dict = Depends(get_current_admin)
):
    """Super Admin inspects all VIP bookings for an event."""
    query: Dict[str, Any] = {"event_id": str(event_id)}
    if booking_status:
        query["booking_status"] = booking_status

    total = await vip_booking_collection.count_documents(query)
    bookings = await vip_booking_collection.find(query).sort("booked_at", -1).skip(skip).limit(limit).to_list(length=limit)
    for b in bookings:
        b["booking_id"] = str(b["_id"])
        b["_id"] = str(b["_id"])

    return serialize_mongo({"total": total, "skip": skip, "limit": limit, "bookings": bookings})

@router.get("/vip-bookings/{booking_id}", response_model=Dict[str, Any])
async def get_admin_vip_booking_detail(
    booking_id: str,
    admin: dict = Depends(get_current_admin)
):
    """Super Admin retrieves detailed information for an individual VIP booking."""
    b_oid = VIPService._to_object_id(booking_id)
    doc = await vip_booking_collection.find_one({"$or": [{"_id": b_oid}, {"_id": booking_id}]}) if b_oid else await vip_booking_collection.find_one({"_id": booking_id})
    if not doc:
        raise HTTPException(status_code=404, detail="VIP Booking not found.")
    doc["booking_id"] = str(doc["_id"])
    doc["_id"] = str(doc["_id"])
    return doc

@router.post("/vip-bookings/{booking_id}/cancel", response_model=Dict[str, Any])
async def cancel_vip_booking(
    booking_id: str,
    reason: str = Query("Administrative cancellation"),
    admin: dict = Depends(get_current_admin)
):
    """Super Admin cancels a VIP booking and invalidates associated passes."""
    return serialize_mongo(await VIPService.cancel_booking(booking_id, reason, admin.get("email")))

@router.get("/events/{event_id}/vip-checkins", response_model=Dict[str, Any])
async def list_admin_vip_checkins(
    event_id: str,
    outcome: Optional[str] = None,
    gate_id: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    skip: int = Query(0, ge=0),
    admin: dict = Depends(get_current_admin)
):
    """Super Admin inspects chronological check-in and scan logs for an event."""
    query: Dict[str, Any] = {"event_id": str(event_id)}
    if outcome: query["outcome"] = outcome
    if gate_id: query["gate_id"] = gate_id

    total = await vip_checkin_collection.count_documents(query)
    records = await vip_checkin_collection.find(query).sort("timestamp", -1).skip(skip).limit(limit).to_list(length=limit)
    for r in records:
        r["_id"] = str(r["_id"])

    return {"total": total, "skip": skip, "limit": limit, "checkins": records}

# ──────────────────────────────────────────────────────────
# Entry Verification Endpoint (Admin / Staff Scanner)
# ──────────────────────────────────────────────────────────

@router.post("/vip-passes/verify", response_model=VIPVerifyResponse)
async def verify_vip_pass_entry(
    req: VIPVerifyReq,
    admin: dict = Depends(get_current_admin)
):
    """
    Authoritative Gate Entry & Access Control Verification.
    Validates token, event match, active status, gate/access area permission, single vs re-entry rules.
    """
    res = await VIPService.verify_entry(req, operator_email=admin.get("email", "admin"))
    return res
