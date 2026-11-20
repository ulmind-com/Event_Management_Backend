"""
test_competition_category_pricing.py
====================================
End-to-End Production Tests for Competition Category-Specific Pricing:

1. Admin creates competition with custom category pricing:
   - Junior: price = 150.0
   - Senior: price = 350.0
   - Free Open: price = 0.0 (explicit free tier)
   - General: no price provided (inherits competition default fee: 500.0)
2. Admin fetches competition details and verifies all categories return custom prices under price, entry_fee, fee, and participation_fee aliases.
3. Public / User fetches competition details and verifies category prices.
4. Admin updates competition categories with modified prices (e.g. Junior becomes 200.0, adds Master at 1200.0).
5. User registers for Junior category (CASH payment) -> fee charged is 200.0.
6. User registers for Free Open category -> fee is 0.0, status CONFIRMED, payment NOT_REQUIRED.
7. User registers for Master category via generic /api/registrations -> fee charged is 1200.0.
8. User registers for General category (fallback) -> fee charged is 500.0.
9. Cleanup test data.
"""

import pytest
from datetime import datetime, timezone, timedelta
from httpx import AsyncClient, ASGITransport
from bson import ObjectId

from app.main import app
from app.core.security import create_access_token, get_password_hash
from app.core.database import (
    competition_collection,
    competition_registration_collection,
    registration_collection,
    admin_collection,
    user_collection,
)


@pytest.mark.asyncio
async def test_competition_category_specific_pricing_flow():
    ts = int(datetime.now().timestamp())
    now = datetime.now(timezone.utc)

    admin_email = f"admin_cat_price_{ts}@test.com"
    user_junior_email = f"user_junior_{ts}@test.com"
    user_free_email = f"user_free_{ts}@test.com"
    user_master_email = f"user_master_{ts}@test.com"
    user_general_email = f"user_general_{ts}@test.com"

    comp_id = None

    try:
        # 1. Setup Admin
        await admin_collection.insert_one({
            "email": admin_email,
            "password_hash": get_password_hash("adminpass123"),
            "role": "super_admin",
            "created_at": now,
        })
        admin_token = create_access_token({"sub": admin_email, "type": "admin", "role": "super_admin"})
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        # 2. Setup Users with completed profiles
        for u_email, u_name, u_dob, u_age in [
            (user_junior_email, "Junior Contestant", "2014-05-15", 12),
            (user_free_email, "Open Contestant", "2000-01-01", 26),
            (user_master_email, "Master Contestant", "1995-03-20", 31),
            (user_general_email, "General Contestant", "1998-07-10", 28),
        ]:
            await user_collection.insert_one({
                "email": u_email,
                "name": u_name,
                "phone_number": "9876543210",
                "phone": "+919876543210",
                "parent_phone_number": "9876543211",
                "parent_phone": "+919876543211",
                "emergency_contact": "9876543211",
                "date_of_birth": u_dob,
                "dob": u_dob,
                "age": u_age,
                "address": "Test Address",
                "role": "USER",
                "registration_complete": True,
                "is_profile_completed": True,
                "profile_completed": True,
                "created_at": now.isoformat(),
            })

        user_junior_token = create_access_token({"sub": user_junior_email, "email": user_junior_email, "role": "USER"})
        user_free_token = create_access_token({"sub": user_free_email, "email": user_free_email, "role": "USER"})
        user_master_token = create_access_token({"sub": user_master_email, "email": user_master_email, "role": "USER"})
        user_general_token = create_access_token({"sub": user_general_email, "email": user_general_email, "role": "USER"})

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:

            # ── Step 1: Admin Creates Competition with Category-Specific Pricing ──
            create_payload = {
                "title": f"Singing Championship {ts}",
                "description": "Grand competition with category specific prices",
                "participation_fee": 500.0,  # Competition-level fallback fee
                "categories": [
                    {
                        "name": "Junior",
                        "min_age": 6,
                        "max_age": 14,
                        "price": 150.0,
                        "description": "Junior tier entry fee 150"
                    },
                    {
                        "name": "Senior",
                        "min_age": 15,
                        "max_age": 30,
                        "entry_fee": 350.0,  # Using entry_fee alias
                        "description": "Senior tier entry fee 350"
                    },
                    {
                        "name": "Free Open",
                        "price": 0.0,  # Explicit free category
                        "description": "Free open category for all"
                    },
                    {
                        "name": "General",
                        # No price provided -> should inherit competition default 500.0
                        "description": "General tier inheriting competition fee"
                    }
                ],
                "start_date": (now - timedelta(days=1)).isoformat(),
                "end_date": (now + timedelta(days=30)).isoformat(),
                "registration_start_date": (now - timedelta(days=1)).isoformat(),
                "submission_deadline": (now + timedelta(days=25)).isoformat(),
                "is_published": True,
                "is_registration_open": True,
                "registration_status": "OPEN",
                "judge_scoring_enabled": True,
                "audience_voting_enabled": False,
            }

            res = await ac.post("/api/admin/advanced-competitions/", json=create_payload, headers=admin_headers)
            assert res.status_code == 200, f"Create competition failed: {res.text}"
            comp_data = res.json()
            comp_id = comp_data["_id"]

            # Verify categories in MongoDB
            stored = await competition_collection.find_one({"_id": ObjectId(comp_id)})
            assert stored is not None
            stored_cats = {c["name"]: c for c in stored.get("categories", [])}

            assert "Junior" in stored_cats
            assert stored_cats["Junior"]["price"] == 150.0
            assert stored_cats["Junior"]["entry_fee"] == 150.0

            assert "Senior" in stored_cats
            assert stored_cats["Senior"]["price"] == 350.0
            assert stored_cats["Senior"]["fee"] == 350.0

            assert "Free Open" in stored_cats
            assert stored_cats["Free Open"]["price"] == 0.0

            assert "General" in stored_cats
            assert stored_cats["General"]["price"] == 500.0  # Inherited default

            # ── Step 2: Verify GET Competition APIs return category prices ──
            # Admin endpoint
            admin_get_res = await ac.get(f"/api/admin/advanced-competitions/{comp_id}", headers=admin_headers)
            assert admin_get_res.status_code == 200
            admin_cats = {c["name"]: c for c in admin_get_res.json().get("categories", [])}
            assert admin_cats["Junior"]["price"] == 150.0
            assert admin_cats["Senior"]["price"] == 350.0
            assert admin_cats["Free Open"]["price"] == 0.0
            assert admin_cats["General"]["price"] == 500.0

            # Public v1 endpoint
            pub_v1_res = await ac.get(f"/api/v1/competitions/{comp_id}")
            assert pub_v1_res.status_code == 200
            pub_cats = {c["name"]: c for c in pub_v1_res.json().get("categories", [])}
            assert pub_cats["Junior"]["price"] == 150.0
            assert pub_cats["Senior"]["entry_fee"] == 350.0

            # ── Step 3: Admin Updates Category Prices ──
            # Update Junior from 150.0 to 200.0, and add Master at 1200.0
            update_payload = {
                "categories": [
                    {
                        "name": "Junior",
                        "min_age": 6,
                        "max_age": 14,
                        "price": 200.0,
                        "description": "Updated Junior price 200"
                    },
                    {
                        "name": "Senior",
                        "min_age": 15,
                        "max_age": 30,
                        "price": 350.0
                    },
                    {
                        "name": "Free Open",
                        "price": 0.0
                    },
                    {
                        "name": "General"
                    },
                    {
                        "name": "Master",
                        "price": 1200.0,
                        "description": "Premium Master Category"
                    }
                ]
            }
            update_res = await ac.patch(f"/api/admin/advanced-competitions/{comp_id}", json=update_payload, headers=admin_headers)
            assert update_res.status_code == 200

            # Verify updated prices in DB
            stored_after_update = await competition_collection.find_one({"_id": ObjectId(comp_id)})
            cats_after = {c["name"]: c for c in stored_after_update.get("categories", [])}
            assert cats_after["Junior"]["price"] == 200.0
            assert cats_after["Master"]["price"] == 1200.0
            assert cats_after["General"]["price"] == 500.0

            # ── Step 4: User registers for Junior (price: 200.0, CASH) ──
            junior_reg_res = await ac.post(
                f"/api/v1/competitions/{comp_id}/categories/Junior/participate",
                json={
                    "category": "Junior",
                    "payment_method": "CASH",
                    "rules_accepted": True,
                    "dob": "2014-05-15"
                },
                headers={"Authorization": f"Bearer {user_junior_token}"}
            )
            assert junior_reg_res.status_code == 200, f"Junior reg failed: {junior_reg_res.text}"
            j_data = junior_reg_res.json()
            assert "200.00" in j_data.get("message", "")
            assert j_data["payment_method"] == "CASH"

            # Check registration document in DB
            reg_doc_junior = await competition_registration_collection.find_one({
                "competition_id": str(comp_id),
                "user_email": user_junior_email
            })
            assert reg_doc_junior is not None
            assert reg_doc_junior["fee"] == 200.0
            assert reg_doc_junior["price"] == 200.0
            assert reg_doc_junior["category"] == "Junior"

            # ── Step 5: User registers for Free Open category (price: 0.0) ──
            free_reg_res = await ac.post(
                f"/api/v1/competitions/{comp_id}/categories/Free%20Open/participate",
                json={
                    "category": "Free Open",
                    "payment_method": "ONLINE",
                    "rules_accepted": True,
                    "dob": "2000-01-01"
                },
                headers={"Authorization": f"Bearer {user_free_token}"}
            )
            assert free_reg_res.status_code == 200, f"Free reg failed: {free_reg_res.text}"
            f_data = free_reg_res.json()
            assert f_data["status"] == "CONFIRMED"
            assert f_data["payment_status"] == "NOT_REQUIRED"

            reg_doc_free = await competition_registration_collection.find_one({
                "competition_id": str(comp_id),
                "user_email": user_free_email
            })
            assert reg_doc_free is not None
            assert reg_doc_free["fee"] == 0.0
            assert reg_doc_free["payment_status"] == "NOT_REQUIRED"

            # ── Step 6: User registers for Master category via generic /api/registrations/ (price: 1200.0) ──
            master_reg_res = await ac.post(
                "/api/registrations/",
                json={
                    "activity_id": str(comp_id),
                    "activity_type": "competition",
                    "category": "Master"
                },
                headers={"Authorization": f"Bearer {user_master_token}"}
            )
            assert master_reg_res.status_code == 200, f"Master reg failed: {master_reg_res.text}"
            m_data = master_reg_res.json()
            assert m_data.get("payable_amount") == 1200.0 or m_data.get("payment_info", {}).get("amount") == 1200.0

            # ── Step 7: User registers for General fallback category via /participate (price: 500.0, CASH) ──
            general_reg_res = await ac.post(
                f"/api/v1/competitions/{comp_id}/categories/General/participate",
                json={
                    "category": "General",
                    "payment_method": "CASH",
                    "rules_accepted": True,
                    "dob": "1998-07-10"
                },
                headers={"Authorization": f"Bearer {user_general_token}"}
            )
            assert general_reg_res.status_code == 200, f"General reg failed: {general_reg_res.text}"
            g_data = general_reg_res.json()
            assert "500.00" in g_data.get("message", "")

            reg_doc_gen = await competition_registration_collection.find_one({
                "competition_id": str(comp_id),
                "user_email": user_general_email
            })
            assert reg_doc_gen is not None
            assert reg_doc_gen["fee"] == 500.0
            assert reg_doc_gen["category"] == "General"

    finally:
        # Teardown
        await admin_collection.delete_one({"email": admin_email})
        await user_collection.delete_many({"email": {"$in": [
            user_junior_email, user_free_email, user_master_email, user_general_email
        ]}})
        if comp_id:
            await competition_collection.delete_one({"_id": ObjectId(comp_id)})
            await competition_registration_collection.delete_many({"competition_id": str(comp_id)})
            await registration_collection.delete_many({"activity_id": str(comp_id)})
