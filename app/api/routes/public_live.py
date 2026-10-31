"""
Public Live Events & Competitions API
======================================
Returns only currently-live events and active competitions.
All endpoints are public (no authentication required).

Live Events:   Events from live_event_collection that are published, visible,
               and currently within their scheduled broadcast window.
               Private stream URLs are NEVER exposed.

Live Competitions: Competitions from competition_collection that are published,
                   actively running (dates in window, or voting/judging open,
                   or published without closed results).
"""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional, List, Any
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
from app.services.competition_lifecycle import evaluate_competition_registration_status
from app.services.competition_stage_service import CompetitionStageService

router = APIRouter()

# ── Helpers ──────────────────────────────────────────────────────────────────

PRIVATE_EVENT_FIELDS = {"stream_url", "admin_notes", "internal_notes"}
PRIVATE_COMP_FIELDS = {"admin_notes", "internal_notes"}


def _parse_to_utc(val: Any) -> Optional[datetime]:
    """Safely convert any datetime object or string representation into a UTC-aware datetime."""
    if not val:
        return None
    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=timezone.utc)
        return val.astimezone(timezone.utc)
    if isinstance(val, str):
        val = val.strip()
        if not val:
            return None
        # ISO format with Z or timezone
        try:
            dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            for fmt in (
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M",
                "%Y-%m-%d",
                "%d-%m-%Y %H:%M:%S",
                "%d-%m-%Y",
                "%d/%m/%Y",
            ):
                try:
                    dt = datetime.strptime(val, fmt)
                    return dt.replace(tzinfo=timezone.utc)
                except ValueError:
                    pass
    return None


def _sanitise_obj(obj: Any) -> Any:
    """Recursively convert MongoDB ObjectId instances to strings."""
    if isinstance(obj, ObjectId):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _sanitise_obj(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_sanitise_obj(v) for v in obj]
    return obj


def _sanitise_id(doc: dict) -> dict:
    """Recursively convert all MongoDB ObjectIds inside a doc to string."""
    if not doc:
        return doc
    return _sanitise_obj(doc)


def _strip_private(doc: dict, keys: set) -> dict:
    """Remove private / internal fields before public response."""
    for k in keys:
        doc.pop(k, None)
    return doc


def _add_competition_live_status(comp: dict, now: datetime) -> dict:
    """Enrich a competition doc with real-time computed status flags."""
    start = _parse_to_utc(comp.get("start_date"))
    end = _parse_to_utc(comp.get("end_date"))
    reg_start = _parse_to_utc(comp.get("registration_start_date"))
    reg_deadline = _parse_to_utc(comp.get("registration_deadline"))
    sub_deadline = _parse_to_utc(comp.get("submission_deadline"))

    voting_status = str(comp.get("voting_status") or "").upper()
    judging_status = str(comp.get("judging_status") or "").upper()
    audience_voting_enabled = bool(comp.get("audience_voting_enabled"))
    judge_scoring_enabled = bool(comp.get("judge_scoring_enabled"))

    # Compute dynamic phase
    if start and end:
        if now < start:
            comp["live_phase"] = "UPCOMING"
        elif now > end:
            if (audience_voting_enabled and voting_status == "OPEN") or (judge_scoring_enabled and judging_status == "OPEN"):
                comp["live_phase"] = "VOTING" if voting_status == "OPEN" else "JUDGING"
            else:
                comp["live_phase"] = "ENDED"
        else:
            comp["live_phase"] = "LIVE"
    elif audience_voting_enabled and voting_status == "OPEN":
        comp["live_phase"] = "VOTING"
    elif judge_scoring_enabled and judging_status == "OPEN":
        comp["live_phase"] = "JUDGING"
    elif comp.get("is_published") or comp.get("status") == "PUBLISHED":
        comp["live_phase"] = "LIVE"
    else:
        comp["live_phase"] = "UNKNOWN"

    # Real-time registration lifecycle evaluation (date window, manual admin lock, capacity)
    reg_eval = evaluate_competition_registration_status(comp, now=now)
    comp["registration_open"] = reg_eval["can_register"]
    comp["registration_status"] = reg_eval["registration_status"]
    comp["can_register"] = reg_eval["can_register"]
    comp["registration_reason"] = reg_eval["reason"]

    # Submission window
    if sub_deadline:
        if start:
            comp["submissions_open"] = start <= now <= sub_deadline
        else:
            comp["submissions_open"] = now <= sub_deadline
    else:
        comp["submissions_open"] = False

    return comp


def _build_live_competition_query(
    now: datetime,
    category: Optional[str] = None,
    voting_open: Optional[bool] = None,
    judging_open: Optional[bool] = None,
) -> dict:
    """Builds MongoDB query matching actively live / running competitions."""
    query: dict = {
        "$or": [
            {"is_published": True},
            {"status": "PUBLISHED"},
        ],
        "results_published": {"$ne": True},
        "result_status": {"$nin": ["PUBLISHED", "COMPLETED", "ARCHIVED"]},
        "status": {"$nin": ["ENDED", "CLOSED", "ARCHIVED", "COMPLETED"]},
        "$and": [
            {
                "$or": [
                    {"start_date": {"$lte": now}, "end_date": {"$gte": now}},
                    {"start_date": {"$lte": now}, "end_date": None},
                    {"voting_status": "OPEN"},
                    {"judging_status": "OPEN"},
                    {"audience_voting_enabled": True},
                    {"judge_scoring_enabled": True},
                    {"start_date": None, "end_date": None},
                ]
            }
        ],
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

    return query


# ── GET /live/feed  ──────────────────────────────────────────────────────────

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
    * **live_competitions**: Competitions that are published and currently running.
    """
    now = datetime.now(timezone.utc)

    # ── Live Events ──────────────────────────────────────────────────────
    live_events_raw = await live_event_collection.find(
        {"is_published": True, "is_visible": True}
    ).sort("display_order", 1).to_list(length=50)

    live_events = []
    for le in live_events_raw:
        le = _sanitise_id(le)
        _strip_private(le, PRIVATE_EVENT_FIELDS)

        # Optionally filter by scheduled window if both dates exist and are parseable
        sched_start = _parse_to_utc(le.get("scheduled_start") or le.get("start_time"))
        sched_end = _parse_to_utc(le.get("scheduled_end") or le.get("end_time"))
        if sched_start and sched_end:
            if not (sched_start <= now <= sched_end):
                continue  # Outside broadcast window

        live_events.append(le)

    # ── Live Competitions ────────────────────────────────────────────────
    comp_query = _build_live_competition_query(now)
    live_comps_raw = await competition_collection.find(comp_query).sort(
        "created_at", -1
    ).to_list(length=50)

    live_competitions = []
    for comp in live_comps_raw:
        comp = _sanitise_id(comp)
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

    # Safe coercion if called programmatically
    if not isinstance(skip, int):
        skip = getattr(skip, "default", 0)
    if not isinstance(limit, int):
        limit = getattr(limit, "default", 20)

    raw = await live_event_collection.find(
        {"is_published": True, "is_visible": True}
    ).sort("display_order", 1).skip(skip).limit(limit).to_list(length=limit)

    events = []
    for le in raw:
        le = _sanitise_id(le)
        _strip_private(le, PRIVATE_EVENT_FIELDS)

        sched_start = _parse_to_utc(le.get("scheduled_start") or le.get("start_time"))
        sched_end = _parse_to_utc(le.get("scheduled_end") or le.get("end_time"))
        if sched_start and sched_end:
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
    description="Returns only currently-running competitions with optional filters.",
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

    # Safe coercion if called programmatically
    if not isinstance(skip, int):
        skip = getattr(skip, "default", 0)
    if not isinstance(limit, int):
        limit = getattr(limit, "default", 20)
    if not isinstance(category, (str, type(None))):
        category = getattr(category, "default", None)
    if not isinstance(voting_open, (bool, type(None))):
        voting_open = getattr(voting_open, "default", None)
    if not isinstance(judging_open, (bool, type(None))):
        judging_open = getattr(judging_open, "default", None)

    query = _build_live_competition_query(
        now=now,
        category=category,
        voting_open=voting_open,
        judging_open=judging_open,
    )

    raw = await competition_collection.find(query).sort(
        "created_at", -1
    ).skip(skip).limit(limit).to_list(length=limit)

    competitions = []
    for comp in raw:
        comp = _sanitise_id(comp)
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

    Returns 404 if the competition doesn't exist or isn't published.
    """
    now = datetime.now(timezone.utc)

    if not ObjectId.is_valid(comp_id):
        raise HTTPException(status_code=400, detail="Invalid competition ID format.")

    comp = await competition_collection.find_one({
        "_id": ObjectId(comp_id),
        "$or": [
            {"is_published": True},
            {"status": "PUBLISHED"},
        ],
    })

    if not comp:
        raise HTTPException(status_code=404, detail="Competition not found or not published.")

    comp = _sanitise_id(comp)
    _strip_private(comp, PRIVATE_COMP_FIELDS)
    _add_competition_live_status(comp, now)

    # Check if upcoming or ended
    start = _parse_to_utc(comp.get("start_date"))
    end = _parse_to_utc(comp.get("end_date"))
    voting_status = str(comp.get("voting_status") or "").upper()
    judging_status = str(comp.get("judging_status") or "").upper()

    if start and now < start:
        raise HTTPException(
            status_code=404,
            detail="This competition has not started yet."
        )

    if end and now > end:
        # If voting or judging is still actively open, allow access
        if not (voting_status == "OPEN" or judging_status == "OPEN"):
            if comp.get("results_published") is True or comp.get("result_status") == "PUBLISHED":
                raise HTTPException(
                    status_code=404,
                    detail="This competition has ended and results are published."
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
        {
            "competition_id": {"$in": comp_id_match},
            "status": {"$in": ["APPROVED", "SUBMITTED", "PENDING", "COMPLETED"]},
        }
    )

    # Aggregate total audience votes
    vote_pipeline = [
        {"$match": {"competition_id": {"$in": comp_id_match}}},
        {"$group": {"_id": None, "total": {"$sum": {"$ifNull": ["$voting_power", 1]}}}},
    ]
    vote_agg = await audience_vote_collection.aggregate(vote_pipeline).to_list(1)
    total_audience_votes = int(vote_agg[0]["total"]) if vote_agg else 0

    comp["live_stats"] = {
        "total_registrations": total_registrations,
        "total_submissions": total_submissions,
        "total_audience_votes": total_audience_votes,
    }

    comp["live_stage"] = comp.get("live_stage") or {
        "stage_status": "IDLE",
        "current_performer": None
    }

    return comp


@router.get(
    "/live/competitions/{comp_id}/live-stage",
    summary="Live Competition Stage Status",
    description="Returns who is currently performing on stage, queue count, and stage status."
)
@router.get(
    "/live/competitions/{comp_id}/current-performer",
    summary="Live Competition Current Performer",
    description="Alias for live stage status."
)
async def get_live_competition_stage(comp_id: str):
    """Public real-time view of current on-stage performer for live competitions."""
    return await CompetitionStageService.get_public_stage_status(comp_id)



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

    le = _sanitise_id(le)
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

    comp_query = _build_live_competition_query(now)
    live_competitions_count = await competition_collection.count_documents(comp_query)

    voting_open_count = await competition_collection.count_documents({
        **comp_query,
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
