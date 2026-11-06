"""
test_event_categories.py
========================
End-to-end tests for event categories with category-wise price and category-wise display in user profile:
1. Admin creates event with multiple custom categories and individual prices (e.g. VIP: 1500, General: 500, Balcony: 300).
2. Category ticket tiers are auto-provisioned with matching prices and capacities.
3. User books tickets for specific categories (e.g. VIP vs General), paying the category-specific price.
4. Bookings and issued tickets store category and unit price.
5. User profile dashboard (`GET /api/profile/dashboard`) displays events grouped category-wise in `my_events_by_category`.
6. User profile endpoint `GET /api/profile/my-events` returns events categorized by category.
7. Public endpoint `GET /api/activities/events/by-category` returns published events grouped by category.
8. Event registration via `POST /api/registrations/` supports category selection and charges category-specific price.
9. Admin updates event categories/prices and ticket tiers sync accordingly.
"""

import pytest
from datetime import datetime, timezone
from httpx import AsyncClient, ASGITransport
from bson import ObjectId

from app.main import app
from app.core.security import create_access_token, get_password_hash
from app.core.database import (
    user_collection,
    admin_collection,
    event_collection,
    ticket_collection,
    booking_collection,
    issued_ticket_collection,
    registration_collection,
)


@pytest.mark.asyncio
async def test_event_categories_and_category_wise_pricing_end_to_end():
    ts = int(datetime.now().timestamp())
    admin_email = f"event_admin_{ts}@test.com"
    user_email = f"event_attendee_{ts}@test.com"

    # 1. Setup Admin & User
    await admin_collection.insert_one({
        "email": admin_email,
        "password_hash": get_password_hash("adminpass123"),
        "role": "super_admin",
        "created_at": datetime.now(timezone.utc),
    })
    admin_token = create_access_token({"sub": admin_email, "type": "admin", "role": "super_admin"})
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    await user_collection.insert_one({
        "email": user_email,
        "name": "Alex EventGoer",
        "phone_number": 9876543210,
        "parent_phone_number": 9876543202,
        "date_of_birth": "1995-05-15",
        "address": "42 Festival Way",
        "role": "USER",
        "is_verified": True,
        "registration_complete": True,
        "is_profile_completed": True,
        "profile_completed": True,
        "created_at": datetime.now(timezone.utc),
    })
    user_token = create_access_token({"sub": user_email, "type": "user"})
    user_headers = {"Authorization": f"Bearer {user_token}"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 2. Admin creates event with custom categories & subfield prices
        event_payload = {
            "title": f"Global Music Summit {ts}",
            "description": "Annual global music and cultural conference",
            "date": "2026-12-25",
            "time": "06:00 PM",
            "venue": "Grand Symphony Hall",
            "capacity": 500,
            "is_published": True,
            "is_visible": True,
            "registration_enabled": True,
            "ticket_enabled": True,
            "categories": [
                {"name": "VIP", "price": 1500.0, "capacity": 50, "description": "Front stage VIP lounge with refreshments"},
                {"name": "General", "price": 500.0, "capacity": 350, "description": "Standard admission hall seating"},
                {"name": "Balcony", "price": 300.0, "capacity": 100, "description": "Elevated balcony seating"}
            ]
        }

        res_create = await client.post("/api/admin/events", json=event_payload, headers=admin_headers)
        assert res_create.status_code == 200, f"Event creation failed: {res_create.text}"
        ev_data = res_create.json()
        event_id = ev_data["_id"]
        assert len(ev_data["categories"]) == 3
        assert ev_data["categories"][0]["name"] == "VIP"
        assert ev_data["categories"][0]["price"] == 1500.0
        assert ev_data["categories"][1]["name"] == "General"
        assert ev_data["categories"][1]["price"] == 500.0
        assert ev_data["categories"][2]["name"] == "Balcony"
        assert ev_data["categories"][2]["price"] == 300.0

        # 3. Verify ticket tiers auto-created for each category
        res_tickets = await client.get(f"/api/activities/tickets?event_id={event_id}")
        assert res_tickets.status_code == 200
        tickets = res_tickets.json()
        assert len(tickets) >= 3
        vip_tier = next(t for t in tickets if t.get("category") == "VIP" or t.get("type") == "VIP")
        gen_tier = next(t for t in tickets if t.get("category") == "General" or t.get("type") == "General")
        bal_tier = next(t for t in tickets if t.get("category") == "Balcony" or t.get("type") == "Balcony")
        assert vip_tier["price"] == 1500.0
        assert gen_tier["price"] == 500.0
        assert bal_tier["price"] == 300.0

        # 4. Public events grouped by category
        res_by_cat = await client.get("/api/activities/events/by-category")
        assert res_by_cat.status_code == 200
        by_cat_data = res_by_cat.json()
        assert "VIP" in by_cat_data["by_category"]
        assert "General" in by_cat_data["by_category"]
        assert "Balcony" in by_cat_data["by_category"]
        assert any(e["_id"] == event_id for e in by_cat_data["by_category"]["VIP"])

        # 5. Public events category filter
        res_filter_vip = await client.get(f"/api/activities/events?category=VIP")
        assert res_filter_vip.status_code == 200
        assert any(e["_id"] == event_id for e in res_filter_vip.json())

        # 6. User books a VIP ticket
        vip_booking_payload = {
            "event_id": event_id,
            "category": "VIP",
            "quantity": 2,
            "holder_names": ["Alex EventGoer", "Guest VIP"],
            "payment_method": "CASH"
        }
        res_vip_book = await client.post("/api/bookings/", json=vip_booking_payload, headers=user_headers)
        assert res_vip_book.status_code == 200, f"Booking VIP failed: {res_vip_book.text}"
        vip_book_data = res_vip_book.json()
        assert vip_book_data["category"] == "VIP"
        assert vip_book_data["unit_price"] == 1500.0
        assert vip_book_data["total_amount"] == 3000.0
        assert vip_book_data["payment_method"] == "CASH"
        assert vip_book_data["booking_status"] == "CONFIRMED"

        # 7. User books a General ticket
        gen_booking_payload = {
            "event_id": event_id,
            "category": "General",
            "quantity": 1,
            "holder_names": ["Alex General"],
            "payment_method": "CASH"
        }
        res_gen_book = await client.post("/api/bookings/", json=gen_booking_payload, headers=user_headers)
        assert res_gen_book.status_code == 200, f"Booking General failed: {res_gen_book.text}"
        gen_book_data = res_gen_book.json()
        assert gen_book_data["category"] == "General"
        assert gen_book_data["unit_price"] == 500.0
        assert gen_book_data["total_amount"] == 500.0

        # 8. User profile dashboard: events must be grouped category-wise
        res_dash = await client.get("/api/profile/dashboard", headers=user_headers)
        assert res_dash.status_code == 200
        dash_data = res_dash.json()
        assert "my_events" in dash_data
        assert "my_events_by_category" in dash_data
        cat_map = dash_data["my_events_by_category"]
        assert "VIP" in cat_map, f"VIP category missing in dashboard: {cat_map.keys()}"
        assert "General" in cat_map, f"General category missing in dashboard: {cat_map.keys()}"
        
        vip_entry = next(e for e in cat_map["VIP"] if e.get("event_id") == event_id or e.get("activity_id") == event_id)
        assert vip_entry["category"] == "VIP"
        assert vip_entry["price"] == 1500.0 or vip_entry["unit_price"] == 1500.0

        gen_entry = next(e for e in cat_map["General"] if e.get("event_id") == event_id or e.get("activity_id") == event_id)
        assert gen_entry["category"] == "General"
        assert gen_entry["price"] == 500.0 or gen_entry["unit_price"] == 500.0

        # 9. Dedicated endpoint GET /api/profile/my-events
        res_my_events = await client.get("/api/profile/my-events", headers=user_headers)
        assert res_my_events.status_code == 200
        my_events_data = res_my_events.json()
        assert "events" in my_events_data
        assert "by_category" in my_events_data
        assert "categories" in my_events_data
        assert "VIP" in my_events_data["by_category"]
        assert "General" in my_events_data["by_category"]

        # 10. Direct event registration with category selection
        user2_email = f"reg_user_{ts}@test.com"
        await user_collection.insert_one({
            "email": user2_email,
            "name": "Sarah Attendee",
            "phone_number": 9876543299,
            "parent_phone_number": 9876543288,
            "date_of_birth": "1998-08-20",
            "address": "12 Balcony Street",
            "role": "USER",
            "is_verified": True,
            "registration_complete": True,
            "is_profile_completed": True,
            "profile_completed": True,
            "created_at": datetime.now(timezone.utc),
        })
        user2_token = create_access_token({"sub": user2_email, "type": "user"})
        user2_headers = {"Authorization": f"Bearer {user2_token}"}

        reg_payload = {
            "activity_id": event_id,
            "activity_type": "event",
            "category": "Balcony",
            "payment_method": "CASH"
        }
        res_reg = await client.post("/api/registrations/", json=reg_payload, headers=user2_headers)
        assert res_reg.status_code == 200, f"Registration failed: {res_reg.text}"
        reg_data = res_reg.json()
        assert reg_data["payable_amount"] == 300.0
        assert reg_data["status"] == "CONFIRMED"

        # Check user2 profile dashboard
        res_u2_dash = await client.get("/api/profile/dashboard", headers=user2_headers)
        assert res_u2_dash.status_code == 200
        u2_map = res_u2_dash.json()["my_events_by_category"]
        assert "Balcony" in u2_map
        balcony_reg = u2_map["Balcony"][0]
        assert balcony_reg["category"] == "Balcony"
        assert balcony_reg["price"] == 300.0

        # 11. Admin updates event categories (e.g. adjusts VIP price to 1800, adds Student category)
        update_payload = {
            "categories": [
                {"name": "VIP", "price": 1800.0, "capacity": 50, "description": "Updated VIP with backstage pass"},
                {"name": "General", "price": 500.0, "capacity": 350},
                {"name": "Balcony", "price": 300.0, "capacity": 100},
                {"name": "Student", "price": 200.0, "capacity": 50, "description": "Valid Student ID required"}
            ]
        }
        res_up = await client.put(f"/api/admin/events/{event_id}", json=update_payload, headers=admin_headers)
        assert res_up.status_code == 200
        updated_ev = res_up.json()
        assert len(updated_ev["categories"]) == 4
        student_cat = next(c for c in updated_ev["categories"] if c["name"] == "Student")
        assert student_cat["price"] == 200.0

        # Verify updated ticket tier in ticket collection
        res_up_tickets = await client.get(f"/api/activities/tickets?event_id={event_id}")
        assert res_up_tickets.status_code == 200
        up_tickets = res_up_tickets.json()
        vip_up_tier = next(t for t in up_tickets if t.get("category") == "VIP" or t.get("type") == "VIP")
        student_tier = next(t for t in up_tickets if t.get("category") == "Student" or t.get("type") == "Student")
        assert vip_up_tier["price"] == 1800.0
        assert student_tier["price"] == 200.0


@pytest.mark.asyncio
async def test_event_single_category_fallback_and_free_category_and_openapi():
    ts = int(datetime.now().timestamp()) + 100
    admin_email = f"event_admin_fb_{ts}@test.com"
    user_email = f"event_user_fb_{ts}@test.com"

    await admin_collection.insert_one({
        "email": admin_email,
        "password_hash": get_password_hash("adminpass123"),
        "role": "super_admin",
        "created_at": datetime.now(timezone.utc),
    })
    admin_token = create_access_token({"sub": admin_email, "type": "admin", "role": "super_admin"})
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    await user_collection.insert_one({
        "email": user_email,
        "name": "Fallback User",
        "phone_number": 9876543200,
        "parent_phone_number": 9876543201,
        "date_of_birth": "1997-03-10",
        "address": "10 Tech Road",
        "role": "USER",
        "is_verified": True,
        "registration_complete": True,
        "is_profile_completed": True,
        "profile_completed": True,
        "created_at": datetime.now(timezone.utc),
    })
    user_token = create_access_token({"sub": user_email, "type": "user"})
    user_headers = {"Authorization": f"Bearer {user_token}"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Verify OpenAPI specification builds cleanly with all new schemas
        res_openapi = await client.get("/openapi.json")
        assert res_openapi.status_code == 200
        openapi_spec = res_openapi.json()
        assert "EventCategory" in str(openapi_spec)
        assert "/api/profile/my-events" in openapi_spec["paths"]
        assert "/api/activities/events/by-category" in openapi_spec["paths"]

        # Admin creates event with mixed Free and Paid categories
        payload = {
            "title": f"Community Open Source Expo {ts}",
            "description": "Free community developer conference",
            "date": "2026-11-30",
            "venue": "City Convention Center",
            "capacity": 200,
            "is_published": True,
            "ticket_enabled": True,
            "categories": [
                {"name": "Free Community Pass", "price": 0.0, "capacity": 150},
                {"name": "Donor Supporter", "price": 499.0, "capacity": 50}
            ]
        }
        res_create = await client.post("/api/admin/events", json=payload, headers=admin_headers)
        assert res_create.status_code == 200
        event_id = res_create.json()["_id"]

        # User books Free Community Pass
        free_booking_payload = {
            "event_id": event_id,
            "category": "Free Community Pass",
            "quantity": 1
        }
        res_book_free = await client.post("/api/bookings/", json=free_booking_payload, headers=user_headers)
        assert res_book_free.status_code == 200
        book_data = res_book_free.json()
        assert book_data["category"] == "Free Community Pass"
        assert book_data["total_amount"] == 0.0
        assert book_data["booking_status"] == "CONFIRMED"
        assert book_data["payment_status"] == "NOT_REQUIRED"

        # Check user profile displays the Free category
        res_profile = await client.get("/api/profile/my-events", headers=user_headers)
        assert res_profile.status_code == 200
        prof_data = res_profile.json()
        assert "Free Community Pass" in prof_data["by_category"]
        assert prof_data["by_category"]["Free Community Pass"][0]["event_id"] == event_id


@pytest.mark.asyncio
async def test_single_ticket_multiple_categories_and_multi_category_booking():
    """
    Tests:
    1. 1 ticket tier can have multiple categories (TicketCreate.categories = ["VIP", "Meet & Greet"]).
    2. 1 booking can book tickets across multiple categories in a single request (items=[VIP, General]).
    3. User profile receives both categories in my_events_by_category from the single booking.
    """
    ts = int(datetime.now().timestamp()) + 200
    admin_email = f"multi_admin_{ts}@test.com"
    user_email = f"multi_user_{ts}@test.com"

    await admin_collection.insert_one({
        "email": admin_email,
        "password_hash": get_password_hash("adminpass123"),
        "role": "super_admin",
        "created_at": datetime.now(timezone.utc),
    })
    admin_token = create_access_token({"sub": admin_email, "type": "admin", "role": "super_admin"})
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    await user_collection.insert_one({
        "email": user_email,
        "name": "Multi Attendee",
        "phone_number": 9876543277,
        "parent_phone_number": 9876543288,
        "date_of_birth": "1994-11-20",
        "address": "99 Harmony Blvd",
        "role": "USER",
        "is_verified": True,
        "registration_complete": True,
        "is_profile_completed": True,
        "profile_completed": True,
        "created_at": datetime.now(timezone.utc),
    })
    user_token = create_access_token({"sub": user_email, "type": "user"})
    user_headers = {"Authorization": f"Bearer {user_token}"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create base event
        ev_payload = {
            "title": f"International Arts Gala {ts}",
            "description": "Multi-category gala showcase",
            "date": "2026-11-25",
            "venue": "Opera House",
            "capacity": 300,
            "is_published": True,
            "ticket_enabled": True,
            "categories": [
                {"name": "VIP Platinum", "price": 2000.0, "capacity": 50},
                {"name": "Standard Access", "price": 600.0, "capacity": 200}
            ]
        }
        res_ev = await client.post("/api/admin/events", json=ev_payload, headers=admin_headers)
        assert res_ev.status_code == 200
        event_id = res_ev.json()["_id"]

        # 1. Admin creates a ticket tier with multiple categories
        ticket_payload = {
            "event_id": event_id,
            "type": "Combo Pass",
            "categories": ["VIP Platinum", "Backstage Pass", "Meet & Greet"],
            "price": 2500.0,
            "total_quantity": 40
        }
        res_tkt = await client.post("/api/admin/tickets", json=ticket_payload, headers=admin_headers)
        assert res_tkt.status_code == 200
        tkt_data = res_tkt.json()
        assert "VIP Platinum" in tkt_data["categories"]
        assert "Meet & Greet" in tkt_data["categories"]
        tkt_id = tkt_data["_id"]

        # Admin updates ticket tier to add another category
        res_tkt_up = await client.put(f"/api/admin/tickets/{tkt_id}", json={
            "categories": ["VIP Platinum", "Backstage Pass", "Meet & Greet", "After Party"],
            "price": 2800.0
        }, headers=admin_headers)
        assert res_tkt_up.status_code == 200
        assert "After Party" in res_tkt_up.json()["categories"]
        assert res_tkt_up.json()["price"] == 2800.0

        # 2. User books multiple categories in 1 ticket booking!
        multi_book_payload = {
            "event_id": event_id,
            "items": [
                {"category": "VIP Platinum", "quantity": 1, "holder_names": ["VIP Attendee"]},
                {"category": "Standard Access", "quantity": 2, "holder_names": ["Guest 1", "Guest 2"]}
            ],
            "payment_method": "CASH"
        }
        res_multi_book = await client.post("/api/bookings/", json=multi_book_payload, headers=user_headers)
        assert res_multi_book.status_code == 200, f"Multi-category booking failed: {res_multi_book.text}"
        multi_book_data = res_multi_book.json()
        assert multi_book_data["quantity_booked"] == 3
        # 1 * 2000.0 + 2 * 600.0 = 3200.0
        assert multi_book_data["total_amount"] == 3200.0
        assert "VIP Platinum" in multi_book_data["categories"]
        assert "Standard Access" in multi_book_data["categories"]

        # 3. User profile dashboard has BOTH categories from the single booking
        res_dash = await client.get("/api/profile/dashboard", headers=user_headers)
        assert res_dash.status_code == 200
        dash_cats = res_dash.json()["my_events_by_category"]
        assert "VIP Platinum" in dash_cats
        assert "Standard Access" in dash_cats
        vip_ev = dash_cats["VIP Platinum"][0]
        std_ev = dash_cats["Standard Access"][0]
        assert vip_ev["price"] == 2000.0
        assert std_ev["price"] == 600.0
        assert std_ev["quantity"] == 2

        # 4. User profile GET /api/profile/my-events also reflects both categories
        res_my_ev = await client.get("/api/profile/my-events", headers=user_headers)
        assert res_my_ev.status_code == 200
        my_ev_data = res_my_ev.json()
        assert "VIP Platinum" in my_ev_data["by_category"]
        assert "Standard Access" in my_ev_data["by_category"]


