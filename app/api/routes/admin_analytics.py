"""
Admin Analytics API Routes
============================
Production-level endpoints for Admin Analytics Dashboard.
Provides comprehensive buyer insights across tickets, competitions,
VIP passes, merchandise, and unified revenue metrics.

All endpoints require super-admin authentication.
"""

from fastapi import APIRouter, Depends, Query
from typing import Optional

from app.core.admin_auth import get_current_admin
from app.services.admin_analytics_service import AdminAnalyticsService

router = APIRouter()


# ══════════════════════════════════════════════════════════════
# 1. UNIFIED DASHBOARD
# ══════════════════════════════════════════════════════════════

@router.get("/dashboard", summary="Admin Unified Dashboard", response_model=dict)
async def admin_analytics_dashboard(
    admin: dict = Depends(get_current_admin)
):
    """
    **Unified Admin Dashboard Overview**

    Returns high-level metrics across all modules:
    - Total ticket bookings, confirmed, pending, cancelled with revenue
    - Total registrations (events, competitions, courses) with revenue
    - Total VIP pass bookings with revenue
    - Total merchandise orders with revenue
    - Overall grand total revenue and unique buyer count
    """
    return await AdminAnalyticsService.get_unified_dashboard()


# ══════════════════════════════════════════════════════════════
# 2. TICKET BUYERS
# ══════════════════════════════════════════════════════════════

@router.get("/events/{event_id}/ticket-buyers", summary="Event Ticket Buyers", response_model=dict)
async def get_event_ticket_buyers(
    event_id: str,
    status: Optional[str] = Query(None, description="Filter by booking status (CONFIRMED, PENDING, CANCELLED)"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    admin: dict = Depends(get_current_admin)
):
    """
    **All Ticket Buyers for a Specific Event**

    Returns each buyer with:
    - Full user profile (name, email, phone, photo)
    - Ticket type and pricing
    - Booking reference, quantity, total amount
    - Booking and payment status
    - Timestamp
    """
    return await AdminAnalyticsService.get_event_ticket_buyers(
        event_id=event_id,
        status_filter=status,
        skip=skip,
        limit=limit
    )


@router.get("/events/{event_id}/ticket-summary", summary="Event Ticket Summary", response_model=dict)
async def get_event_ticket_summary(
    event_id: str,
    admin: dict = Depends(get_current_admin)
):
    """
    **Per-Ticket-Type Sales Breakdown for an Event**

    Returns for each ticket type:
    - Total capacity and available count
    - Sold count, confirmed bookings, pending bookings
    - Revenue generated
    """
    return await AdminAnalyticsService.get_event_ticket_summary(event_id)


# ══════════════════════════════════════════════════════════════
# 3. COMPETITION REGISTRANTS
# ══════════════════════════════════════════════════════════════

@router.get("/competitions/{comp_id}/registrants", summary="Competition Registrants", response_model=dict)
async def get_competition_registrants(
    comp_id: str,
    status: Optional[str] = Query(None, description="Filter by status (CONFIRMED, PENDING_PAYMENT)"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    admin: dict = Depends(get_current_admin)
):
    """
    **All Registrants for a Specific Competition**

    Returns each registrant with:
    - Full user profile (name, email, phone, photo)
    - Registration status, payment info
    - Coins applied, discount amount
    - Timestamp
    """
    return await AdminAnalyticsService.get_competition_registrants(
        comp_id=comp_id,
        status_filter=status,
        skip=skip,
        limit=limit
    )


@router.get("/competitions/summary", summary="All Competitions Summary", response_model=dict)
async def get_competitions_summary(
    admin: dict = Depends(get_current_admin)
):
    """
    **Overview of All Competitions**

    Returns for each competition:
    - Total registrations, confirmed vs pending
    - Revenue generated
    - Voting/judging/result status
    """
    return await AdminAnalyticsService.get_competitions_summary()


# ══════════════════════════════════════════════════════════════
# 4. VIP PASS BUYERS
# ══════════════════════════════════════════════════════════════

@router.get("/vip/buyers", summary="All VIP Buyers", response_model=dict)
async def get_all_vip_buyers(
    event_id: Optional[str] = Query(None, description="Filter by event ID"),
    status: Optional[str] = Query(None, description="Filter by booking status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    admin: dict = Depends(get_current_admin)
):
    """
    **All VIP Pass Buyers (optionally filtered by event)**

    Returns each buyer with:
    - Full user profile (name, email, phone, photo)
    - VIP configuration details (name, category, price)
    - Event info, booking status, amount paid
    - Timestamp
    """
    return await AdminAnalyticsService.get_vip_buyers(
        event_id=event_id,
        status_filter=status,
        skip=skip,
        limit=limit
    )


@router.get("/events/{event_id}/vip-buyers", summary="Event VIP Buyers", response_model=dict)
async def get_event_vip_buyers(
    event_id: str,
    status: Optional[str] = Query(None, description="Filter by booking status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    admin: dict = Depends(get_current_admin)
):
    """
    **VIP Pass Buyers for a Specific Event**

    Same as `/vip/buyers` but pre-filtered to a specific event.
    """
    return await AdminAnalyticsService.get_vip_buyers(
        event_id=event_id,
        status_filter=status,
        skip=skip,
        limit=limit
    )


# ══════════════════════════════════════════════════════════════
# 5. MERCHANDISE BUYERS
# ══════════════════════════════════════════════════════════════

@router.get("/merchandise/buyers", summary="All Merchandise Buyers", response_model=dict)
async def get_merchandise_buyers(
    status: Optional[str] = Query(None, description="Filter by order status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    admin: dict = Depends(get_current_admin)
):
    """
    **All Merchandise Order Buyers**

    Returns each buyer with:
    - Full user profile (name, email, phone, photo)
    - Order items, total amount, currency
    - Order and payment status
    - Shipping address
    - Timestamp
    """
    return await AdminAnalyticsService.get_merchandise_buyers(
        status_filter=status,
        skip=skip,
        limit=limit
    )


@router.get("/merchandise/product-sales", summary="Merchandise Product Sales", response_model=dict)
async def get_merchandise_product_sales(
    admin: dict = Depends(get_current_admin)
):
    """
    **Per-Product Sales Breakdown**

    Returns for each merchandise product:
    - Product details (name, category, price, stock)
    - Quantity sold, order count, revenue
    - Total across all products
    """
    return await AdminAnalyticsService.get_merchandise_product_sales()


# ══════════════════════════════════════════════════════════════
# 6. REGISTRATION ANALYTICS
# ══════════════════════════════════════════════════════════════

@router.get("/registrations/summary", summary="Registration Summary", response_model=dict)
async def get_registrations_summary(
    admin: dict = Depends(get_current_admin)
):
    """
    **All Activity Registrations Grouped by Type**

    Returns registration counts and revenue breakdown by:
    - event, competition, course, workshop
    """
    return await AdminAnalyticsService.get_registrations_summary()


@router.get("/registrations/{activity_type}", summary="Registrants by Activity Type", response_model=dict)
async def get_registrants_by_type(
    activity_type: str,
    status: Optional[str] = Query(None, description="Filter by status (CONFIRMED, PENDING_PAYMENT)"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    admin: dict = Depends(get_current_admin)
):
    """
    **All Registrants Filtered by Activity Type**

    Valid types: `event`, `competition`, `course`, `workshop`

    Returns each registrant with:
    - Full user profile, activity title, status
    - Payment info, coins applied
    - Timestamp
    """
    return await AdminAnalyticsService.get_registrants_by_type(
        activity_type=activity_type,
        status_filter=status,
        skip=skip,
        limit=limit
    )


# ══════════════════════════════════════════════════════════════
# 7. REVENUE
# ══════════════════════════════════════════════════════════════

@router.get("/revenue", summary="Revenue Breakdown", response_model=dict)
async def get_revenue_breakdown(
    admin: dict = Depends(get_current_admin)
):
    """
    **Comprehensive Revenue Breakdown**

    Returns:
    - Grand total revenue across all modules
    - Ticket revenue per event
    - VIP revenue per event
    - Merchandise total revenue and order count
    - Registration total revenue and count
    """
    return await AdminAnalyticsService.get_revenue_breakdown()
