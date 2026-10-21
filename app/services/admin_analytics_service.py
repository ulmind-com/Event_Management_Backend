"""
Admin Analytics Service
========================
Production-grade analytics aggregation for the admin dashboard.
Provides buyer insights across tickets, competitions, VIP passes,
merchandise, and unified revenue metrics.
"""

from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from bson import ObjectId

from app.core.database import (
    booking_collection,
    issued_ticket_collection,
    ticket_collection,
    event_collection,
    registration_collection,
    competition_collection,
    course_collection,
    vip_booking_collection,
    vip_pass_collection,
    vip_configuration_collection,
    merchandise_order_collection,
    merchandise_product_collection,
    user_collection,
)


def _serialize(doc: dict) -> dict:
    """Recursively serialize MongoDB documents for JSON output."""
    if doc is None:
        return {}
    out = {}
    for k, v in doc.items():
        if isinstance(v, ObjectId):
            out[k] = str(v)
        elif isinstance(v, datetime):
            out[k] = v.isoformat()
        elif isinstance(v, dict):
            out[k] = _serialize(v)
        elif isinstance(v, list):
            out[k] = [_serialize(i) if isinstance(i, dict) else (str(i) if isinstance(i, ObjectId) else i) for i in v]
        else:
            out[k] = v
    return out


async def _enrich_user(email: str) -> Dict[str, Any]:
    """Fetch user profile basics by email for buyer details."""
    if not email:
        return {"email": email}
    user = await user_collection.find_one(
        {"email": email},
        {"full_name": 1, "phone": 1, "profile_image": 1, "email": 1, "role": 1, "is_verified": 1}
    )
    if user:
        return {
            "email": email,
            "full_name": user.get("full_name", ""),
            "phone": user.get("phone", ""),
            "profile_image": user.get("profile_image", ""),
            "role": user.get("role", "user"),
            "is_verified": user.get("is_verified", False),
        }
    return {"email": email}


async def _enrich_users_bulk(emails: List[str]) -> Dict[str, Dict[str, Any]]:
    """Bulk-fetch user profiles for a list of emails (deduped)."""
    unique_emails = list(set(e for e in emails if e))
    if not unique_emails:
        return {}
    users = await user_collection.find(
        {"email": {"$in": unique_emails}},
        {"full_name": 1, "phone": 1, "profile_image": 1, "email": 1, "role": 1, "is_verified": 1}
    ).to_list(length=len(unique_emails))
    
    lookup = {}
    for u in users:
        lookup[u["email"]] = {
            "email": u["email"],
            "full_name": u.get("full_name", ""),
            "phone": u.get("phone", ""),
            "profile_image": u.get("profile_image", ""),
            "role": u.get("role", "user"),
            "is_verified": u.get("is_verified", False),
        }
    return lookup


class AdminAnalyticsService:
    """Centralized analytics aggregation for admin dashboard."""

    # ──────────────────────────────────────────────────────────
    # 1. UNIFIED DASHBOARD
    # ──────────────────────────────────────────────────────────

    @staticmethod
    async def get_unified_dashboard() -> Dict[str, Any]:
        """
        High-level admin overview across all modules.
        Returns total counts and revenue for bookings, registrations, VIP, merchandise.
        """
        # Ticket bookings
        total_bookings = await booking_collection.count_documents({})
        confirmed_bookings = await booking_collection.count_documents({"booking_status": "CONFIRMED"})
        pending_bookings = await booking_collection.count_documents({"booking_status": "PENDING"})
        cancelled_bookings = await booking_collection.count_documents({"booking_status": "CANCELLED"})

        # Booking revenue (confirmed only)
        booking_revenue_pipeline = [
            {"$match": {"booking_status": "CONFIRMED"}},
            {"$group": {"_id": None, "total": {"$sum": "$total_amount"}}}
        ]
        booking_rev = await booking_collection.aggregate(booking_revenue_pipeline).to_list(length=1)
        booking_revenue = booking_rev[0]["total"] if booking_rev else 0.0

        # Registrations
        total_registrations = await registration_collection.count_documents({})
        confirmed_registrations = await registration_collection.count_documents({"status": "CONFIRMED"})

        # Registration revenue
        reg_revenue_pipeline = [
            {"$match": {"status": "CONFIRMED", "payable_amount": {"$gt": 0}}},
            {"$group": {"_id": None, "total": {"$sum": "$payable_amount"}}}
        ]
        reg_rev = await registration_collection.aggregate(reg_revenue_pipeline).to_list(length=1)
        registration_revenue = reg_rev[0]["total"] if reg_rev else 0.0

        # VIP
        total_vip_bookings = await vip_booking_collection.count_documents({})
        confirmed_vip = await vip_booking_collection.count_documents({"booking_status": "CONFIRMED"})
        vip_revenue_pipeline = [
            {"$match": {"booking_status": "CONFIRMED"}},
            {"$group": {"_id": None, "total": {"$sum": "$total_amount"}}}
        ]
        vip_rev = await vip_booking_collection.aggregate(vip_revenue_pipeline).to_list(length=1)
        vip_revenue = vip_rev[0]["total"] if vip_rev else 0.0

        # Merchandise
        total_merch_orders = await merchandise_order_collection.count_documents({})
        confirmed_merch = await merchandise_order_collection.count_documents({"payment_status": "PAID"})
        merch_revenue_pipeline = [
            {"$match": {"payment_status": "PAID"}},
            {"$group": {"_id": None, "total": {"$sum": "$total_amount"}}}
        ]
        merch_rev = await merchandise_order_collection.aggregate(merch_revenue_pipeline).to_list(length=1)
        merch_revenue = merch_rev[0]["total"] if merch_rev else 0.0

        # Unique buyers across all modules
        booking_emails = await booking_collection.distinct("user_email")
        reg_emails = await registration_collection.distinct("email")
        vip_emails = await vip_booking_collection.distinct("user_email")
        merch_emails = await merchandise_order_collection.distinct("user_email")
        all_unique_buyers = set(booking_emails + reg_emails + vip_emails + merch_emails)

        total_revenue = float(booking_revenue) + float(registration_revenue) + float(vip_revenue) + float(merch_revenue)

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "tickets": {
                "total_bookings": total_bookings,
                "confirmed": confirmed_bookings,
                "pending": pending_bookings,
                "cancelled": cancelled_bookings,
                "revenue": float(booking_revenue),
            },
            "registrations": {
                "total": total_registrations,
                "confirmed": confirmed_registrations,
                "revenue": float(registration_revenue),
            },
            "vip_passes": {
                "total_bookings": total_vip_bookings,
                "confirmed": confirmed_vip,
                "revenue": float(vip_revenue),
            },
            "merchandise": {
                "total_orders": total_merch_orders,
                "paid": confirmed_merch,
                "revenue": float(merch_revenue),
            },
            "overall": {
                "total_revenue": total_revenue,
                "unique_buyers": len(all_unique_buyers),
                "currency": "INR",
            }
        }

    # ──────────────────────────────────────────────────────────
    # 2. TICKET BUYERS
    # ──────────────────────────────────────────────────────────

    @staticmethod
    async def get_event_ticket_buyers(
        event_id: str,
        status_filter: Optional[str] = None,
        skip: int = 0,
        limit: int = 50
    ) -> Dict[str, Any]:
        """
        Returns all ticket buyers for a specific event with enriched user details.
        """
        query: Dict[str, Any] = {"event_id": event_id}
        if status_filter:
            query["booking_status"] = status_filter

        total = await booking_collection.count_documents(query)
        bookings = await booking_collection.find(query).sort("booked_at", -1).skip(skip).limit(limit).to_list(length=limit)

        # Bulk enrich users
        emails = [b.get("user_email", "") for b in bookings]
        user_lookup = await _enrich_users_bulk(emails)

        # Fetch event info
        event = None
        if ObjectId.is_valid(event_id):
            event = await event_collection.find_one({"_id": ObjectId(event_id)})

        # Fetch ticket info for each booking
        ticket_ids = list(set(b.get("ticket_id", "") for b in bookings if b.get("ticket_id")))
        ticket_lookup = {}
        for tid in ticket_ids:
            if ObjectId.is_valid(tid):
                t = await ticket_collection.find_one({"_id": ObjectId(tid)})
                if t:
                    ticket_lookup[tid] = {
                        "type": t.get("type", ""),
                        "name": t.get("name", t.get("type", "")),
                        "price": float(t.get("price", 0.0)),
                    }

        buyers = []
        for b in bookings:
            email = b.get("user_email", "")
            user_info = user_lookup.get(email, {"email": email})
            tid = b.get("ticket_id", "")

            buyers.append({
                "booking_id": str(b["_id"]),
                "booking_reference": b.get("booking_reference", ""),
                "user": user_info,
                "ticket": ticket_lookup.get(tid, {"type": "unknown"}),
                "quantity": b.get("quantity", 1),
                "unit_price": float(b.get("unit_price", 0.0)),
                "total_amount": float(b.get("total_amount", 0.0)),
                "booking_status": b.get("booking_status", ""),
                "payment_status": b.get("payment_status", ""),
                "booked_at": b.get("booked_at").isoformat() if b.get("booked_at") else None,
            })

        return {
            "event_id": event_id,
            "event_title": event.get("title", "") if event else "",
            "total_buyers": total,
            "skip": skip,
            "limit": limit,
            "buyers": buyers,
        }

    @staticmethod
    async def get_event_ticket_summary(event_id: str) -> Dict[str, Any]:
        """
        Per-ticket-type breakdown: sold count, revenue, remaining capacity.
        """
        tickets = await ticket_collection.find({"event_id": event_id}).to_list(length=50)
        event = None
        if ObjectId.is_valid(event_id):
            event = await event_collection.find_one({"_id": ObjectId(event_id)})

        summaries = []
        total_sold = 0
        total_revenue = 0.0

        for t in tickets:
            tid = str(t["_id"])
            # Count confirmed bookings for this ticket type
            confirmed = await booking_collection.count_documents({
                "event_id": event_id,
                "ticket_id": tid,
                "booking_status": "CONFIRMED"
            })
            pending = await booking_collection.count_documents({
                "event_id": event_id,
                "ticket_id": tid,
                "booking_status": "PENDING"
            })

            # Revenue for this ticket type
            rev_pipeline = [
                {"$match": {"event_id": event_id, "ticket_id": tid, "booking_status": "CONFIRMED"}},
                {"$group": {"_id": None, "total": {"$sum": "$total_amount"}, "qty": {"$sum": "$quantity"}}}
            ]
            rev_result = await booking_collection.aggregate(rev_pipeline).to_list(length=1)
            revenue = float(rev_result[0]["total"]) if rev_result else 0.0
            qty_sold = rev_result[0]["qty"] if rev_result else 0

            total_capacity = int(t.get("quantity", 0))
            available = int(t.get("available", 0))

            summaries.append({
                "ticket_id": tid,
                "ticket_type": t.get("type", ""),
                "ticket_name": t.get("name", t.get("type", "")),
                "price": float(t.get("price", 0.0)),
                "total_capacity": total_capacity,
                "available": available,
                "sold": qty_sold,
                "confirmed_bookings": confirmed,
                "pending_bookings": pending,
                "revenue": revenue,
            })

            total_sold += qty_sold
            total_revenue += revenue

        return {
            "event_id": event_id,
            "event_title": event.get("title", "") if event else "",
            "total_ticket_types": len(summaries),
            "total_sold": total_sold,
            "total_revenue": total_revenue,
            "currency": "INR",
            "ticket_breakdown": summaries,
        }

    # ──────────────────────────────────────────────────────────
    # 3. COMPETITION REGISTRANTS
    # ──────────────────────────────────────────────────────────

    @staticmethod
    async def get_competition_registrants(
        comp_id: str,
        status_filter: Optional[str] = None,
        skip: int = 0,
        limit: int = 50
    ) -> Dict[str, Any]:
        """
        Returns all registrants for a specific competition with user details.
        """
        query: Dict[str, Any] = {
            "activity_id": comp_id,
            "activity_type": "competition"
        }
        if status_filter:
            query["status"] = status_filter

        total = await registration_collection.count_documents(query)
        regs = await registration_collection.find(query).sort("created_at", -1).skip(skip).limit(limit).to_list(length=limit)

        # Fetch competition info
        comp = None
        if ObjectId.is_valid(comp_id):
            comp = await competition_collection.find_one({"_id": ObjectId(comp_id)})

        # Bulk enrich users
        emails = [r.get("email", "") for r in regs]
        user_lookup = await _enrich_users_bulk(emails)

        registrants = []
        for r in regs:
            email = r.get("email", "")
            user_info = user_lookup.get(email, {"email": email})
            registrants.append({
                "registration_id": str(r["_id"]),
                "user": user_info,
                "participant_name": r.get("participant_name", ""),
                "status": r.get("status", ""),
                "price_snapshot": float(r.get("price_snapshot", 0.0)),
                "payable_amount": float(r.get("payable_amount", 0.0)),
                "coins_applied": r.get("coins_applied", 0),
                "discount_amount": float(r.get("discount_amount", 0.0)),
                "order_reference": r.get("order_reference", ""),
                "payment_status": r.get("payment_status", ""),
                "registered_at": r.get("created_at").isoformat() if r.get("created_at") else None,
            })

        return {
            "competition_id": comp_id,
            "competition_title": comp.get("title", "") if comp else "",
            "total_registrants": total,
            "skip": skip,
            "limit": limit,
            "registrants": registrants,
        }

    @staticmethod
    async def get_competitions_summary() -> Dict[str, Any]:
        """
        All competitions with registration counts and status.
        """
        comps = await competition_collection.find().sort("created_at", -1).to_list(length=200)
        summaries = []

        for c in comps:
            cid = str(c["_id"])
            confirmed = await registration_collection.count_documents({
                "activity_id": cid,
                "activity_type": "competition",
                "status": "CONFIRMED"
            })
            pending = await registration_collection.count_documents({
                "activity_id": cid,
                "activity_type": "competition",
                "status": "PENDING_PAYMENT"
            })
            total = confirmed + pending

            # Revenue
            rev_pipeline = [
                {"$match": {"activity_id": cid, "activity_type": "competition", "status": "CONFIRMED", "payable_amount": {"$gt": 0}}},
                {"$group": {"_id": None, "total": {"$sum": "$payable_amount"}}}
            ]
            rev_result = await registration_collection.aggregate(rev_pipeline).to_list(length=1)
            revenue = float(rev_result[0]["total"]) if rev_result else 0.0

            summaries.append({
                "competition_id": cid,
                "title": c.get("title", ""),
                "status": c.get("result_status", ""),
                "voting_status": c.get("voting_status", ""),
                "judging_status": c.get("judging_status", ""),
                "total_registrations": total,
                "confirmed": confirmed,
                "pending_payment": pending,
                "registered_count_field": c.get("registered_count", 0),
                "revenue": revenue,
                "created_at": c.get("created_at").isoformat() if c.get("created_at") else None,
            })

        return {
            "total_competitions": len(summaries),
            "competitions": summaries,
        }

    # ──────────────────────────────────────────────────────────
    # 4. VIP BUYERS
    # ──────────────────────────────────────────────────────────

    @staticmethod
    async def get_vip_buyers(
        event_id: Optional[str] = None,
        status_filter: Optional[str] = None,
        skip: int = 0,
        limit: int = 50
    ) -> Dict[str, Any]:
        """
        Returns VIP pass buyers — optionally filtered by event.
        """
        query: Dict[str, Any] = {}
        if event_id:
            query["event_id"] = event_id
        if status_filter:
            query["booking_status"] = status_filter

        total = await vip_booking_collection.count_documents(query)
        bookings = await vip_booking_collection.find(query).sort("booked_at", -1).skip(skip).limit(limit).to_list(length=limit)

        # Bulk enrich
        emails = [b.get("user_email", "") for b in bookings]
        user_lookup = await _enrich_users_bulk(emails)

        # Config lookup
        config_ids = list(set(b.get("configuration_id", "") for b in bookings if b.get("configuration_id")))
        config_lookup = {}
        for cid in config_ids:
            if ObjectId.is_valid(cid):
                cfg = await vip_configuration_collection.find_one({"_id": ObjectId(cid)})
                if cfg:
                    config_lookup[cid] = {
                        "name": cfg.get("name", ""),
                        "category": cfg.get("category", ""),
                        "price": float(cfg.get("price", 0.0)),
                    }

        # Event lookup
        event_ids = list(set(b.get("event_id", "") for b in bookings if b.get("event_id")))
        event_lookup = {}
        for eid in event_ids:
            if ObjectId.is_valid(eid):
                ev = await event_collection.find_one({"_id": ObjectId(eid)})
                if ev:
                    event_lookup[eid] = ev.get("title", "")

        buyers = []
        for b in bookings:
            email = b.get("user_email", "")
            user_info = user_lookup.get(email, {"email": email})
            cid = b.get("configuration_id", "")
            eid = b.get("event_id", "")

            buyers.append({
                "booking_id": str(b["_id"]),
                "booking_reference": b.get("booking_reference", ""),
                "user": user_info,
                "event_id": eid,
                "event_title": event_lookup.get(eid, ""),
                "vip_config": config_lookup.get(cid, {}),
                "quantity": b.get("quantity", 1),
                "total_amount": float(b.get("total_amount", 0.0)),
                "currency": b.get("currency", "INR"),
                "booking_status": b.get("booking_status", ""),
                "payment_status": b.get("payment_status", ""),
                "booked_at": b.get("booked_at").isoformat() if b.get("booked_at") else None,
            })

        return {
            "event_id": event_id,
            "total_buyers": total,
            "skip": skip,
            "limit": limit,
            "buyers": buyers,
        }

    # ──────────────────────────────────────────────────────────
    # 5. MERCHANDISE BUYERS
    # ──────────────────────────────────────────────────────────

    @staticmethod
    async def get_merchandise_buyers(
        status_filter: Optional[str] = None,
        skip: int = 0,
        limit: int = 50
    ) -> Dict[str, Any]:
        """
        Returns all merchandise order buyers with user details.
        """
        query: Dict[str, Any] = {}
        if status_filter:
            query["status"] = status_filter

        total = await merchandise_order_collection.count_documents(query)
        orders = await merchandise_order_collection.find(query).sort("created_at", -1).skip(skip).limit(limit).to_list(length=limit)

        emails = [o.get("user_email", "") for o in orders]
        user_lookup = await _enrich_users_bulk(emails)

        buyers = []
        for o in orders:
            email = o.get("user_email", "")
            user_info = user_lookup.get(email, {"email": email})

            buyers.append({
                "order_id": str(o["_id"]),
                "order_number": o.get("order_number", ""),
                "user": user_info,
                "items": _serialize({"items": o.get("items", [])}).get("items", []),
                "total_amount": float(o.get("total_amount", 0.0)),
                "currency": o.get("currency", "INR"),
                "status": o.get("status", ""),
                "payment_status": o.get("payment_status", ""),
                "shipping_address": _serialize(o.get("shipping_address", {})) if o.get("shipping_address") else None,
                "ordered_at": o.get("created_at").isoformat() if o.get("created_at") else None,
            })

        return {
            "total_buyers": total,
            "skip": skip,
            "limit": limit,
            "buyers": buyers,
        }

    @staticmethod
    async def get_merchandise_product_sales() -> Dict[str, Any]:
        """
        Per-product sales breakdown with revenue and quantity sold.
        """
        products = await merchandise_product_collection.find().to_list(length=200)
        summaries = []
        total_revenue = 0.0
        total_qty_sold = 0

        for p in products:
            pid = str(p["_id"])

            # Aggregate orders for this product
            pipeline = [
                {"$match": {"payment_status": "PAID"}},
                {"$unwind": "$items"},
                {"$match": {"items.product_id": pid}},
                {"$group": {
                    "_id": None,
                    "total_qty": {"$sum": "$items.quantity"},
                    "total_revenue": {"$sum": {"$multiply": ["$items.quantity", "$items.unit_price"]}},
                    "order_count": {"$sum": 1}
                }}
            ]
            result = await merchandise_order_collection.aggregate(pipeline).to_list(length=1)

            qty_sold = result[0]["total_qty"] if result else 0
            revenue = float(result[0]["total_revenue"]) if result else 0.0
            order_count = result[0]["order_count"] if result else 0

            summaries.append({
                "product_id": pid,
                "name": p.get("name", ""),
                "category": p.get("category", ""),
                "price": float(p.get("price", 0.0)),
                "status": p.get("status", ""),
                "stock": p.get("stock", 0),
                "quantity_sold": qty_sold,
                "order_count": order_count,
                "revenue": revenue,
            })

            total_revenue += revenue
            total_qty_sold += qty_sold

        return {
            "total_products": len(summaries),
            "total_quantity_sold": total_qty_sold,
            "total_revenue": total_revenue,
            "currency": "INR",
            "products": summaries,
        }

    # ──────────────────────────────────────────────────────────
    # 6. REGISTRATION ANALYTICS
    # ──────────────────────────────────────────────────────────

    @staticmethod
    async def get_registrations_summary() -> Dict[str, Any]:
        """
        All registrations grouped by activity type.
        """
        pipeline = [
            {"$group": {
                "_id": {"activity_type": "$activity_type", "status": "$status"},
                "count": {"$sum": 1},
                "revenue": {"$sum": "$payable_amount"}
            }}
        ]
        results = await registration_collection.aggregate(pipeline).to_list(length=100)

        # Organize by type
        by_type: Dict[str, Dict[str, Any]] = {}
        for r in results:
            a_type = r["_id"]["activity_type"]
            a_status = r["_id"]["status"]
            if a_type not in by_type:
                by_type[a_type] = {"total": 0, "confirmed": 0, "pending_payment": 0, "revenue": 0.0}
            by_type[a_type]["total"] += r["count"]
            if a_status == "CONFIRMED":
                by_type[a_type]["confirmed"] += r["count"]
                by_type[a_type]["revenue"] += float(r.get("revenue", 0.0))
            elif a_status == "PENDING_PAYMENT":
                by_type[a_type]["pending_payment"] += r["count"]

        total = sum(v["total"] for v in by_type.values())
        total_revenue = sum(v["revenue"] for v in by_type.values())

        return {
            "total_registrations": total,
            "total_revenue": total_revenue,
            "currency": "INR",
            "by_activity_type": by_type,
        }

    @staticmethod
    async def get_registrants_by_type(
        activity_type: str,
        status_filter: Optional[str] = None,
        skip: int = 0,
        limit: int = 50
    ) -> Dict[str, Any]:
        """
        All registrants filtered by activity type (event/competition/course/workshop).
        """
        query: Dict[str, Any] = {"activity_type": activity_type}
        if status_filter:
            query["status"] = status_filter

        total = await registration_collection.count_documents(query)
        regs = await registration_collection.find(query).sort("created_at", -1).skip(skip).limit(limit).to_list(length=limit)

        emails = [r.get("email", "") for r in regs]
        user_lookup = await _enrich_users_bulk(emails)

        # Activity ID -> title lookup
        activity_ids = list(set(r.get("activity_id", "") for r in regs if r.get("activity_id")))
        activity_lookup: Dict[str, str] = {}
        for aid in activity_ids:
            if ObjectId.is_valid(aid):
                oid = ObjectId(aid)
                if activity_type == "competition":
                    a = await competition_collection.find_one({"_id": oid})
                elif activity_type in ("course", "workshop"):
                    a = await course_collection.find_one({"_id": oid})
                else:
                    a = await event_collection.find_one({"_id": oid})
                if a:
                    activity_lookup[aid] = a.get("title", a.get("name", ""))

        registrants = []
        for r in regs:
            email = r.get("email", "")
            user_info = user_lookup.get(email, {"email": email})
            aid = r.get("activity_id", "")
            registrants.append({
                "registration_id": str(r["_id"]),
                "user": user_info,
                "participant_name": r.get("participant_name", ""),
                "activity_id": aid,
                "activity_title": activity_lookup.get(aid, ""),
                "status": r.get("status", ""),
                "payable_amount": float(r.get("payable_amount", 0.0)),
                "coins_applied": r.get("coins_applied", 0),
                "order_reference": r.get("order_reference", ""),
                "registered_at": r.get("created_at").isoformat() if r.get("created_at") else None,
            })

        return {
            "activity_type": activity_type,
            "total_registrants": total,
            "skip": skip,
            "limit": limit,
            "registrants": registrants,
        }

    # ──────────────────────────────────────────────────────────
    # 7. REVENUE BREAKDOWN
    # ──────────────────────────────────────────────────────────

    @staticmethod
    async def get_revenue_breakdown() -> Dict[str, Any]:
        """
        Comprehensive revenue across all modules with per-event drill-down.
        """
        # Per-event ticket revenue
        event_rev_pipeline = [
            {"$match": {"booking_status": "CONFIRMED"}},
            {"$group": {
                "_id": "$event_id",
                "total_revenue": {"$sum": "$total_amount"},
                "total_bookings": {"$sum": 1},
                "total_tickets": {"$sum": "$quantity"},
            }}
        ]
        event_revenues = await booking_collection.aggregate(event_rev_pipeline).to_list(length=200)

        # Enrich event titles
        for er in event_revenues:
            eid = er["_id"]
            if ObjectId.is_valid(eid):
                ev = await event_collection.find_one({"_id": ObjectId(eid)})
                er["event_title"] = ev.get("title", "") if ev else ""
            else:
                er["event_title"] = ""
            er["event_id"] = eid
            del er["_id"]
            er["total_revenue"] = float(er["total_revenue"])

        # VIP revenue per event
        vip_rev_pipeline = [
            {"$match": {"booking_status": "CONFIRMED"}},
            {"$group": {
                "_id": "$event_id",
                "total_revenue": {"$sum": "$total_amount"},
                "total_bookings": {"$sum": 1},
            }}
        ]
        vip_revenues = await vip_booking_collection.aggregate(vip_rev_pipeline).to_list(length=200)
        for vr in vip_revenues:
            eid = vr["_id"]
            if ObjectId.is_valid(eid):
                ev = await event_collection.find_one({"_id": ObjectId(eid)})
                vr["event_title"] = ev.get("title", "") if ev else ""
            else:
                vr["event_title"] = ""
            vr["event_id"] = eid
            del vr["_id"]
            vr["total_revenue"] = float(vr["total_revenue"])

        # Merchandise total
        merch_pipeline = [
            {"$match": {"payment_status": "PAID"}},
            {"$group": {"_id": None, "total": {"$sum": "$total_amount"}, "count": {"$sum": 1}}}
        ]
        merch_rev = await merchandise_order_collection.aggregate(merch_pipeline).to_list(length=1)

        # Registration total
        reg_pipeline = [
            {"$match": {"status": "CONFIRMED", "payable_amount": {"$gt": 0}}},
            {"$group": {"_id": None, "total": {"$sum": "$payable_amount"}, "count": {"$sum": 1}}}
        ]
        reg_rev = await registration_collection.aggregate(reg_pipeline).to_list(length=1)

        ticket_total = sum(er.get("total_revenue", 0) for er in event_revenues)
        vip_total = sum(vr.get("total_revenue", 0) for vr in vip_revenues)
        merch_total = float(merch_rev[0]["total"]) if merch_rev else 0.0
        reg_total = float(reg_rev[0]["total"]) if reg_rev else 0.0

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "currency": "INR",
            "grand_total": ticket_total + vip_total + merch_total + reg_total,
            "ticket_revenue": {
                "total": ticket_total,
                "per_event": event_revenues,
            },
            "vip_revenue": {
                "total": vip_total,
                "per_event": vip_revenues,
            },
            "merchandise_revenue": {
                "total": merch_total,
                "total_orders": merch_rev[0]["count"] if merch_rev else 0,
            },
            "registration_revenue": {
                "total": reg_total,
                "total_paid_registrations": reg_rev[0]["count"] if reg_rev else 0,
            },
        }
