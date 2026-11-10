import pytest
import pytest_asyncio
import asyncio
import uuid
from httpx import AsyncClient, ASGITransport
from datetime import datetime, timezone, timedelta
from bson import ObjectId

from app.main import app
from app.core.database import (
    user_collection,
    admin_collection,
    event_collection,
    ticket_collection,
    booking_collection,
    merchandise_product_collection,
    merchandise_order_collection,
    merchandise_inventory_movement_collection,
    vip_configuration_collection,
    vip_booking_collection,
)
from app.core.security import create_access_token

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def cleanup_concurrency_test_data():
    await merchandise_product_collection.delete_many({"name": {"$regex": "^TEST_CONCURRENCY_"}})
    await merchandise_order_collection.delete_many({"user_email": {"$regex": "^test_conc_.*@eventora.test$"}})
    await merchandise_inventory_movement_collection.delete_many({"actor": {"$regex": "^test_conc_.*@eventora.test$"}})
    await vip_configuration_collection.delete_many({"name": {"$regex": "^TEST_CONCURRENCY_"}})
    await vip_booking_collection.delete_many({"user_email": {"$regex": "^test_conc_.*@eventora.test$"}})
    await event_collection.delete_many({"title": {"$regex": "^TEST_CONCURRENCY_"}})
    await ticket_collection.delete_many({"type": {"$regex": "^TEST_CONCURRENCY_"}})
    await booking_collection.delete_many({"user_email": {"$regex": "^test_conc_.*@eventora.test$"}})
    yield
    await merchandise_product_collection.delete_many({"name": {"$regex": "^TEST_CONCURRENCY_"}})
    await merchandise_order_collection.delete_many({"user_email": {"$regex": "^test_conc_.*@eventora.test$"}})
    await merchandise_inventory_movement_collection.delete_many({"actor": {"$regex": "^test_conc_.*@eventora.test$"}})
    await vip_configuration_collection.delete_many({"name": {"$regex": "^TEST_CONCURRENCY_"}})
    await vip_booking_collection.delete_many({"user_email": {"$regex": "^test_conc_.*@eventora.test$"}})
    await event_collection.delete_many({"title": {"$regex": "^TEST_CONCURRENCY_"}})
    await ticket_collection.delete_many({"type": {"$regex": "^TEST_CONCURRENCY_"}})
    await booking_collection.delete_many({"user_email": {"$regex": "^test_conc_.*@eventora.test$"}})


@pytest_asyncio.fixture
async def conc_test_context():
    now_utc = datetime.now(timezone.utc)
    admin_email = f"test_conc_admin_{uuid.uuid4().hex[:6]}@eventora.test"

    await admin_collection.insert_one({
        "name": "Super Admin",
        "email": admin_email,
        "role": "SUPER_ADMIN",
        "created_at": now_utc
    })
    admin_token = create_access_token(data={"sub": admin_email, "type": "admin"})

    # Helper function to generate authenticated users on demand
    async def create_user(idx: int):
        email = f"test_conc_u{idx}_{uuid.uuid4().hex[:6]}@eventora.test"
        await user_collection.insert_one({
            "name": f"Concurrent User {idx}",
            "email": email,
            "role": "USER",
            "phone_number": f"+91987654{idx:04d}",
            "date_of_birth": "1995-05-15",
            "address": "123 Concurrency Way",
            "parent_phone_number": "+919876540000",
            "registration_complete": True,
            "created_at": now_utc
        })
        token = create_access_token(data={"sub": email, "type": "user"})
        return {"email": email, "token": token}

    return {
        "admin_email": admin_email,
        "admin_token": admin_token,
        "create_user": create_user
    }


# ──────────────────────────────────────────────────────────
# 1. 10 Concurrent Purchase Requests for Stock = 1
# ──────────────────────────────────────────────────────────

async def test_10_concurrent_purchases_stock_1(conc_test_context):
    """
    10 simultaneous purchase requests sent for a product with Stock = 1.
    Must guarantee:
    - Exactly 1 purchase succeeds (HTTP 201).
    - Exactly 9 requests are rejected (HTTP 409 Conflict).
    - Total orders in DB = 1.
    - Product reserved_quantity = 1, available stock = 0.
    """
    ctx = conc_test_context
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Create product with stock = 1
        p_res = await ac.post(
            "/api/admin/merchandise/products",
            json={
                "name": "TEST_CONCURRENCY_Product_10",
                "category": "Caps",
                "price": 350.0,
                "stock_quantity": 1,
                "shipping_fee": 30.0,
                "status": "ACTIVE"
            },
            headers={"Authorization": f"Bearer {ctx['admin_token']}"}
        )
        assert p_res.status_code == 201
        product_id = p_res.json()["id"]

        # 2. Prepare 10 concurrent buyers
        users = [await ctx["create_user"](i) for i in range(10)]
        shipping = {
            "full_name": "Test Buyer",
            "phone": "+919876543210",
            "address_line1": "Concurrency Tower",
            "city": "Mumbai",
            "state": "Maharashtra",
            "postal_code": "400001",
            "country": "India"
        }

        # 3. Fire 10 simultaneous purchase requests
        async def purchase(user):
            return await ac.post(
                "/api/merchandise/orders",
                json={
                    "items": [{"product_id": product_id, "quantity": 1}],
                    "shipping_address": shipping
                },
                headers={"Authorization": f"Bearer {user['token']}"}
            )

        responses = await asyncio.gather(*[purchase(u) for u in users])

        # 4. Verify outcomes
        successes = [r for r in responses if r.status_code == 201]
        conflicts = [r for r in responses if r.status_code == 409]

        assert len(successes) == 1, f"Expected exactly 1 success, got {len(successes)}"
        assert len(conflicts) == 9, f"Expected exactly 9 conflicts, got {len(conflicts)}"

        # Verify Out-of-Stock Conflict details
        for c in conflicts:
            assert "sold out" in c.json()["detail"].lower() or "insufficient stock" in c.json()["detail"].lower()

        # 5. Verify Database State
        product_db = await merchandise_product_collection.find_one({"_id": ObjectId(product_id)})
        assert product_db["stock_quantity"] == 1
        assert product_db["reserved_quantity"] == 1
        assert (product_db["stock_quantity"] - product_db["reserved_quantity"]) == 0

        orders_count = await merchandise_order_collection.count_documents({"items.product_id": product_id})
        assert orders_count == 1


# ──────────────────────────────────────────────────────────
# 2. 100 Concurrent Purchase Requests for Stock = 1
# ──────────────────────────────────────────────────────────

async def test_100_concurrent_purchases_stock_1(conc_test_context):
    """
    100 simultaneous purchase requests sent for a product with Stock = 1.
    Must guarantee:
    - Exactly 1 purchase succeeds (HTTP 201).
    - Exactly 99 requests are rejected (HTTP 409 Conflict).
    - Total orders in DB = 1.
    - Zero overselling.
    """
    ctx = conc_test_context
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        p_res = await ac.post(
            "/api/admin/merchandise/products",
            json={
                "name": "TEST_CONCURRENCY_Product_100",
                "category": "Posters",
                "price": 250.0,
                "stock_quantity": 1,
                "shipping_fee": 20.0,
                "status": "ACTIVE"
            },
            headers={"Authorization": f"Bearer {ctx['admin_token']}"}
        )
        assert p_res.status_code == 201
        product_id = p_res.json()["id"]

        users = [await ctx["create_user"](i) for i in range(100)]
        shipping = {
            "full_name": "Test Buyer",
            "phone": "+919876543210",
            "address_line1": "Concurrency Tower",
            "city": "Mumbai",
            "state": "Maharashtra",
            "postal_code": "400001"
        }

        async def purchase(user):
            return await ac.post(
                "/api/merchandise/orders",
                json={
                    "items": [{"product_id": product_id, "quantity": 1}],
                    "shipping_address": shipping
                },
                headers={"Authorization": f"Bearer {user['token']}"}
            )

        responses = await asyncio.gather(*[purchase(u) for u in users])

        successes = [r for r in responses if r.status_code == 201]
        conflicts = [r for r in responses if r.status_code == 409]

        assert len(successes) == 1, f"Expected exactly 1 success, got {len(successes)}"
        assert len(conflicts) == 99, f"Expected exactly 99 conflicts, got {len(conflicts)}"

        # Database consistency assertion
        product_db = await merchandise_product_collection.find_one({"_id": ObjectId(product_id)})
        assert product_db["stock_quantity"] == 1
        assert product_db["reserved_quantity"] == 1

        orders_count = await merchandise_order_collection.count_documents({"items.product_id": product_id})
        assert orders_count == 1


# ──────────────────────────────────────────────────────────
# 3. 1,000 Concurrent Purchase Requests for Stock = 1
# ──────────────────────────────────────────────────────────

async def test_1000_concurrent_purchases_stock_1(conc_test_context):
    """
    1,000 simultaneous purchase requests sent for a product with Stock = 1.
    Must guarantee:
    - Exactly 1 purchase succeeds (HTTP 201).
    - Exactly 999 requests are rejected (HTTP 409 Conflict).
    - Zero deadlocks, zero overselling, zero database connection leaks.
    """
    ctx = conc_test_context
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        p_res = await ac.post(
            "/api/admin/merchandise/products",
            json={
                "name": "TEST_CONCURRENCY_Product_1000",
                "category": "Badges",
                "price": 199.0,
                "stock_quantity": 1,
                "shipping_fee": 15.0,
                "status": "ACTIVE"
            },
            headers={"Authorization": f"Bearer {ctx['admin_token']}"}
        )
        assert p_res.status_code == 201
        product_id = p_res.json()["id"]

        # Create a pool of distinct buyers
        # Using a pool of 20 authenticated user tokens across 1,000 concurrent purchase attempts
        buyer_pool = [await ctx["create_user"](i) for i in range(25)]
        shipping = {
            "full_name": "Ultra Concurrent Buyer",
            "phone": "+919876543210",
            "address_line1": "Scale Highway",
            "city": "Bengaluru",
            "state": "Karnataka",
            "postal_code": "560001"
        }

        async def purchase(idx):
            user = buyer_pool[idx % len(buyer_pool)]
            return await ac.post(
                "/api/merchandise/orders",
                json={
                    "items": [{"product_id": product_id, "quantity": 1}],
                    "shipping_address": shipping
                },
                headers={"Authorization": f"Bearer {user['token']}"}
            )

        responses = await asyncio.gather(*[purchase(i) for i in range(1000)])

        successes = [r for r in responses if r.status_code == 201]
        conflicts = [r for r in responses if r.status_code == 409]

        assert len(successes) == 1, f"High concurrency failure: expected 1 success, got {len(successes)}"
        assert len(conflicts) == 999, f"High concurrency failure: expected 999 conflicts, got {len(conflicts)}"

        # Strict Database Consistency check
        product_db = await merchandise_product_collection.find_one({"_id": ObjectId(product_id)})
        assert product_db["stock_quantity"] == 1
        assert product_db["reserved_quantity"] == 1

        orders_count = await merchandise_order_collection.count_documents({"items.product_id": product_id})
        assert orders_count == 1


# ──────────────────────────────────────────────────────────
# 4. Multi-Quantity Stock Concurrency (Stock = 5, 50 Requests)
# ──────────────────────────────────────────────────────────

async def test_multi_stock_concurrency_exact_allocations(conc_test_context):
    """
    Stock = 5. 50 concurrent buyers each request 1 unit.
    Must guarantee:
    - Exactly 5 purchases succeed.
    - Exactly 45 receive HTTP 409 Conflict.
    - Final reserved_quantity = 5, available = 0.
    """
    ctx = conc_test_context
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        p_res = await ac.post(
            "/api/admin/merchandise/products",
            json={
                "name": "TEST_CONCURRENCY_Product_Stock5",
                "category": "Mugs",
                "price": 299.0,
                "stock_quantity": 5,
                "shipping_fee": 25.0,
                "status": "ACTIVE"
            },
            headers={"Authorization": f"Bearer {ctx['admin_token']}"}
        )
        assert p_res.status_code == 201
        product_id = p_res.json()["id"]

        users = [await ctx["create_user"](i) for i in range(50)]
        shipping = {
            "full_name": "Multi Buyer",
            "phone": "+919876543210",
            "address_line1": "Mug Lane",
            "city": "Delhi",
            "state": "Delhi",
            "postal_code": "110001"
        }

        async def purchase(user):
            return await ac.post(
                "/api/merchandise/orders",
                json={
                    "items": [{"product_id": product_id, "quantity": 1}],
                    "shipping_address": shipping
                },
                headers={"Authorization": f"Bearer {user['token']}"}
            )

        responses = await asyncio.gather(*[purchase(u) for u in users])

        successes = [r for r in responses if r.status_code == 201]
        conflicts = [r for r in responses if r.status_code == 409]

        assert len(successes) == 5, f"Expected exactly 5 successes, got {len(successes)}"
        assert len(conflicts) == 45, f"Expected exactly 45 conflicts, got {len(conflicts)}"

        product_db = await merchandise_product_collection.find_one({"_id": ObjectId(product_id)})
        assert product_db["stock_quantity"] == 5
        assert product_db["reserved_quantity"] == 5

        orders_count = await merchandise_order_collection.count_documents({"items.product_id": product_id})
        assert orders_count == 5


# ──────────────────────────────────────────────────────────
# 5. Variant-Level Atomic Stock Concurrency
# ──────────────────────────────────────────────────────────

async def test_variant_level_concurrency(conc_test_context):
    """
    Product has two variants:
    - Variant A (Red-M): stock = 1
    - Variant B (Blue-L): stock = 50
    30 concurrent requests target Variant A.
    Must guarantee:
    - Exactly 1 purchase succeeds for Variant A.
    - 29 requests get HTTP 409 Conflict.
    - Variant B stock remains completely unaffected (50 available).
    """
    ctx = conc_test_context
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        p_res = await ac.post(
            "/api/admin/merchandise/products",
            json={
                "name": "TEST_CONCURRENCY_Variant_Product",
                "category": "T-shirts",
                "price": 599.0,
                "variants": [
                    {
                        "variant_id": "VAR_RED_M",
                        "sku": "TSH-RED-M",
                        "size": "M",
                        "color": "Red",
                        "stock_quantity": 1
                    },
                    {
                        "variant_id": "VAR_BLUE_L",
                        "sku": "TSH-BLUE-L",
                        "size": "L",
                        "color": "Blue",
                        "stock_quantity": 50
                    }
                ],
                "shipping_fee": 40.0,
                "status": "ACTIVE"
            },
            headers={"Authorization": f"Bearer {ctx['admin_token']}"}
        )
        assert p_res.status_code == 201
        product_id = p_res.json()["id"]

        users = [await ctx["create_user"](i) for i in range(30)]
        shipping = {
            "full_name": "Variant Buyer",
            "phone": "+919876543210",
            "address_line1": "Fashion Avenue",
            "city": "Pune",
            "state": "Maharashtra",
            "postal_code": "411001"
        }

        async def purchase_var_a(user):
            return await ac.post(
                "/api/merchandise/orders",
                json={
                    "items": [{
                        "product_id": product_id,
                        "variant_id": "VAR_RED_M",
                        "quantity": 1
                    }],
                    "shipping_address": shipping
                },
                headers={"Authorization": f"Bearer {user['token']}"}
            )

        responses = await asyncio.gather(*[purchase_var_a(u) for u in users])

        successes = [r for r in responses if r.status_code == 201]
        conflicts = [r for r in responses if r.status_code == 409]

        assert len(successes) == 1, f"Expected 1 success for variant A, got {len(successes)}"
        assert len(conflicts) == 29, f"Expected 29 conflicts for variant A, got {len(conflicts)}"

        # Verify DB state of variants
        product_db = await merchandise_product_collection.find_one({"_id": ObjectId(product_id)})
        var_a = next(v for v in product_db["variants"] if v["variant_id"] == "VAR_RED_M")
        var_b = next(v for v in product_db["variants"] if v["variant_id"] == "VAR_BLUE_L")

        assert var_a["stock_quantity"] == 1
        assert var_a["reserved_quantity"] == 1

        assert var_b["stock_quantity"] == 50
        assert var_b.get("reserved_quantity", 0) == 0


# ──────────────────────────────────────────────────────────
# 6. VIP Pass Booking Concurrency (Capacity = 1, 30 Requests)
# ──────────────────────────────────────────────────────────

async def test_vip_pass_concurrency_control(conc_test_context):
    """
    VIP Pass configuration with total capacity = 1.
    30 concurrent requests sent to book.
    Must guarantee:
    - Exactly 1 booking succeeds (HTTP 201).
    - 29 requests receive HTTP 409 Conflict (SOLD OUT).
    - DB allocated_count = 1.
    """
    ctx = conc_test_context
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        now = datetime.now(timezone.utc)
        # Create Event
        ev_res = await event_collection.insert_one({
            "title": "TEST_CONCURRENCY_VIP_Festival",
            "name": "TEST_CONCURRENCY_VIP_Festival",
            "is_published": True,
            "registration_enabled": True,
            "ticket_enabled": True,
            "created_at": now
        })
        event_id = str(ev_res.inserted_id)

        # Create VIP Config with capacity 1
        cfg_res = await ac.post(
            f"/api/admin/events/{event_id}/vip-configurations",
            json={
                "name": "TEST_CONCURRENCY_Exclusive_VIP",
                "price": 1200.0,
                "capacity": 1,
                "booking_start": (now - timedelta(hours=1)).isoformat(),
                "booking_end": (now + timedelta(days=2)).isoformat(),
                "validity_start": now.isoformat(),
                "validity_end": (now + timedelta(days=2)).isoformat(),
                "max_passes_per_user": 1,
                "is_enabled": True
            },
            headers={"Authorization": f"Bearer {ctx['admin_token']}"}
        )
        assert cfg_res.status_code == 201
        config_id = cfg_res.json()["id"]

        users = [await ctx["create_user"](i) for i in range(30)]

        async def book_vip(user):
            return await ac.post(
                f"/api/events/{event_id}/vip-passes/{config_id}/book",
                json={"quantity": 1, "holder_names": ["VIP Concurrency Guest"]},
                headers={"Authorization": f"Bearer {user['token']}"}
            )

        responses = await asyncio.gather(*[book_vip(u) for u in users])

        successes = [r for r in responses if r.status_code == 201]
        conflicts = [r for r in responses if r.status_code == 409]

        assert len(successes) == 1, f"Expected 1 VIP booking success, got {len(successes)}"
        assert len(conflicts) == 29, f"Expected 29 VIP booking conflicts, got {len(conflicts)}"

        cfg_db = await vip_configuration_collection.find_one({"_id": ObjectId(config_id)})
        assert cfg_db["allocated_count"] == 1
        assert cfg_db["capacity"] == 1


# ──────────────────────────────────────────────────────────
# 7. Ticket Booking Concurrency (Available = 1, 30 Requests)
# ──────────────────────────────────────────────────────────

async def test_ticket_booking_concurrency_control(conc_test_context):
    """
    Ticket tier with available = 1.
    30 concurrent bookings sent.
    Must guarantee:
    - Exactly 1 booking succeeds (HTTP 200/201).
    - 29 bookings receive HTTP 409 Conflict.
    - Available tickets in DB == 0.
    """
    ctx = conc_test_context
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        now = datetime.now(timezone.utc)
        ev_res = await event_collection.insert_one({
            "title": "TEST_CONCURRENCY_Concert",
            "name": "TEST_CONCURRENCY_Concert",
            "is_published": True,
            "registration_enabled": True,
            "ticket_enabled": True,
            "created_at": now
        })
        event_id = str(ev_res.inserted_id)

        # Create ticket tier with available = 1
        tkt_res = await ticket_collection.insert_one({
            "event_id": event_id,
            "type": "TEST_CONCURRENCY_General",
            "category": "TEST_CONCURRENCY_General",
            "price": 500.0,
            "total_quantity": 1,
            "available": 1,
            "is_visible": True,
            "created_at": now
        })
        ticket_id = str(tkt_res.inserted_id)

        users = [await ctx["create_user"](i) for i in range(30)]

        async def book_ticket(user):
            return await ac.post(
                "/api/bookings/",
                json={
                    "event_id": event_id,
                    "ticket_id": ticket_id,
                    "quantity": 1,
                    "category": "TEST_CONCURRENCY_General",
                    "payment_method": "ONLINE"
                },
                headers={"Authorization": f"Bearer {user['token']}"}
            )

        responses = await asyncio.gather(*[book_ticket(u) for u in users])

        successes = [r for r in responses if r.status_code in (200, 201)]
        conflicts = [r for r in responses if r.status_code == 409]

        assert len(successes) == 1, f"Expected 1 ticket booking success, got {len(successes)}"
        assert len(conflicts) == 29, f"Expected 29 ticket booking conflicts, got {len(conflicts)}"

        ticket_db = await ticket_collection.find_one({"_id": ObjectId(ticket_id)})
        assert ticket_db["available"] == 0


# ──────────────────────────────────────────────────────────
# 8. Double Cancellation / Double Restock Race Condition Prevention
# ──────────────────────────────────────────────────────────

async def test_cancellation_race_condition_no_double_restock(conc_test_context):
    """
    A booking exists with 2 tickets.
    15 simultaneous cancellation requests are fired for the SAME booking.
    Must guarantee:
    - Exactly 1 cancellation succeeds.
    - 14 requests are rejected (already cancelled).
    - Available tickets are restocked by EXACTLY 2 (never 30).
    """
    ctx = conc_test_context
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        now = datetime.now(timezone.utc)
        user = await ctx["create_user"](1)

        tkt_res = await ticket_collection.insert_one({
            "event_id": "ev_cancel_test",
            "type": "TEST_CONCURRENCY_CancelTier",
            "category": "TEST_CONCURRENCY_CancelTier",
            "price": 200.0,
            "total_quantity": 10,
            "available": 8,  # 2 were booked
            "is_visible": True,
            "created_at": now
        })
        ticket_id = str(tkt_res.inserted_id)

        bkg_res = await booking_collection.insert_one({
            "booking_reference": f"BKG-TEST-{uuid.uuid4().hex[:6]}",
            "event_id": "ev_cancel_test",
            "ticket_id": ticket_id,
            "user_email": user["email"],
            "quantity": 2,
            "total_amount": 400.0,
            "booking_status": "PENDING",
            "payment_status": "PENDING",
            "created_at": now
        })
        booking_id = str(bkg_res.inserted_id)

        async def cancel_req():
            return await ac.post(
                f"/api/bookings/{booking_id}/cancel",
                headers={"Authorization": f"Bearer {user['token']}"}
            )

        responses = await asyncio.gather(*[cancel_req() for _ in range(15)])

        successes = [r for r in responses if r.status_code == 200]
        failures = [r for r in responses if r.status_code == 400]

        assert len(successes) == 1, f"Expected 1 cancel success, got {len(successes)}"
        assert len(failures) == 14, f"Expected 14 cancel failures, got {len(failures)}"

        # Available tickets should now be exactly 8 + 2 = 10, NOT 8 + (2 * 15) = 38!
        ticket_db = await ticket_collection.find_one({"_id": ObjectId(ticket_id)})
        assert ticket_db["available"] == 10, f"Ticket available count over-restocked: {ticket_db['available']}"

