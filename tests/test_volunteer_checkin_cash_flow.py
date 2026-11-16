"""
test_volunteer_checkin_cash_flow.py
===================================
Production End-to-End Verification of Volunteer Check-in & Cash Collection:
1. Setup Admin, Volunteer, and Event Attendee.
2. Verify Cash Ticket Check-in:
   - Volunteer scans ticket with cash pending.
   - Cash is marked COLLECTED, cash_collect_done=True, cash_collected_display='Cash Collect Done'.
   - checked_in_by records volunteer email, name, role, and timestamp.
3. Verify Admin Panel Endpoints:
   - GET /api/admin/bookings: Returns who checked in the ticket and cash collection status.
   - GET /api/admin/bookings/{id}: Returns full volunteer and cash collection details.
   - GET /api/admin/events/{event_id}/attendance: Returns volunteer info and cash status in both attendee list and present list.
   - GET /api/admin/volunteers/: Returns aggregated total_checkins and total_cash_collected per volunteer.
   - GET /api/volunteer/tickets/checkin-history: Returns check-in history for the volunteer.
4. Verify Already Paid Ticket:
   - Volunteer scans online paid ticket.
   - Ticket checked in successfully, checked_in_by recorded, cash not required.
5. Verify Duplicate Check-in Prevention:
   - Attempting duplicate scan returns 400 with already checked in error.
6. Full Teardown & Cleanup.
"""

import pytest
from datetime import datetime, timezone, timedelta
from httpx import AsyncClient, ASGITransport
from bson import ObjectId

from app.main import app
from app.core.database import (
    user_collection,
    admin_collection,
    event_collection,
    booking_collection,
    issued_ticket_collection,
    audit_log_collection,
)
from app.core.security import create_access_token, get_password_hash


@pytest.mark.asyncio
async def test_volunteer_checkin_and_cash_collection_flow():
    ts = int(datetime.now().timestamp())
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    admin_email = f"admin_cash_{ts}@upgradetalentsindia.in"
    vol_email = f"vol_scanner_{ts}@upgradetalentsindia.in"
    attendee1_email = f"attendee_cash_{ts}@upgradetalentsindia.in"
    attendee2_email = f"attendee_paid_{ts}@upgradetalentsindia.in"

    event_id = None
    booking1_id = None
    booking2_id = None

    try:
        # 1. Setup Admin & Volunteer in DB
        await admin_collection.insert_one({
            "email": admin_email,
            "full_name": "Super Admin",
            "hashed_password": get_password_hash("AdminPass123!"),
            "role": "SUPER_ADMIN",
            "is_active": True,
            "created_at": now_iso
        })
        admin_token = create_access_token({"sub": admin_email, "type": "admin"})
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        await user_collection.insert_one({
            "email": vol_email,
            "full_name": "Volunteer Ramesh",
            "role": "VOLUNTEER",
            "phone": "+919876543210",
            "registration_complete": True,
            "created_at": now_iso
        })
        vol_token = create_access_token({"sub": vol_email, "role": "VOLUNTEER", "type": "user"})
        vol_headers = {"Authorization": f"Bearer {vol_token}"}

        # 2. Setup Event
        event_doc = {
            "title": f"Annual Tech Gala {ts}",
            "slug": f"annual-tech-gala-{ts}",
            "description": "Tech conference & grand event",
            "event_type": "OFFLINE",
            "category": "CONCERT",
            "start_time": (now + timedelta(days=2)).isoformat(),
            "end_time": (now + timedelta(days=3)).isoformat(),
            "venue": {
                "name": "Grand Ballroom",
                "address": "Park Street, Kolkata",
                "city": "Kolkata",
                "state": "WB",
                "country": "India"
            },
            "status": "PUBLISHED",
            "is_active": True,
            "volunteers": [{"email": vol_email, "name": "Volunteer Ramesh", "role": "Entrance Gate Scanner"}],
            "created_at": now_iso,
            "updated_at": now_iso
        }
        ev_res = await event_collection.insert_one(event_doc)
        event_id = str(ev_res.inserted_id)

        # 3. Create Cash Pending Booking & Ticket for Attendee 1
        booking1_ref = f"BK-CASH-{ts}"
        ticket1_ref = f"TCK-CASH-{ts}"
        qr1_token = f"QR_CASH_TOKEN_{ts}"

        booking1_doc = {
            "booking_reference": booking1_ref,
            "event_id": event_id,
            "user_email": attendee1_email,
            "attendee_name": "Priya Sharma",
            "user_phone": "+919123456780",
            "total_amount": 500.0,
            "currency": "INR",
            "payment_method": "cash",
            "payment_status": "PENDING",
            "cash_status": "PENDING",
            "cash_collected": False,
            "cash_collect_done": False,
            "is_present": False,
            "attendance_status": "ABSENT",
            "ticket_quantity": 1,
            "created_at": now_iso
        }
        b1_res = await booking_collection.insert_one(booking1_doc)
        booking1_id = str(b1_res.inserted_id)

        ticket1_doc = {
            "ticket_ref": ticket1_ref,
            "booking_ref": booking1_ref,
            "booking_id": booking1_id,
            "event_id": event_id,
            "user_email": attendee1_email,
            "attendee_name": "Priya Sharma",
            "tier_name": "Standard Pass",
            "price": 500.0,
            "qr_token": qr1_token,
            "status": "VALID",
            "payment_status": "PENDING",
            "cash_status": "PENDING",
            "cash_collected": False,
            "cash_collect_done": False,
            "is_checked_in": False,
            "created_at": now_iso
        }
        await issued_ticket_collection.insert_one(ticket1_doc)

        # 4. Create Paid Online Booking & Ticket for Attendee 2
        booking2_ref = f"BK-PAID-{ts}"
        ticket2_ref = f"TCK-PAID-{ts}"
        qr2_token = f"QR_PAID_TOKEN_{ts}"

        booking2_doc = {
            "booking_reference": booking2_ref,
            "event_id": event_id,
            "user_email": attendee2_email,
            "attendee_name": "Rohit Das",
            "user_phone": "+919123456781",
            "total_amount": 750.0,
            "currency": "INR",
            "payment_method": "razorpay",
            "payment_status": "PAID",
            "cash_status": "NOT_APPLICABLE",
            "cash_collected": True,
            "cash_collect_done": True,
            "is_present": False,
            "attendance_status": "ABSENT",
            "ticket_quantity": 1,
            "created_at": now_iso
        }
        b2_res = await booking_collection.insert_one(booking2_doc)
        booking2_id = str(b2_res.inserted_id)

        ticket2_doc = {
            "ticket_ref": ticket2_ref,
            "booking_ref": booking2_ref,
            "booking_id": booking2_id,
            "event_id": event_id,
            "user_email": attendee2_email,
            "attendee_name": "Rohit Das",
            "tier_name": "VIP Pass",
            "price": 750.0,
            "qr_token": qr2_token,
            "status": "VALID",
            "payment_status": "PAID",
            "cash_status": "NOT_APPLICABLE",
            "cash_collected": True,
            "cash_collect_done": True,
            "is_checked_in": False,
            "created_at": now_iso
        }
        await issued_ticket_collection.insert_one(ticket2_doc)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:

            # ── Step 1: Volunteer Verifies Cash Ticket ──
            verify_res = await ac.post(
                "/api/volunteer/tickets/verify",
                json={"qr_token": qr1_token},
                headers=vol_headers
            )
            assert verify_res.status_code == 200, f"Verify failed: {verify_res.text}"
            v_data = verify_res.json()
            assert v_data["is_valid"] is True
            assert v_data["is_cash"] is True
            assert v_data["is_cash_pending"] is True
            assert v_data["is_checked_in"] is False

            # ── Step 2: Volunteer Checks In Cash Ticket with collect_cash=True ──
            checkin_res = await ac.post(
                "/api/volunteer/tickets/check-in",
                json={"qr_token": qr1_token, "collect_cash": True},
                headers=vol_headers
            )
            assert checkin_res.status_code == 200, f"Check-in failed: {checkin_res.text}"
            ci_data = checkin_res.json()
            assert ci_data["success"] is True
            assert ci_data["checked_in_by"] == vol_email
            assert ci_data["checked_in_by_name"] == "Volunteer Ramesh"
            assert ci_data["checked_in_by_role"] == "VOLUNTEER"
            assert ci_data["cash_status"] == "COLLECTED"
            assert ci_data["cash_collect_done"] is True
            assert ci_data["cash_collected_display"] == "Cash Collect Done"
            assert ci_data["cash_collected_by"] == vol_email
            assert ci_data["payment_status"] == "PAID"

            # ── Step 3: Admin Checks Bookings List (/admin/bookings) ──
            admin_bks_res = await ac.get(
                f"/api/admin/bookings?event_id={event_id}",
                headers=admin_headers
            )
            assert admin_bks_res.status_code == 200, f"Admin bookings failed: {admin_bks_res.text}"
            bks_data = admin_bks_res.json()
            bks = bks_data.get("bookings", bks_data) if isinstance(bks_data, dict) else bks_data
            cash_bk = next((b for b in bks if b.get("booking_reference") == booking1_ref), None)
            assert cash_bk is not None, "Cash booking not found in admin bookings list"
            assert cash_bk["checked_in_by"] == vol_email
            assert cash_bk["checked_in_by_name"] == "Volunteer Ramesh"
            assert cash_bk["cash_collect_done"] is True
            assert cash_bk["cash_collected_display"] == "Cash Collect Done"
            assert cash_bk["payment_status"] == "PAID"
            assert cash_bk["tickets"][0]["is_checked_in"] is True
            assert cash_bk["tickets"][0]["checked_in_by"] == vol_email
            assert cash_bk["tickets"][0]["cash_collect_done"] is True

            # ── Step 4: Admin Checks Booking Detail (/admin/bookings/{id}) ──
            admin_bk_detail = await ac.get(
                f"/api/admin/bookings/{booking1_id}",
                headers=admin_headers
            )
            assert admin_bk_detail.status_code == 200, f"Admin booking detail failed: {admin_bk_detail.text}"
            detail_data = admin_bk_detail.json()
            assert detail_data["checked_in_by"] == vol_email
            assert detail_data["checked_in_by_name"] == "Volunteer Ramesh"
            assert detail_data["cash_collect_done"] is True
            assert detail_data["cash_collected_display"] == "Cash Collect Done"
            assert detail_data["payment_status"] == "PAID"

            # ── Step 5: Admin Checks Event Attendance Dashboard ──
            att_res = await ac.get(
                f"/api/admin/events/{event_id}/attendance",
                headers=admin_headers
            )
            assert att_res.status_code == 200, f"Attendance failed: {att_res.text}"
            att_data = att_res.json()
            assert att_data["present_count"] >= 1
            present_list = att_data.get("final_present_list", [])
            attendee1_present = next((a for a in present_list if a.get("user_email") == attendee1_email), None)
            assert attendee1_present is not None, "Attendee 1 should be in final_present_list"
            assert attendee1_present["checked_in_by"] == vol_email
            assert attendee1_present["checked_in_by_name"] == "Volunteer Ramesh"
            assert attendee1_present["cash_collect_done"] is True
            assert attendee1_present["cash_collected_display"] == "Cash Collect Done"

            # ── Step 6: Volunteer Checks Checkin History ──
            history_res = await ac.get(
                "/api/volunteer/tickets/checkin-history",
                headers=vol_headers
            )
            assert history_res.status_code == 200, f"History failed: {history_res.text}"
            history_list = history_res.json()
            history_entry = next((h for h in history_list if h.get("ticket_ref") == ticket1_ref), None)
            assert history_entry is not None, "History entry for ticket 1 not found"
            assert history_entry["checked_in_by"] == vol_email
            assert history_entry["cash_status"] == "COLLECTED"
            assert history_entry["cash_collect_done"] is True
            assert history_entry["cash_collected_display"] == "Cash Collect Done"

            # ── Step 7: Check Volunteer Aggregated Stats (/admin/volunteers/) ──
            vol_stats_res = await ac.get(
                "/api/admin/volunteers/",
                headers=admin_headers
            )
            assert vol_stats_res.status_code == 200, f"Volunteer list failed: {vol_stats_res.text}"
            vols = vol_stats_res.json()
            cur_vol = next((v for v in vols if v.get("email") == vol_email), None)
            assert cur_vol is not None
            assert cur_vol["total_checkins"] >= 1
            assert cur_vol["cash_collected_count"] >= 1
            assert cur_vol["total_cash_collected"] >= 500.0

            # ── Step 8: Volunteer Checks In Paid Online Ticket (Attendee 2) ──
            paid_ci_res = await ac.post(
                "/api/volunteer/tickets/check-in",
                json={"qr_token": qr2_token},
                headers=vol_headers
            )
            assert paid_ci_res.status_code == 200, f"Paid check-in failed: {paid_ci_res.text}"
            paid_ci_data = paid_ci_res.json()
            assert paid_ci_data["success"] is True
            assert paid_ci_data["checked_in_by"] == vol_email
            assert paid_ci_data["checked_in_by_name"] == "Volunteer Ramesh"

            # ── Step 9: Duplicate Check-in Prevention ──
            dup_res = await ac.post(
                "/api/volunteer/tickets/check-in",
                json={"qr_token": qr1_token},
                headers=vol_headers
            )
            assert dup_res.status_code == 400, "Duplicate check-in should be rejected with 400"
            assert "already" in dup_res.json().get("detail", "").lower()

    finally:
        # Cleanup
        await admin_collection.delete_one({"email": admin_email})
        await user_collection.delete_one({"email": vol_email})
        if event_id:
            await event_collection.delete_one({"_id": ObjectId(event_id)})
        if booking1_id:
            await booking_collection.delete_one({"_id": ObjectId(booking1_id)})
        if booking2_id:
            await booking_collection.delete_one({"_id": ObjectId(booking2_id)})
        await issued_ticket_collection.delete_many({
            "ticket_ref": {"$in": [f"TCK-CASH-{ts}", f"TCK-PAID-{ts}"]}
        })
