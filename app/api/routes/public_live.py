"""
Public Live Events & Competitions API
======================================
Returns only currently-live events and active competitions.
All endpoints are public (no authentication required).

Live Events:   Events from live_event_collection that are published, visible,
               and currently within their scheduled broadcast window.
               Private stream URLs are NEVER exposed.

Live Competitions: Competitions from competition_collection that are published,
                   currently running (start_date <= now <= end_date), and have
                   at least one active judging/voting channel open.
"""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional, List
from datetime import datetime, timezone
from bson import ObjectId

from app.core.database import (
    live_event_collection,
    event_collection,
    competition_collection,
    competition_registration_collection,
    competition_submission_collection,
    audience_vote_collection,
)

router = APIRouter()

# ── Helpers ──────────────────────────────────────────────────────────────────

PRIVATE_EVENT_FIELDS = {"stream_url", "admin_notes", "internal_notes"}
PRIVATE_COMP_FIELDS = {"admin_notes", "internal_notes"}


def _sanitise_id(doc: dict) -> dict:
    """Convert MongoDB ObjectId to string."""
    if doc and "_id" in doc:
        doc["_id"] = str(doc["_id"])
    return doc


def _strip_private(doc: dict, keys: set) -> dict:
    """Remove private / internal fields before public response."""
    for k in keys:
        doc.pop(k, None)
    return doc


def _add_competition_live_status(comp: dict, now: datetime) -> dict:
    """Enrich a competition doc with real-time computed status flags."""
    start = comp.get("start_date")
    end = comp.get("end_date")
    reg_deadline = comp.get("registration_deadline")
    sub_deadline = comp.get("submission_deadline")

    # Normalise naive datetimes to UTC
    for dt_field in (start, end, reg_deadline, sub_deadline):
        if dt_field and dt_field.tzinfo is None:
            dt_field = dt_field.replace(tzinfo=timezone.utc)

    # Compute dynamic phase
    if start and end:
        if now < start:
            comp["live_phase"] = "UPCOMING"
        elif now > end:
            comp["live_phase"] = "ENDED"
        else:
            comp["live_phase"] = "LIVE"
    else:
        comp["live_phase"] = "UNKNOWN"

    # Registration window
    reg_start = comp.get("registration_start_date")
    if reg_start and reg_start.tzinfo is None:
        reg_start = reg_start.replace(tzinfo=timezone.utc)
    if reg_start and reg_deadline:
        if reg_deadline.tzinfo is None:
            reg_deadline = reg_deadline.replace(tzinfo=timezone.utc)
        comp["registration_open"] = reg_start <= now <= reg_deadline
    else:
        comp["registration_open"] = False

    # Submission window
    if sub_deadline:
        if sub_deadline.tzinfo is None:
            sub_deadline = sub_deadline.replace(tzinfo=timezone.utc)
        if start:
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            comp["submissions_open"] = start <= now <= sub_deadline
        else:
            comp["submissions_open"] = now <= sub_deadline
    else:
        comp["submissions_open"] = False

    return comp


# ── GET /live/feed  ──────────────────────────────────────────────────────────
# Combined feed: live events + live competitions in a single response.

@router.get(
    "/live/feed",
    summary="Live Feed - Events & Competitions",
    description=(
        "Returns a combined feed of all currently-live broadcast events and "
        "all currently-running competitions. No authentication required."
    ),
    response_description="Combined live events and competitions feed.",
)
async def get_live_feed():
    """
    **Public endpoint** – returns everything that is live *right now*.

    * **live_events**: Broadcast / streaming events that are published and visible.
    * **live_competitions**: Competitions whose `start_date ≤ now ≤ end_date`,
      that are published, and have voting or judging open.
    """
    now = datetime.now(timezone.utc)

    # ── Live Events ──────────────────────────────────────────────────────
    live_events_raw = await live_event_collection.find(
        {"is_published": True, "is_visible": True}
    ).sort("display_order", 1).to_list(length=50)

    live_events = []
    for le in live_events_raw:
        _sanitise_id(le)
        _strip_private(le, PRIVATE_EVENT_FIELDS)

        # Optionally filter by scheduled window if dates exist
        sched_start = le.get("scheduled_start") or le.get("start_time")
        sched_end = le.get("scheduled_end") or le.get("end_time")
        if sched_start and sched_end:
            if sched_start.tzinfo is None:
                sched_start = sched_start.replace(tzinfo=timezone.utc)
            if sched_end.tzinfo is None:
                sched_end = sched_end.replace(tzinfo=timezone.utc)
            if not (sched_start <= now <= sched_end):
                continue  # Not within broadcast window – skip

        live_events.append(le)

    # ── Live Competitions ────────────────────────────────────────────────
    live_comps_raw = await competition_collection.find({
        "is_published": True,
        "start_date": {"$lte": now},
        "end_date": {"$gte": now},
    }).sort("start_date", 1).to_list(length=50)

    live_competitions = []
    for comp in live_comps_raw:
        _sanitise_id(comp)
        _strip_private(comp, PRIVATE_COMP_FIELDS)
        _add_competition_live_status(comp, now)
        live_competitions.append(comp)

    return {
        "timestamp": now.isoformat(),
        "live_events": live_events,
        "live_events_count": len(live_events),
        "live_competitions": live_competitions,
        "live_competitions_count": len(live_competitions),
        "total_live": len(live_events) + len(live_competitions),
    }


# ── GET /live/events  ────────────────────────────────────────────────────────

@router.get(
    "/live/events",
    summary="Live Broadcast Events",
    description="Returns only currently-live broadcast / streaming events.",
)
async def get_live_events_only(
    skip: int = Query(0, ge=0, description="Number of items to skip"),
    limit: int = Query(20, ge=1, le=100, description="Max items to return"),
):
    """Returns published & visible live-broadcast events with pagination."""
    now = datetime.now(timezone.utc)

    raw = await live_event_collection.find(
        {"is_published": True, "is_visible": True}
    ).sort("display_order", 1).skip(skip).limit(limit).to_list(length=limit)

    events = []
    for le in raw:
        _sanitise_id(le)
        _strip_private(le, PRIVATE_EVENT_FIELDS)

        sched_start = le.get("scheduled_start") or le.get("start_time")
        sched_end = le.get("scheduled_end") or le.get("end_time")
        if sched_start and sched_end:
            if sched_start.tzinfo is None:
                sched_start = sched_start.replace(tzinfo=timezone.utc)
            if sched_end.tzinfo is None:
                sched_end = sched_end.replace(tzinfo=timezone.utc)
            if not (sched_start <= now <= sched_end):
                continue

        events.append(le)

    total = await live_event_collection.count_documents(
        {"is_published": True, "is_visible": True}
    )

    return {
        "items": events,
        "count": len(events),
        "total": total,
        "skip": skip,
        "limit": limit,
    }


# ── GET /live/competitions  ──────────────────────────────────────────────────

@router.get(
    "/live/competitions",
    summary="Live Competitions",
    description=(
        "Returns only currently-running competitions "
        "(start_date <= now <= end_date, published)."
    ),
)
async def get_live_competitions_only(
    category: Optional[str] = Query(None, description="Filter by category"),
    voting_open: Optional[bool] = Query(None, description="Filter by voting status"),
    judging_open: Optional[bool] = Query(None, description="Filter by judging status"),
    skip: int = Query(0, ge=0, description="Number of items to skip"),
    limit: int = Query(20, ge=1, le=100, description="Max items to return"),
):
    """
    Returns published competitions that are currently running, with
    optional filters for category, voting status, and judging status.
    """
    now = datetime.now(timezone.utc)

    query: dict = {
        "is_published": True,
        "start_date": {"$lte": now},
        "end_date": {"$gte": now},
    }

    if category:
        query["category"] = {"$regex": f"^{category}$", "$options": "i"}

    if voting_open is True:
        query["audience_voting_enabled"] = True
        query["voting_status"] = "OPEN"
    elif voting_open is False:
        query["$or"] = [
            {"audience_voting_enabled": False},
            {"voting_status": {"$ne": "OPEN"}},
        ]

    if judging_open is True:
        query["judge_scoring_enabled"] = True
        query["judging_status"] = "OPEN"
    elif judging_open is False:
        query["$or"] = [
            {"judge_scoring_enabled": False},
            {"judging_status": {"$ne": "OPEN"}},
        ]

    raw = await competition_collection.find(query).sort(
        "start_date", 1
    ).skip(skip).limit(limit).to_list(length=limit)

    competitions = []
    for comp in raw:
        _sanitise_id(comp)
        _strip_private(comp, PRIVATE_COMP_FIELDS)
        _add_competition_live_status(comp, now)
        competitions.append(comp)

    total = await competition_collection.count_documents(query)

    return {
        "items": competitions,
        "count": len(competitions),
        "total": total,
        "skip": skip,
        "limit": limit,
    }


# ── GET /live/competitions/{comp_id}  ────────────────────────────────────────

@router.get(
    "/live/competitions/{comp_id}",
    summary="Live Competition Detail",
    description="Returns full detail for a single live competition with live stats.",
)
async def get_live_competition_detail(comp_id: str):
    """
    Returns a single competition's detail enriched with live statistics:
    total registrations, total submissions, total audience votes,
    and real-time status flags.

    Returns 404 if the competition doesn't exist or isn't currently live.
    """
    now = datetime.now(timezone.utc)

    if not ObjectId.is_valid(comp_id):
        raise HTTPException(status_code=400, detail="Invalid competition ID format.")

    comp = await competition_collection.find_one({
        "_id": ObjectId(comp_id),
        "is_published": True,
    })

    if not comp:
        raise HTTPException(status_code=404, detail="Competition not found or not published.")

    _sanitise_id(comp)
    _strip_private(comp, PRIVATE_COMP_FIELDS)
    _add_competition_live_status(comp, now)

    # Check if actually live
    start = comp.get("start_date")
    end = comp.get("end_date")
    if start and end:
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        if now < start or now > end:
            raise HTTPException(
                status_code=404,
                detail="This competition is not currently live."
            )

    # Enrich with live statistics
    comp_id_str = str(comp["_id"])
    comp_id_match = [comp_id_str]
    if ObjectId.is_valid(comp_id_str):
        comp_id_match.append(ObjectId(comp_id_str))

    total_registrations = await competition_registration_collection.count_documents(
        {"competition_id": {"$in": comp_id_match}}
    )
    total_submissions = await competition_submission_collection.count_documents(
        {"competition_id": {"$in": comp_id_match},
         "status": {"$in": ["APPROVED", "SUBMITTED", "PENDING", "COMPLETED"]}}
    )

    # Aggregate total audience votes
    vote_pipeline = [
        {"$match": {"competition_id": {"$in": [comp_id_str]}}},
        {"$group": {"_id": None, "total": {"$sum": "$voting_power"}}},
    ]
    vote_agg = await audience_vote_collection.aggregate(vote_pipeline).to_list(1)
    total_audience_votes = int(vote_agg[0]["total"]) if vote_agg else 0

    comp["live_stats"] = {
        "total_registrations": total_registrations,
        "total_submissions": total_submissions,
        "total_audience_votes": total_audience_votes,
    }

    return comp


# ── GET /live/events/{event_id}  ─────────────────────────────────────────────

@router.get(
    "/live/events/{event_id}",
    summary="Live Event Detail",
    description="Returns full detail for a single live broadcast event.",
)
async def get_live_event_detail(event_id: str):
    """
    Returns a single live event's detail.
    Returns 404 if the event doesn't exist or isn't published/visible.
    Private fields (stream_url, admin_notes) are never exposed.
    """
    if not ObjectId.is_valid(event_id):
        raise HTTPException(status_code=400, detail="Invalid event ID format.")

    le = await live_event_collection.find_one({
        "_id": ObjectId(event_id),
        "is_published": True,
        "is_visible": True,
    })

    if not le:
        raise HTTPException(status_code=404, detail="Live event not found or not visible.")

    _sanitise_id(le)
    _strip_private(le, PRIVATE_EVENT_FIELDS)

    return le


# ── GET /live/summary  ──────────────────────────────────────────────────────

@router.get(
    "/live/summary",
    summary="Live Summary / Counts",
    description=(
        "Returns quick counts of currently-live events and competitions. "
        "Useful for badges, indicators, and real-time counters on the frontend."
    ),
)
async def get_live_summary():
    """
    Lightweight endpoint returning only counts – ideal for polling or
    frontend badge indicators (e.g. "3 Live Now").
    """
    now = datetime.now(timezone.utc)

    live_events_count = await live_event_collection.count_documents(
        {"is_published": True, "is_visible": True}
    )

    live_competitions_count = await competition_collection.count_documents({
        "is_published": True,
        "start_date": {"$lte": now},
        "end_date": {"$gte": now},
    })

    voting_open_count = await competition_collection.count_documents({
        "is_published": True,
        "start_date": {"$lte": now},
        "end_date": {"$gte": now},
        "audience_voting_enabled": True,
        "voting_status": "OPEN",
    })

    return {
        "timestamp": now.isoformat(),
        "live_events_count": live_events_count,
        "live_competitions_count": live_competitions_count,
        "voting_open_count": voting_open_count,
        "total_live": live_events_count + live_competitions_count,
    }
