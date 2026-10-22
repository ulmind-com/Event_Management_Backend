import pytest
import io
from bson import ObjectId
from datetime import datetime, timezone
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.core.database import merchandise_order_collection, user_collection
from app.core.security import create_access_token
from app.services.order_pdf_service import OrderPDFService


@pytest.mark.asyncio
async def test_order_invoice_pdf_generation_and_api():
    test_user_email = f"pdf_buyer_{ObjectId()}@example.com"
    other_user_email = f"other_buyer_{ObjectId()}@example.com"
    admin_email = f"admin_pdf_{ObjectId()}@example.com"

    # Create users
    await user_collection.insert_one({
        "email": test_user_email,
        "name": "Arjun Das",
        "phone_number": "9876543210",
        "registration_complete": True,
        "role": "user"
    })
    await user_collection.insert_one({
        "email": other_user_email,
        "name": "Sneha Roy",
        "phone_number": "9876543211",
        "registration_complete": True,
        "role": "user"
    })
    from app.core.database import admin_collection

    await admin_collection.insert_one({
        "email": admin_email,
        "name": "Super Admin",
        "role": "super_admin"
    })

    user_token = create_access_token({"sub": test_user_email, "role": "user"})
    other_token = create_access_token({"sub": other_user_email, "role": "user"})
    admin_token = create_access_token({"sub": admin_email, "type": "admin", "role": "super_admin"})


    user_headers = {"Authorization": f"Bearer {user_token}"}
    other_headers = {"Authorization": f"Bearer {other_token}"}
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    order_number = f"MCH-ORD-TESTPDF-{ObjectId()}"
    now_utc = datetime.now(timezone.utc)

    # Insert a confirmed merchandise order
    order_doc = {
        "order_number": order_number,
        "user_email": test_user_email,
        "user_name": "Arjun Das",
        "items": [
            {
                "product_id": str(ObjectId()),
                "product_name": "Official Premium Hoodie",
                "variant": {"color": "Charcoal Black", "size": "XL", "sku": "HD-BLK-XL"},
                "sku": "HD-BLK-XL",
                "quantity": 2,
                "unit_price": 1499.0,
                "total_price": 2998.0
            },
            {
                "product_id": str(ObjectId()),
                "product_name": "Commemorative Enamel Badge",
                "variant": {"color": "Gold"},
                "sku": "BDG-GLD-01",
                "quantity": 3,
                "unit_price": 199.0,
                "total_price": 597.0
            }
        ],
        "subtotal": 3595.0,
        "shipping_fee": 0.0,
        "discount_amount": 100.0,
        "total_amount": 3495.0,
        "currency": "INR",
        "shipping_address": {
            "full_name": "Arjun Das",
            "phone": "9876543210",
            "address_line1": "12/A Park Street",
            "address_line2": "Near Allen Park",
            "city": "Kolkata",
            "state": "West Bengal",
            "postal_code": "700016",
            "country": "India"
        },
        "status": "CONFIRMED",
        "payment_status": "PAID",
        "payment_reference": "pay_TEST_123456789",
        "courier_name": "Blue Dart Express",
        "tracking_number": "BLR123456789IN",
        "created_at": now_utc,
        "paid_at": now_utc
    }

    insert_res = await merchandise_order_collection.insert_one(order_doc)
    order_id = str(insert_res.inserted_id)

    try:
        # ── Test 1: Direct Service PDF Generation ──
        pdf_bytes = OrderPDFService.generate_order_invoice_pdf(order_doc)
        assert isinstance(pdf_bytes, bytes)
        assert len(pdf_bytes) > 2000
        assert pdf_bytes.startswith(b"%PDF-1.4")

        # ── Test 2: Fallback Robustness with empty items ──
        empty_order = {
            "order_number": "MCH-ORD-EMPTY-001",
            "user_email": "test@example.com",
            "items": [],
            "total_amount": 0.0
        }
        fallback_pdf = OrderPDFService.generate_order_invoice_pdf(empty_order)
        assert isinstance(fallback_pdf, bytes)
        assert len(fallback_pdf) > 1000
        assert fallback_pdf.startswith(b"%PDF-1.4")

        # ── Test 3: User Download Endpoint by order_id ──
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.get(f"/api/merchandise/orders/{order_id}/invoice", headers=user_headers)
            assert res.status_code == 200
            assert res.headers["content-type"] == "application/pdf"
            assert f"Order_Invoice_{order_number}.pdf" in res.headers["content-disposition"]
            assert res.content.startswith(b"%PDF-1.4")

            # Test alias /pdf
            res_pdf = await ac.get(f"/api/merchandise/orders/{order_id}/pdf", headers=user_headers)
            assert res_pdf.status_code == 200
            assert res_pdf.content.startswith(b"%PDF-1.4")

            # Test alias /receipt
            res_rec = await ac.get(f"/api/merchandise/orders/{order_id}/receipt", headers=user_headers)
            assert res_rec.status_code == 200
            assert res_rec.content.startswith(b"%PDF-1.4")

            # Test lookup by order_number string
            res_num = await ac.get(f"/api/merchandise/orders/{order_number}/invoice", headers=user_headers)
            assert res_num.status_code == 200
            assert res_num.content.startswith(b"%PDF-1.4")

            # ── Test 4: Super Admin Download Endpoint ──
            admin_res = await ac.get(f"/api/admin/merchandise/orders/{order_id}/invoice", headers=admin_headers)
            assert admin_res.status_code == 200
            assert admin_res.content.startswith(b"%PDF-1.4")

            # ── Test 5: Authorization Security Check ──
            other_res = await ac.get(f"/api/merchandise/orders/{order_id}/invoice", headers=other_headers)
            assert other_res.status_code == 404

    finally:
        # Cleanup
        await merchandise_order_collection.delete_many({"_id": ObjectId(order_id)})
        await user_collection.delete_many({"email": {"$in": [test_user_email, other_user_email]}})
        await admin_collection.delete_many({"email": admin_email})

