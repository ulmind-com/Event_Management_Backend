import pytest
import pytest_asyncio
import hmac
import hashlib
import json
import uuid
from httpx import AsyncClient, ASGITransport
from datetime import datetime, timezone, timedelta

from app.main import app
from app.core.config import settings
from app.core.database import (
    user_collection,
    admin_collection,
    merchandise_product_collection,
    merchandise_order_collection,
    merchandise_inventory_movement_collection,
    merchandise_payment_attempt_collection
)
from app.core.security import create_access_token
from app.services.merchandise_service import MerchandiseService

pytestmark = pytest.mark.asyncio

@pytest_asyncio.fixture(autouse=True)
async def cleanup_merchandise_test_data():
    await merchandise_product_collection.delete_many({"name": {"$regex": "^TEST_"}})
    await merchandise_order_collection.delete_many({"user_email": {"$regex": "^test_.*@eventora.test$"}})
    await merchandise_inventory_movement_collection.delete_many({"actor": {"$regex": ".*test.*"}})
    yield
    await merchandise_product_collection.delete_many({"name": {"$regex": "^TEST_"}})
    await merchandise_order_collection.delete_many({"user_email": {"$regex": "^test_.*@eventora.test$"}})
    await merchandise_inventory_movement_collection.delete_many({"actor": {"$regex": ".*test.*"}})

@pytest_asyncio.fixture
async def merch_test_context():
    now_utc = datetime.now(timezone.utc)
    admin_email = f"test_merch_admin_{uuid.uuid4().hex[:6]}@eventora.test"
    user1_email = f"test_user1_{uuid.uuid4().hex[:6]}@eventora.test"
    user2_email = f"test_user2_{uuid.uuid4().hex[:6]}@eventora.test"

    await admin_collection.insert_one({
        "name": "Super Admin",
        "email": admin_email,
        "role": "SUPER_ADMIN",
        "created_at": now_utc
    })

    await user_collection.insert_one({
        "name": "Alice Buyer",
        "email": user1_email,
        "role": "USER",
        "registration_complete": True,
        "created_at": now_utc
    })

    await user_collection.insert_one({
        "name": "Bob Buyer",
        "email": user2_email,
        "role": "USER",
        "registration_complete": True,
        "created_at": now_utc
    })

    admin_token = create_access_token(data={"sub": admin_email, "type": "admin"})
    user1_token = create_access_token(data={"sub": user1_email, "type": "user"})
    user2_token = create_access_token(data={"sub": user2_email, "type": "user"})

    return {
        "admin_email": admin_email,
        "admin_token": admin_token,
        "user1_email": user1_email,
        "user1_token": user1_token,
        "user2_email": user2_email,
        "user2_token": user2_token
    }

# ──────────────────────────────────────────────────────────
# 1. PRODUCT CREATION & PAID-ONLY POLICY
# ──────────────────────────────────────────────────────────

async def test_super_admin_create_product_success(merch_test_context):
    data = merch_test_context
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        payload = {
            "name": "TEST_Eventora Official T-Shirt",
            "description": "Premium 100% Cotton Event T-shirt",
            "category": "T-shirts",
            "price": 599.0,
            "currency": "INR",
            "stock_quantity": 20,
            "variants": [
                {"variant_id": "var_blk_m", "sku": "TSH-BLK-M", "size": "M", "color": "Black", "stock_quantity": 10},
                {"variant_id": "var_blk_l", "sku": "TSH-BLK-L", "size": "L", "color": "Black", "stock_quantity": 10}
            ],
            "shipping_fee": 50.0,
            "status": "ACTIVE"
        }
        res = await ac.post(
            "/api/admin/merchandise/products",
            json=payload,
            headers={"Authorization": f"Bearer {data['admin_token']}"}
        )
        assert res.status_code == 201
        body = res.json()
        assert body["name"] == "TEST_Eventora Official T-Shirt"
        assert body["price"] == 599.0
        assert body["stock_quantity"] == 20
        assert len(body["variants"]) == 2

async def test_ordinary_user_cannot_create_product(merch_test_context):
    data = merch_test_context
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        payload = {
            "name": "TEST_Hacker Mug",
            "category": "Mugs",
            "price": 299.0,
            "stock_quantity": 5
        }
        res = await ac.post(
            "/api/admin/merchandise/products",
            json=payload,
            headers={"Authorization": f"Bearer {data['user1_token']}"}
        )
        assert res.status_code == 403

async def test_zero_or_negative_price_strictly_rejected(merch_test_context):
    data = merch_test_context
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Zero price product
        res = await ac.post(
            "/api/admin/merchandise/products",
            json={"name": "TEST_Free Cap", "category": "Caps", "price": 0.0, "stock_quantity": 10},
            headers={"Authorization": f"Bearer {data['admin_token']}"}
        )
        assert res.status_code in [400, 422]

        # Negative price product
        res_neg = await ac.post(
            "/api/admin/merchandise/products",
            json={"name": "TEST_Negative Cap", "category": "Caps", "price": -50.0, "stock_quantity": 10},
            headers={"Authorization": f"Bearer {data['admin_token']}"}
        )
        assert res_neg.status_code in [400, 422]

# ──────────────────────────────────────────────────────────
# 2. PRODUCT DISCOVERY & ARCHIVE PROTECTION
# ──────────────────────────────────────────────────────────

async def test_product_discovery_and_archived_filtering(merch_test_context):
    data = merch_test_context
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        p1 = await ac.post(
            "/api/admin/merchandise/products",
            json={"name": "TEST_Active Hoodie", "category": "Hoodies", "price": 1299.0, "stock_quantity": 15, "status": "ACTIVE"},
            headers={"Authorization": f"Bearer {data['admin_token']}"}
        )
        assert p1.status_code == 201
        p1_id = p1.json()["id"]

        p2 = await ac.post(
            "/api/admin/merchandise/products",
            json={"name": "TEST_Archived Hoodie", "category": "Hoodies", "price": 1299.0, "stock_quantity": 15, "status": "DRAFT"},
            headers={"Authorization": f"Bearer {data['admin_token']}"}
        )
        assert p2.status_code == 201
        p2_id = p2.json()["id"]

        pub_list = await ac.get("/api/merchandise/products?category=Hoodies")
        assert pub_list.status_code == 200
        prods = pub_list.json()["products"]
        assert any(p["name"] == "TEST_Active Hoodie" for p in prods)
        assert not any(p["name"] == "TEST_Archived Hoodie" for p in prods)

        arc_res = await ac.post(
            f"/api/admin/merchandise/products/{p1_id}/archive",
            headers={"Authorization": f"Bearer {data['admin_token']}"}
        )
        assert arc_res.status_code == 200
        assert arc_res.json()["status"] == "ARCHIVED"

        det_res = await ac.get(f"/api/merchandise/products/{p1_id}")
        assert det_res.status_code == 404

# ──────────────────────────────────────────────────────────
# 3. ORDER CREATION, ATOMIC ALLOCATION & OVERSELLING
# ──────────────────────────────────────────────────────────

async def test_order_creation_and_atomic_stock_reservation(merch_test_context):
    data = merch_test_context
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        p_res = await ac.post(
            "/api/admin/merchandise/products",
            json={
                "name": "TEST_Limited Badge",
                "category": "Badges",
                "price": 150.0,
                "stock_quantity": 2,
                "shipping_fee": 20.0,
                "status": "ACTIVE"
            },
            headers={"Authorization": f"Bearer {data['admin_token']}"}
        )
        assert p_res.status_code == 201
        product_id = p_res.json()["id"]

        shipping = {
            "full_name": "Alice Buyer",
            "phone": "+919876543210",
            "address_line1": "Flat 4B, Green Towers",
            "city": "Kolkata",
            "state": "West Bengal",
            "postal_code": "700001",
            "country": "India"
        }

        ord1_res = await ac.post(
            "/api/merchandise/orders",
            json={
                "items": [{"product_id": product_id, "quantity": 1}],
                "shipping_address": shipping
            },
            headers={"Authorization": f"Bearer {data['user1_token']}"}
        )
        assert ord1_res.status_code == 201
        ord1 = ord1_res.json()
        assert ord1["status"] == "PENDING_PAYMENT"
        assert ord1["payment_status"] == "PENDING"
        assert ord1["subtotal"] == 150.0
        assert ord1["shipping_fee"] == 20.0
        assert ord1["total_amount"] == 170.0

        ord2_res = await ac.post(
            "/api/merchandise/orders",
            json={
                "items": [{"product_id": product_id, "quantity": 1}],
                "shipping_address": shipping
            },
            headers={"Authorization": f"Bearer {data['user2_token']}"}
        )
        assert ord2_res.status_code == 201

        ord3_res = await ac.post(
            "/api/merchandise/orders",
            json={
                "items": [{"product_id": product_id, "quantity": 1}],
                "shipping_address": shipping
            },
            headers={"Authorization": f"Bearer {data['user1_token']}"}
        )
        assert ord3_res.status_code == 409
        assert "insufficient stock" in ord3_res.json()["detail"].lower() or "sold out" in ord3_res.json()["detail"].lower()

# ──────────────────────────────────────────────────────────
# 4. PAYMENT WEBHOOK, IDEMPOTENCY & STOCK COMMITMENT
# ──────────────────────────────────────────────────────────

async def test_valid_webhook_confirms_order_and_commits_stock(merch_test_context):
    data = merch_test_context
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        p_res = await ac.post(
            "/api/admin/merchandise/products",
            json={"name": "TEST_Water Bottle", "category": "Accessories", "price": 400.0, "stock_quantity": 5, "status": "ACTIVE"},
            headers={"Authorization": f"Bearer {data['admin_token']}"}
        )
        product_id = p_res.json()["id"]

        shipping = {
            "full_name": "Alice Buyer",
            "phone": "+919876543210",
            "address_line1": "Flat 4B, Green Towers",
            "city": "Kolkata",
            "state": "West Bengal",
            "postal_code": "700001"
        }

        ord_res = await ac.post(
            "/api/merchandise/orders",
            json={"items": [{"product_id": product_id, "quantity": 2}], "shipping_address": shipping},
            headers={"Authorization": f"Bearer {data['user1_token']}"}
        )
        assert ord_res.status_code == 201
        order_number = ord_res.json()["order_number"]
        total_amount = ord_res.json()["total_amount"]

        bad_wh = await ac.post(
            "/api/merchandise/payments/webhook",
            json={"order_number": order_number, "amount": total_amount},
            headers={"x-webhook-signature": "invalid_sig"}
        )
        assert bad_wh.status_code == 400

        secret = getattr(settings, "PAYMENT_WEBHOOK_SECRET", None) or getattr(settings, "SECRET_KEY", "eventora_secret_key")
        payload = {
            "event": "payment.captured",
            "order_number": order_number,
            "payment_id": f"pay_{uuid.uuid4().hex[:10]}",
            "amount": total_amount,
            "currency": "INR"
        }
        body_bytes = json.dumps(payload).encode("utf-8")
        valid_sig = hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()

        wh_res = await ac.post(
            "/api/merchandise/payments/webhook",
            content=body_bytes,
            headers={"Content-Type": "application/json", "x-webhook-signature": valid_sig}
        )
        assert wh_res.status_code == 200
        assert wh_res.json()["status"] == "SUCCESS"

        db_order = await merchandise_order_collection.find_one({"order_number": order_number})
        assert db_order["status"] == "CONFIRMED"
        assert db_order["payment_status"] == "PAID"
        assert db_order["paid_at"] is not None

        db_prod = await merchandise_product_collection.find_one({"_id": MerchandiseService._to_object_id(product_id)})
        assert db_prod["stock_quantity"] == 3
        assert db_prod["reserved_quantity"] == 0

        dup_res = await ac.post(
            "/api/merchandise/payments/webhook",
            content=body_bytes,
            headers={"Content-Type": "application/json", "x-webhook-signature": valid_sig}
        )
        assert dup_res.status_code == 200
        assert dup_res.json()["status"] == "ALREADY_CONFIRMED"

        db_prod_after = await merchandise_product_collection.find_one({"_id": MerchandiseService._to_object_id(product_id)})
        assert db_prod_after["stock_quantity"] == 3

# ──────────────────────────────────────────────────────────
# 5. USER ORDER TRACKING & FULFILLMENT LIFECYCLE
# ──────────────────────────────────────────────────────────

async def test_order_tracking_and_fulfillment_lifecycle(merch_test_context):
    data = merch_test_context
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        p_res = await ac.post(
            "/api/admin/merchandise/products",
            json={"name": "TEST_Poster Pack", "category": "Posters", "price": 250.0, "stock_quantity": 10, "status": "ACTIVE"},
            headers={"Authorization": f"Bearer {data['admin_token']}"}
        )
        product_id = p_res.json()["id"]

        shipping = {
            "full_name": "Alice Buyer",
            "phone": "+919876543210",
            "address_line1": "Flat 4B, Green Towers",
            "city": "Kolkata",
            "state": "West Bengal",
            "postal_code": "700001"
        }

        ord_res = await ac.post(
            "/api/merchandise/orders",
            json={"items": [{"product_id": product_id, "quantity": 1}], "shipping_address": shipping},
            headers={"Authorization": f"Bearer {data['user1_token']}"}
        )
        order_number = ord_res.json()["order_number"]

        await merchandise_order_collection.update_one(
            {"order_number": order_number},
            {"$set": {"status": "CONFIRMED", "payment_status": "PAID"}}
        )

        track_res = await ac.get(
            f"/api/merchandise/orders/{order_number}/tracking",
            headers={"Authorization": f"Bearer {data['user1_token']}"}
        )
        assert track_res.status_code == 200
        assert track_res.json()["order_number"] == order_number

        unauth_track = await ac.get(
            f"/api/merchandise/orders/{order_number}/tracking",
            headers={"Authorization": f"Bearer {data['user2_token']}"}
        )
        assert unauth_track.status_code == 404

        ship_res = await ac.patch(
            f"/api/admin/merchandise/orders/{order_number}/status",
            json={"status": "SHIPPED", "courier_name": "BlueDart", "tracking_number": "BD99887766"},
            headers={"Authorization": f"Bearer {data['admin_token']}"}
        )
        assert ship_res.status_code == 200
        assert ship_res.json()["status"] == "SHIPPED"
        assert ship_res.json()["tracking_number"] == "BD99887766"

# ──────────────────────────────────────────────────────────
# 6. INVENTORY ADJUSTMENTS & ANALYTICS
# ──────────────────────────────────────────────────────────

async def test_admin_stock_adjustment_and_analytics(merch_test_context):
    data = merch_test_context
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        p_res = await ac.post(
            "/api/admin/merchandise/products",
            json={"name": "TEST_Keychain", "category": "Accessories", "price": 99.0, "stock_quantity": 4, "low_stock_threshold": 5, "status": "ACTIVE"},
            headers={"Authorization": f"Bearer {data['admin_token']}"}
        )
        product_id = p_res.json()["id"]

        adj_res = await ac.post(
            "/api/admin/merchandise/inventory/adjustments",
            json={"product_id": product_id, "quantity_delta": 10, "reason": "Restocked from warehouse shipment"},
            headers={"Authorization": f"Bearer {data['admin_token']}"}
        )
        assert adj_res.status_code == 200
        assert adj_res.json()["stock_quantity"] == 14

        an_res = await ac.get(
            "/api/admin/merchandise/analytics",
            headers={"Authorization": f"Bearer {data['admin_token']}"}
        )
        assert an_res.status_code == 200
        analytics = an_res.json()
        assert analytics["total_products"] >= 1
        assert "gross_revenue" in analytics
        assert "units_sold" in analytics

async def test_unauthenticated_user_cannot_place_order():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as ac:
        res = await ac.post(
            '/api/merchandise/orders',
            json={
                'items': [{'product_id': '507f1f77bcf86cd799439011', 'quantity': 1}],
                'shipping_address': {
                    'full_name': 'Anonymous',
                    'phone': '9876543210',
                    'address_line1': 'Unknown Street',
                    'city': 'Kolkata',
                    'state': 'WB',
                    'postal_code': '700001'
                }
            }
        )
        assert res.status_code == 401
