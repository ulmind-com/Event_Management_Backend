import pytest
from bson import ObjectId
from datetime import datetime, timezone
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.core.database import (
    user_collection, admin_collection, event_collection, ticket_collection,
    booking_collection, issued_ticket_collection, registration_collection
)
from app.core.security import create_access_token


@pytest.mark.asyncio
async def test_cash_event_participation_and_admin_approval():
    unique_suffix = ObjectId()
    user_email = f"cash_attendee_{unique_suffix}@example.com"
    admin_email = f"admin_cash_{unique_suffix}@example.com"

    # 1. Insert user
    await user_collection.insert_one({
        "email": user_email,
        "name": "Rohan Sen",
        "full_name": "Rohan Sen",
        "phone_number": "9830112233",
        "registration_complete": True,
        "role": "user"
    })

    # 2. Insert admin
    await admin_collection.insert_one({
        "email": admin_email,
        "name": "Super Admin",
        "role": "super_admin"
    })

    user_token = create_access_token({"sub": user_email, "role": "user"})
    admin_token = create_access_token({"sub": admin_email, "type": "admin", "role": "super_admin"})

    user_headers = {"Authorization": f"Bearer {user_token}"}
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # 3. Create published event and ticket
    now_utc = datetime.now(timezone.utc)
    ev_res = await event_collection.insert_one({
        "title": f"Grand Tech Fest {unique_suffix}",
        "date": "2026-11-20",
        "time": "10:00 AM",
        "venue": "Science City, Kolkata",
        "price": 499.0,
        "is_published": True,
        "registration_enabled": True,
        "ticket_enabled": True,
        "registered_count": 0,
        "event_status": "upcoming",
        "created_at": now_utc
    })
    event_id = str(ev_res.inserted_id)

    tk_res = await ticket_collection.insert_one({
        "event_id": event_id,
        "type": "Standard Pass",
        "price": 499.0,
        "total_quantity": 100,
        "available": 100,
        "created_at": now_utc
    })
    ticket_id = str(tk_res.inserted_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        try:
            # ── 4. User registers for event choosing CASH ──
            booking_payload = {
                "event_id": event_id,
                "ticket_id": ticket_id,
                "quantity": 2,
                "holder_names": ["Rohan Sen", "Priya Sen"],
                "payment_method": "CASH"
            }
            res = await ac.post("/api/bookings/", json=booking_payload, headers=user_headers)
            assert res.status_code == 200, res.text
            data = res.json()
            assert data["booking_status"] == "CONFIRMED"
            assert data["payment_status"] == "PENDING"
            assert data["payment_method"] == "CASH"
            assert data["total_amount"] == 998.0
            booking_id = data["booking_id"]
            booking_ref = data["booking_reference"]

            # Verify registered_count was incremented on the event
            updated_ev = await event_collection.find_one({"_id": ObjectId(event_id)})
            assert updated_ev["registered_count"] == 2

            # ── 5. Admin lists attendees with search bar by participant name ──
            search_res = await ac.get(
                f"/api/admin/events/{event_id}/bookings?search=Rohan",
                headers=admin_headers
            )
            assert search_res.status_code == 200, search_res.text
            search_data = search_res.json()
            assert search_data["total"] >= 1
            found = next((b for b in search_data["bookings"] if b["booking_id"] == booking_id), None)
            assert found is not None
            assert found["user_name"] == "Rohan Sen"
            assert found["payment_status"] == "PENDING"
            assert found["payment_status_display"] == "PENDING"
            assert found["payment_method"] == "CASH"
            assert found["can_approve_cash"] is True

            # Also test alias endpoint /api/admin/events/{event_id}/registrations
            alias_res = await ac.get(
                f"/api/admin/events/{event_id}/registrations?search=Rohan",
                headers=admin_headers
            )
            assert alias_res.status_code == 200
            assert len(alias_res.json()["bookings"]) >= 1

            # ── 6. Admin approves Cash Payment on event day ──
            approve_res = await ac.post(
                f"/api/admin/events/{event_id}/bookings/{booking_id}/approve-cash",
                json={"notes": "Cash received at Registration Desk 1"},
                headers=admin_headers
            )
            assert approve_res.status_code == 200, approve_res.text
            app_data = approve_res.json()
            assert app_data["payment_status"] == "PAID"
            assert app_data["payment_status_display"] == "SUCCESS"
            assert app_data["payment_method"] == "CASH"
            assert app_data["cash_approved"] is True
            assert app_data["approved_by"] == admin_email

            # ── 7. Verify booking document and tickets in DB ──
            db_booking = await booking_collection.find_one({"_id": ObjectId(booking_id)})
            assert db_booking["payment_status"] == "PAID"
            assert db_booking["payment_method"] == "CASH"
            assert db_booking["cash_approved"] is True
            assert db_booking["approved_by"] == admin_email
            assert db_booking["paid_at"] is not None

            # ── 8. Verify search after approval shows payment status SUCCESS ──
            search_after = await ac.get(
                f"/api/admin/events/{event_id}/bookings?search=Rohan",
                headers=admin_headers
            )
            assert search_after.status_code == 200
            found_after = next((b for b in search_after.json()["bookings"] if b["booking_id"] == booking_id), None)
            assert found_after["payment_status"] == "PAID"
            assert found_after["payment_status_display"] == "SUCCESS"
            assert found_after["payment_method"] == "CASH"
            assert found_after["can_approve_cash"] is False

            # ── 9. Direct activity registration with CASH option ──
            reg_payload = {
                "activity_id": event_id,
                "activity_type": "event",
                "payment_method": "CASH"
            }
            reg_res = await ac.post("/api/registrations/", json=reg_payload, headers=user_headers)
            assert reg_res.status_code == 200
            r_data = reg_res.json()
            assert r_data["status"] == "CONFIRMED"
            assert r_data["payment_status"] == "PENDING"
            assert r_data["payment_method"] == "CASH"
            direct_reg_id = r_data["registration_id"]

            # Admin approves direct registration cash payment
            app_reg_res = await ac.post(
                f"/api/admin/registrations/{direct_reg_id}/approve-cash",
                json={"notes": "Desk 2 cash receipt"},
                headers=admin_headers
            )
            assert app_reg_res.status_code == 200
            assert app_reg_res.json()["payment_status"] == "PAID"
            assert app_reg_res.json()["payment_status_display"] == "SUCCESS"
            assert app_reg_res.json()["payment_method"] == "CASH"

        finally:
            # Cleanup
            await user_collection.delete_many({"email": user_email})
            await admin_collection.delete_many({"email": admin_email})
            await event_collection.delete_many({"_id": ObjectId(event_id)})
            await ticket_collection.delete_many({"_id": ObjectId(ticket_id)})
            await booking_collection.delete_many({"event_id": event_id})
            await issued_ticket_collection.delete_many({"event_id": event_id})
            await registration_collection.delete_many({"activity_id": event_id})
