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
)
from app.core.security import create_access_token, get_password_hash

@pytest.mark.asyncio
async def test_ticket_already_used_stamp_and_download_block():
    ts = int(datetime.now().timestamp())
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    vol_email = f"vol_gate_{ts}@upgradetalentsindia.in"
    user_email = f"user_ticket_{ts}@upgradetalentsindia.in"

    event_id = None
    booking_id = None

    try:
        # 1. Setup User in DB with completed profile
        await user_collection.insert_one({
            "email": user_email,
            "full_name": "Arun Roy",
            "name": "Arun Roy",
            "role": "USER",
            "phone_number": "+919830123456",
            "date_of_birth": "2000-01-01",
            "address": "123 Park Street, Kolkata",
            "parent_phone_number": "+919830123457",
            "registration_complete": True,
            "created_at": now_iso
        })
        user_token = create_access_token({"sub": user_email, "role": "USER", "type": "user"})
        user_headers = {"Authorization": f"Bearer {user_token}"}

        # 2. Setup Volunteer in DB
        await user_collection.insert_one({
            "email": vol_email,
            "full_name": "Volunteer Subhash",
            "role": "VOLUNTEER",
            "phone": "+919830987654",
            "registration_complete": True,
            "created_at": now_iso
        })
        vol_token = create_access_token({"sub": vol_email, "role": "VOLUNTEER", "type": "user"})
        vol_headers = {"Authorization": f"Bearer {vol_token}"}

        # 3. Setup Event
        event_doc = {
            "title": f"Grand Finale Concert {ts}",
            "slug": f"grand-finale-{ts}",
            "event_type": "OFFLINE",
            "category": "CONCERT",
            "start_time": (now + timedelta(days=1)).isoformat(),
            "end_time": (now + timedelta(days=2)).isoformat(),
            "venue": "Science City Auditorium, Kolkata",
            "status": "PUBLISHED",
            "is_active": True,
            "volunteers": [{"email": vol_email, "name": "Volunteer Subhash", "role": "Main Gate Scanner"}],
            "created_at": now_iso,
        }
        ev_res = await event_collection.insert_one(event_doc)
        event_id = str(ev_res.inserted_id)

        # 4. Create Online Paid Booking & Issued Ticket
        booking_ref = f"BK-ONLINE-{ts}"
        ticket_ref = f"TCK-ONLINE-{ts}"
        qr_token = f"QR_ONLINE_{ts}"

        booking_doc = {
            "booking_reference": booking_ref,
            "event_id": event_id,
            "user_email": user_email,
            "user_name": "Arun Roy",
            "total_amount": 1200.0,
            "payment_method": "ONLINE",
            "payment_status": "PAID",
            "booking_status": "CONFIRMED",
            "quantity": 1,
            "is_checked_in": False,
            "booked_at": now_iso,
        }
        b_res = await booking_collection.insert_one(booking_doc)
        booking_id = str(b_res.inserted_id)

        ticket_doc = {
            "ticket_ref": ticket_ref,
            "booking_id": booking_id,
            "booking_ref": booking_ref,
            "event_id": event_id,
            "event_title": f"Grand Finale Concert {ts}",
            "holder_email": user_email,
            "holder_name": "Arun Roy",
            "quantity": 1,
            "admitted_count": 0,
            "price": 1200.0,
            "qr_token": qr_token,
            "status": "ACTIVE",
            "payment_status": "PAID",
            "payment_method": "ONLINE",
            "is_checked_in": False,
            "issued_at": now_iso,
        }
        await issued_ticket_collection.insert_one(ticket_doc)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # ── Step 1: Before scanning, user sees ticket as ACTIVE and can_download is True ──
            my_tix_res = await ac.get("/api/bookings/my-tickets", headers=user_headers)
            assert my_tix_res.status_code == 200
            tix = my_tix_res.json()
            assert len(tix) >= 1
            user_ticket = next((t for t in tix if t.get("ticket_ref") == ticket_ref), None)
            assert user_ticket is not None
            assert user_ticket["is_checked_in"] is False
            assert user_ticket["status"] == "ACTIVE"
            assert user_ticket.get("can_download") is True

            # ── Step 2: Volunteer scans and verifies ticket at gate ──
            verify_res = await ac.post(
                "/api/volunteer/tickets/verify",
                json={"qr_token": qr_token},
                headers=vol_headers
            )
            assert verify_res.status_code == 200
            v_data = verify_res.json()
            assert v_data["is_checked_in"] is False

            # ── Step 3: Volunteer admits attendee / checks in ticket ──
            ci_res = await ac.post(
                "/api/volunteer/tickets/check-in",
                json={"qr_token": qr_token},
                headers=vol_headers
            )
            assert ci_res.status_code == 200
            ci_data = ci_res.json()
            assert ci_data["success"] is True
            assert ci_data["is_fully_used"] is True

            # ── Step 4: User's profile now immediately shows ALREADY_USED and can_download is False ──
            post_ci_res = await ac.get("/api/bookings/my-tickets", headers=user_headers)
            assert post_ci_res.status_code == 200
            post_tix = post_ci_res.json()
            updated_ticket = next((t for t in post_tix if t.get("ticket_ref") == ticket_ref), None)
            assert updated_ticket is not None
            assert updated_ticket["is_checked_in"] is True
            assert updated_ticket["status"] == "USED"
            assert updated_ticket.get("status_display") == "ALREADY_USED"
            assert updated_ticket.get("can_download") is False

            # Also verify via /api/bookings/my
            my_bk_res = await ac.get("/api/bookings/my", headers=user_headers)
            assert my_bk_res.status_code == 200
            b_list = my_bk_res.json()
            my_b = next((b for b in b_list if b.get("booking_reference") == booking_ref), None)
            assert my_b is not None
            assert my_b["is_checked_in"] is True
            assert my_b["ticket_status"] == "USED"
            assert my_b.get("status_display") == "ALREADY_USED"
            assert my_b.get("can_download") is False

            # ── Step 5: User cannot re-email or download pass after check-in ──
            email_res = await ac.post(
                f"/api/bookings/tickets/{ticket_ref}/send-email",
                headers=user_headers
            )
            assert email_res.status_code == 400
            assert "already been used" in email_res.json().get("detail", "").lower()

            # ── Step 6: Duplicate scan at gate is rejected ──
            dup_res = await ac.post(
                "/api/volunteer/tickets/check-in",
                json={"qr_token": qr_token},
                headers=vol_headers
            )
            assert dup_res.status_code == 400
            assert "already" in dup_res.json().get("detail", "").lower()

            # ── Step 7: CASH ON SPOT BOOKING FLOW ──
            # User books via cash on spot, arrives at event, volunteer collects cash and scans entry
            cash_booking_ref = f"BK-CASH-{ts}"
            cash_ticket_ref = f"TCK-CASH-{ts}"
            cash_qr_token = f"QR_CASH_{ts}"

            cash_b_res = await booking_collection.insert_one({
                "booking_reference": cash_booking_ref,
                "event_id": event_id,
                "user_email": user_email,
                "user_name": "Arun Roy",
                "total_amount": 500.0,
                "payment_method": "CASH",
                "payment_status": "PENDING",
                "cash_payment": True,
                "cash_status": "PENDING",
                "booking_status": "CONFIRMED",
                "quantity": 1,
                "is_checked_in": False,
                "booked_at": now_iso,
            })
            cash_booking_id = str(cash_b_res.inserted_id)

            await issued_ticket_collection.insert_one({
                "ticket_ref": cash_ticket_ref,
                "booking_id": cash_booking_id,
                "booking_ref": cash_booking_ref,
                "event_id": event_id,
                "event_title": f"Grand Finale Concert {ts}",
                "holder_email": user_email,
                "holder_name": "Arun Roy",
                "quantity": 1,
                "admitted_count": 0,
                "price": 500.0,
                "qr_token": cash_qr_token,
                "status": "ACTIVE",
                "payment_status": "PENDING",
                "payment_method": "CASH",
                "cash_payment": True,
                "cash_status": "PENDING",
                "is_checked_in": False,
                "issued_at": now_iso,
            })

            # Check cash ticket before scanning: can_download is True, is_checked_in is False
            cash_pre_res = await ac.get("/api/bookings/my-tickets", headers=user_headers)
            assert cash_pre_res.status_code == 200
            cash_ticket = next((t for t in cash_pre_res.json() if t.get("ticket_ref") == cash_ticket_ref), None)
            assert cash_ticket is not None
            assert cash_ticket["is_checked_in"] is False
            assert cash_ticket.get("can_download") is True

            # Volunteer scans and collects cash
            cash_ci_res = await ac.post(
                "/api/volunteer/tickets/check-in",
                json={"qr_token": cash_qr_token, "collect_cash": True, "cash_amount": 500.0},
                headers=vol_headers
            )
            assert cash_ci_res.status_code == 200
            cash_ci_data = cash_ci_res.json()
            assert cash_ci_data["success"] is True
            assert cash_ci_data["is_fully_used"] is True
            assert cash_ci_data["cash_collect_done"] is True

            # In user profile, cash ticket is now ALREADY USED and cannot be downloaded
            cash_post_res = await ac.get("/api/bookings/my-tickets", headers=user_headers)
            assert cash_post_res.status_code == 200
            cash_used_ticket = next((t for t in cash_post_res.json() if t.get("ticket_ref") == cash_ticket_ref), None)
            assert cash_used_ticket is not None
            assert cash_used_ticket["is_checked_in"] is True
            assert cash_used_ticket["status"] == "USED"
            assert cash_used_ticket.get("status_display") == "ALREADY_USED"
            assert cash_used_ticket.get("can_download") is False
            assert cash_used_ticket.get("payment_status") == "PAID"

            # Check booking list for cash booking
            cash_my_bk = await ac.get("/api/bookings/my", headers=user_headers)
            assert cash_my_bk.status_code == 200
            my_cash_b = next((b for b in cash_my_bk.json() if b.get("booking_reference") == cash_booking_ref), None)
            assert my_cash_b is not None
            assert my_cash_b["is_checked_in"] is True
            assert my_cash_b["ticket_status"] == "USED"
            assert my_cash_b.get("status_display") == "ALREADY_USED"
            assert my_cash_b.get("can_download") is False

            # Attempting to re-send or download cash pass is blocked
            cash_email_res = await ac.post(
                f"/api/bookings/tickets/{cash_ticket_ref}/send-email",
                headers=user_headers
            )
            assert cash_email_res.status_code == 400
            assert "already been used" in cash_email_res.json().get("detail", "").lower()

            # Clean up cash booking
            await booking_collection.delete_one({"_id": ObjectId(cash_booking_id)})
            await issued_ticket_collection.delete_many({"ticket_ref": cash_ticket_ref})

    finally:
        # Cleanup
        await user_collection.delete_many({"email": {"$in": [user_email, vol_email]}})
        if event_id:
            await event_collection.delete_one({"_id": ObjectId(event_id)})
        if booking_id:
            await booking_collection.delete_one({"_id": ObjectId(booking_id)})
        await issued_ticket_collection.delete_many({"ticket_ref": ticket_ref})

