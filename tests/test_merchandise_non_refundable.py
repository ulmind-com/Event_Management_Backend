import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from datetime import datetime, timezone
from bson import ObjectId
from app.main import app
from app.core.database import (
    merchandise_order_collection,
    merchandise_product_collection,
    user_collection
)
from app.core.security import create_access_token
from app.schemas.merchandise import OrderStatus, PaymentStatus


@pytest_asyncio.fixture
async def ac():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_merchandise_orders_are_strictly_non_refundable_and_cannot_be_cancelled(ac: AsyncClient):
    """
    End-to-End Test:
    Ensures that once a merchandise order is placed (CONFIRMED, PAID, PROCESSING, etc.),
    it CANNOT be cancelled or refunded under any circumstance.
    """
    unique_suffix = str(ObjectId())[:8]
    test_user_email = f"buyer_{unique_suffix}@test.com"
    prod_id = str(ObjectId())
    order_id = str(ObjectId())
    order_number = f"ORD-TEST-{unique_suffix}"

    try:
        # 1. Setup User
        await user_collection.insert_one({
            "email": test_user_email,
            "name": "Merchandise Buyer",
            "role": "user",
            "registration_complete": True,
            "created_at": datetime.now(timezone.utc)
        })
        user_headers = {"Authorization": f"Bearer {create_access_token({'sub': test_user_email})}"}

        # 2. Setup Product
        await merchandise_product_collection.insert_one({
            "_id": ObjectId(prod_id),
            "title": "Official Festival Hoodie",
            "price": 1499.0,
            "stock_quantity": 50,
            "reserved_quantity": 0,
            "is_active": True,
            "created_at": datetime.now(timezone.utc)
        })

        # 3. Setup a Placed & Confirmed Order (Paid)
        await merchandise_order_collection.insert_one({
            "_id": ObjectId(order_id),
            "order_number": order_number,
            "user_email": test_user_email,
            "user_name": "Merchandise Buyer",
            "items": [
                {
                    "product_id": prod_id,
                    "title": "Official Festival Hoodie",
                    "unit_price": 1499.0,
                    "quantity": 1,
                    "total_price": 1499.0
                }
            ],
            "total_amount": 1499.0,
            "status": OrderStatus.CONFIRMED.value,
            "payment_status": PaymentStatus.PAID.value,
            "shipping_address": {
                "full_name": "Merchandise Buyer",
                "phone": "+919876543210",
                "address_line1": "123 Event Street",
                "city": "Kolkata",
                "state": "West Bengal",
                "postal_code": "700001",
                "country": "India"
            },
            "status_history": [
                {"status": OrderStatus.CONFIRMED.value, "timestamp": datetime.now(timezone.utc), "actor": "system"}
            ],
            "created_at": datetime.now(timezone.utc)
        })

        # ── Test 1: Verify cancel order endpoint is removed from OpenAPI/Swagger schema ──
        res_openapi = await ac.get("/openapi.json")
        assert res_openapi.status_code == 200
        schema = res_openapi.json()
        assert "/api/merchandise/orders/{order_id}/cancel" not in schema.get("paths", {})

        # ── Test 2: Any HTTP request to cancel order returns 404 Not Found ──
        res_cancel = await ac.post(
            f"/api/merchandise/orders/{order_id}/cancel",
            json={"reason": "Changed my mind, please refund"},
            headers=user_headers
        )
        assert res_cancel.status_code == 404

        # ── Test 3: MerchandiseService.cancel_order directly raises 400 non-refundable ──
        from app.services.merchandise_service import MerchandiseService
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await MerchandiseService.cancel_order(order_id, test_user_email, "Direct cancel attempt")
        assert exc_info.value.status_code == 400
        assert "non-refundable" in exc_info.value.detail.lower()

        # ── Test 4: Status remains unchanged (CONFIRMED & PAID) in database ──
        unchanged_order = await merchandise_order_collection.find_one({"_id": ObjectId(order_id)})
        assert unchanged_order["status"] == OrderStatus.CONFIRMED.value
        assert unchanged_order["payment_status"] == PaymentStatus.PAID.value

    finally:
        # Cleanup
        await user_collection.delete_many({"email": test_user_email})
        await merchandise_product_collection.delete_one({"_id": ObjectId(prod_id)})
        await merchandise_order_collection.delete_one({"_id": ObjectId(order_id)})
