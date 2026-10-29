import pytest
import hmac
import hashlib
from datetime import datetime, timezone
from bson import ObjectId
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.core.config import settings
from app.core.database import (
    competition_collection,
    competition_registration_collection,
    registration_collection,
    course_collection,
    user_collection,
    vip_configuration_collection,
)
from app.core.security import create_access_token


def _generate_test_signature(order_id: str, payment_id: str, secret: str | None = None) -> str:
    key = secret or settings.RAZORPAY_KEY_SECRET or "rzp_secret_dummy_test"
    msg = f"{order_id}|{payment_id}"
    return hmac.new(key.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).hexdigest()


@pytest.mark.asyncio
async def test_paid_competition_registration_payment_lifecycle():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        test_email = f"paytest_{int(datetime.now().timestamp())}@example.com"
        token = create_access_token(data={"sub": test_email, "type": "user"})
        headers = {"Authorization": f"Bearer {token}"}

        await user_collection.update_one(
            {"email": test_email},
            {"$set": {
                "email": test_email,
                "role": "USER",
                "name": "Payment Tester",
                "full_name": "Payment Tester",
                "phone_number": "9876543210",
                "address": "123 Test St",
                "date_of_birth": "2000-01-01",
                "registration_complete": True,
                "is_profile_completed": True,
                "profile_completed": True,
            }},
            upsert=True
        )

        comp_doc = {
            "title": "Pro Dance Battle",
            "name": "Pro Dance Battle",
            "category": "dance",
            "participation_fee": 499.0,
            "status": "PUBLISHED",
            "is_published": True,
            "registered_count": 0,
            "dynamic_submission_fields": [{"name": "Audition Audio", "type": "audio", "required": True}],
            "ranking_modes": {"judge": True},
            "created_at": datetime.now(timezone.utc)
        }
        res_comp = await competition_collection.insert_one(comp_doc)
        comp_id = str(res_comp.inserted_id)

        # 1. First registration attempt: paid competition -> status PENDING
        reg_resp = await client.post(
            f"/api/v1/competitions/{comp_id}/register?rules_accepted=true",
            headers=headers
        )
        assert reg_resp.status_code == 200
        reg_data = reg_resp.json()
        assert reg_data["status"] == "PENDING"
        assert "payment_info" in reg_data
        reg_ref = reg_data["reference"]

        # 2. Check DB status: registration is PENDING, NOT confirmed, count NOT incremented
        db_reg = await competition_registration_collection.find_one({"registration_reference": reg_ref})
        assert db_reg is not None
        assert db_reg["status"] == "PENDING"
        assert db_reg["payment_status"] == "PENDING"

        comp_in_db = await competition_collection.find_one({"_id": ObjectId(comp_id)})
        assert comp_in_db.get("registered_count", 0) == 0

        # 3. User cancelled payment modal and re-clicks register: should re-initiate, NOT throw 409
        retry_resp = await client.post(
            f"/api/v1/competitions/{comp_id}/register?rules_accepted=true",
            headers=headers
        )
        assert retry_resp.status_code == 200
        retry_data = retry_resp.json()
        assert retry_data["status"] == "PENDING"
        assert retry_data["reference"] == reg_ref

        # 4. Check dashboard: competition must report is_confirmed: False
        dash_resp = await client.get("/api/profile/dashboard", headers=headers)
        assert dash_resp.status_code == 200
        dash_data = dash_resp.json()
        my_comp = next((c for c in dash_data.get("my_competitions", []) if c.get("competition_id") == comp_id), None)
        assert my_comp is not None
        assert my_comp["is_confirmed"] is False
        assert my_comp["status"] == "PENDING"

        # 5. Verify payment with valid signature -> status CONFIRMED
        fake_order_id = "order_fake_123456"
        fake_payment_id = "pay_fake_987654"
        valid_signature = _generate_test_signature(fake_order_id, fake_payment_id)

        verify_resp = await client.post(
            f"/api/v1/competitions/{comp_id}/verify-payment",
            headers=headers,
            json={
                "registration_reference": reg_ref,
                "razorpay_order_id": fake_order_id,
                "razorpay_payment_id": fake_payment_id,
                "razorpay_signature": valid_signature,
            }
        )
        assert verify_resp.status_code == 200
        verify_data = verify_resp.json()
        assert verify_data["status"] == "CONFIRMED"

        # 6. DB should now be CONFIRMED and registered_count incremented to 1
        db_reg_after = await competition_registration_collection.find_one({"registration_reference": reg_ref})
        assert db_reg_after["status"] == "CONFIRMED"
        assert db_reg_after["payment_status"] == "PAID"

        comp_after = await competition_collection.find_one({"_id": ObjectId(comp_id)})
        assert comp_after.get("registered_count") == 1

        # 7. Now that it is confirmed, dashboard should report is_confirmed: True
        dash_resp2 = await client.get("/api/profile/dashboard", headers=headers)
        assert dash_resp2.status_code == 200
        dash_data2 = dash_resp2.json()
        my_comp2 = next((c for c in dash_data2.get("my_competitions", []) if c.get("competition_id") == comp_id), None)
        assert my_comp2 is not None
        assert my_comp2["is_confirmed"] is True
        assert my_comp2["status"] == "CONFIRMED"

        # 8. Now that it is confirmed, attempting to register again MUST return 409
        again_resp = await client.post(
            f"/api/v1/competitions/{comp_id}/register?rules_accepted=true",
            headers=headers
        )
        assert again_resp.status_code == 409
        assert "Already registered and confirmed" in again_resp.json()["detail"]


@pytest.mark.asyncio
async def test_paid_course_activity_registration_lifecycle():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        test_email = f"coursetest_{int(datetime.now().timestamp())}@example.com"
        token = create_access_token(data={"sub": test_email, "type": "user"})
        headers = {"Authorization": f"Bearer {token}"}

        await user_collection.update_one(
            {"email": test_email},
            {"$set": {
                "email": test_email,
                "role": "USER",
                "name": "Course Tester",
                "full_name": "Course Tester",
                "phone_number": "9876543210",
                "address": "123 Test St",
                "date_of_birth": "2000-01-01",
                "registration_complete": True,
                "is_profile_completed": True,
                "profile_completed": True,
            }},
            upsert=True
        )

        course_doc = {
            "title": "Mastering Music Production",
            "name": "Mastering Music Production",
            "course_type": "course",
            "price": 1299.0,
            "is_published": True,
            "available_seats": 20,
            "registered_count": 0,
            "created_at": datetime.now(timezone.utc)
        }
        res_course = await course_collection.insert_one(course_doc)
        course_id = str(res_course.inserted_id)

        # 1. Initial register attempt -> PENDING_PAYMENT
        reg_resp = await client.post(
            "/api/registrations/",
            headers=headers,
            json={"activity_id": course_id, "activity_type": "course"}
        )
        assert reg_resp.status_code == 200
        reg_data = reg_resp.json()
        assert reg_data["status"] == "PENDING_PAYMENT"
        order_ref = reg_data["order_reference"]

        # Count in course should still be 0
        c_check = await course_collection.find_one({"_id": ObjectId(course_id)})
        assert c_check.get("registered_count", 0) == 0

        # 2. Retrying while pending -> should re-initiate, NOT throw 400
        retry_resp = await client.post(
            "/api/registrations/",
            headers=headers,
            json={"activity_id": course_id, "activity_type": "course"}
        )
        assert retry_resp.status_code == 200
        retry_data = retry_resp.json()
        assert retry_data["status"] == "PENDING_PAYMENT"
        assert retry_data["order_reference"] == order_ref

        # 3. Verify payment with valid signature
        fake_order_id = "order_course_111"
        fake_payment_id = "pay_course_222"
        valid_signature = _generate_test_signature(fake_order_id, fake_payment_id)

        verify_resp = await client.post(
            "/api/registrations/verify-payment",
            headers=headers,
            json={
                "order_reference": order_ref,
                "razorpay_order_id": fake_order_id,
                "razorpay_payment_id": fake_payment_id,
                "razorpay_signature": valid_signature,
            }
        )
        assert verify_resp.status_code == 200
        assert verify_resp.json()["status"] == "CONFIRMED"

        # 4. Check DB status and counter
        db_reg = await registration_collection.find_one({"order_reference": order_ref})
        assert db_reg["status"] == "CONFIRMED"
        assert db_reg["payment_status"] == "PAID"

        c_after = await course_collection.find_one({"_id": ObjectId(course_id)})
        assert c_after.get("registered_count") == 1

        # 5. After confirmed, retry MUST throw 400 Already registered
        again_resp = await client.post(
            "/api/registrations/",
            headers=headers,
            json={"activity_id": course_id, "activity_type": "course"}
        )
        assert again_resp.status_code == 400
        assert "Already registered" in again_resp.json()["detail"]


@pytest.mark.asyncio
async def test_vip_pass_booking_lifecycle():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        test_email = f"viptest_{int(datetime.now().timestamp())}@example.com"
        token = create_access_token(data={"sub": test_email, "type": "user"})
        headers = {"Authorization": f"Bearer {token}"}

        await user_collection.update_one(
            {"email": test_email},
            {"$set": {
                "email": test_email,
                "role": "USER",
                "name": "VIP Tester",
                "full_name": "VIP Tester",
                "phone_number": "9876543210",
                "address": "123 Test St",
                "date_of_birth": "2000-01-01",
                "registration_complete": True,
                "is_profile_completed": True,
                "profile_completed": True,
            }},
            upsert=True
        )

        event_id = str(ObjectId())
        vip_cfg_doc = {
            "event_id": event_id,
            "name": "Backstage All-Access VIP",
            "category": "ALL_ACCESS",
            "price": 2500.0,
            "currency": "INR",
            "capacity": 5,
            "max_passes_per_user": 1,
            "reservation_expiry_minutes": 15,
            "is_enabled": True,
            "created_at": datetime.now(timezone.utc)
        }
        res_cfg = await vip_configuration_collection.insert_one(vip_cfg_doc)
        cfg_id = str(res_cfg.inserted_id)

        # 1. Initial VIP Pass Booking -> returns 201 with PENDING_PAYMENT
        book_resp = await client.post(
            f"/api/events/{event_id}/vip-passes/{cfg_id}/book",
            headers=headers,
            json={"quantity": 1, "holder_names": ["VIP Tester"]}
        )
        assert book_resp.status_code == 201
        book_data = book_resp.json()
        assert book_data["booking_status"] == "PENDING_PAYMENT"
        assert "payment_details" in book_data
        booking_ref = book_data["booking_reference"]

        # 2. User closes Razorpay and retries booking: should return existing pending booking without hitting per-user limit
        retry_book_resp = await client.post(
            f"/api/events/{event_id}/vip-passes/{cfg_id}/book",
            headers=headers,
            json={"quantity": 1, "holder_names": ["VIP Tester"]}
        )
        assert retry_book_resp.status_code == 201
        retry_book_data = retry_book_resp.json()
        assert retry_book_data["booking_status"] == "PENDING_PAYMENT"
        assert retry_book_data["booking_reference"] == booking_ref
        assert "payment_details" in retry_book_data
