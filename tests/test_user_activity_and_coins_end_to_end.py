import pytest
from datetime import datetime, timezone
from bson import ObjectId
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.core.database import (
    user_collection,
    admin_collection,
    course_collection,
    registration_collection,
    merchandise_order_collection,
    user_wallet_collection,
    coin_ledger_collection,
)
from app.core.security import create_access_token

pytestmark = pytest.mark.asyncio

async def setup_test_data():
    user_email = "alice_audit_test@eventora.test"
    admin_email = "admin_audit_test@eventora.test"

    # Clean up
    await user_collection.delete_many({"email": user_email})
    await admin_collection.delete_many({"email": admin_email})
    await registration_collection.delete_many({"email": user_email})
    await merchandise_order_collection.delete_many({"$or": [{"user_email": user_email}, {"email": user_email}]})
    await user_wallet_collection.delete_many({"email": user_email})
    await coin_ledger_collection.delete_many({"email": user_email})

    # Create User
    user_res = await user_collection.insert_one({
        "name": "Alice Audit",
        "email": user_email,
        "phone": "+919876500001",
        "role": "USER",
        "status": "ACTIVE",
        "registration_complete": True,
        "created_at": datetime.now(timezone.utc)
    })
    user_id = str(user_res.inserted_id)

    # Create Admin
    await admin_collection.insert_one({
        "email": admin_email,
        "role": "SUPER_ADMIN",
        "created_at": datetime.now(timezone.utc)
    })

    user_token = create_access_token({"sub": user_email, "type": "user"})
    admin_token = create_access_token({"sub": admin_email, "type": "admin"})

    # Create Course
    course_res = await course_collection.insert_one({
        "title": "Full Stack Mastery 2026",
        "course_type": "course",
        "category": "TECH",
        "price": 1500.0,
        "is_published": True,
        "created_at": datetime.now(timezone.utc)
    })
    course_id = str(course_res.inserted_id)

    # User registers/buys course
    now = datetime.now(timezone.utc)
    await registration_collection.insert_one({
        "email": user_email,
        "participant_name": "Alice Audit",
        "participant_email": user_email,
        "activity_id": course_id,
        "activity_type": "course",
        "original_price": 1500.0,
        "price_snapshot": 1500.0,
        "coins_applied": 100,
        "discount_amount": 500.0,
        "payable_amount": 1000.0,
        "order_reference": "ORD_COURSE_TEST_101",
        "status": "CONFIRMED",
        "created_at": now
    })

    # User buys merchandise
    await merchandise_order_collection.insert_one({
        "order_number": "MERCH-AUDIT-2026-001",
        "user_email": user_email,
        "user_name": "Alice Audit",
        "items": [
            {
                "product_id": str(ObjectId()),
                "title": "Eventora Pro Artist Hoodie",
                "variant_name": "Black / L",
                "quantity": 1,
                "unit_price": 1499.0,
                "total_price": 1499.0
            }
        ],
        "subtotal": 1499.0,
        "shipping_fee": 0.0,
        "discount_amount": 0.0,
        "total_amount": 1499.0,
        "status": "CONFIRMED",
        "payment_status": "PAID",
        "created_at": now
    })

    # Seed User Wallet
    await user_wallet_collection.insert_one({
        "email": user_email,
        "available_coins": 150,
        "pending_coins": 0,
        "reserved_coins": 0,
        "total_earned_coins": 250,
        "total_redeemed_coins": 100,
        "created_at": now,
        "updated_at": now
    })

    # Seed Coin Ledger: 1 Earned (Credit), 1 Spent (Debit)
    await coin_ledger_collection.insert_one({
        "transaction_id": "TXN_EARN_001",
        "email": user_email,
        "transaction_type": "REFERRER_REFERRAL_REWARD",
        "coin_amount": 250,
        "direction": "CREDIT",
        "status": "COMPLETED",
        "description": "Earned 250 coins for referring 5 friends",
        "created_at": now
    })

    await coin_ledger_collection.insert_one({
        "transaction_id": "TXN_SPEND_001",
        "email": user_email,
        "transaction_type": "COURSE_DISCOUNT",
        "coin_amount": 100,
        "direction": "DEBIT",
        "status": "COMPLETED",
        "reference_id": "ORD_COURSE_TEST_101",
        "discount_amount": 500.0,
        "description": "Redeemed 100 coins for course discount on order ORD_COURSE_TEST_101",
        "created_at": now
    })

    return {
        "user_id": user_id,
        "user_email": user_email,
        "user_token": user_token,
        "admin_token": admin_token,
        "course_id": course_id
    }


async def test_admin_user_management_courses_merchandise_and_wallet():
    data = await setup_test_data()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        admin_headers = {"Authorization": f"Bearer {data['admin_token']}"}

        # 1. Admin checks which course(s) the user bought
        courses_res = await ac.get(f"/api/admin/users/{data['user_id']}/courses", headers=admin_headers)
        assert courses_res.status_code == 200, courses_res.text
        courses_data = courses_res.json()
        assert courses_data["total"] == 1
        course_item = courses_data["courses"][0]
        assert course_item["title"] == "Full Stack Mastery 2026"
        assert course_item["payable_amount"] == 1000.0
        assert course_item["coins_applied"] == 100
        assert course_item["discount_amount"] == 500.0
        assert course_item["status"] == "CONFIRMED"
        assert course_item["completion_status"] == "COMPLETED"

        # 2. Admin checks which merchandise the user bought
        merch_res = await ac.get(f"/api/admin/users/{data['user_id']}/merchandise-orders", headers=admin_headers)
        assert merch_res.status_code == 200, merch_res.text
        merch_data = merch_res.json()
        assert merch_data["total"] == 1
        order_item = merch_data["orders"][0]
        assert order_item["order_number"] == "MERCH-AUDIT-2026-001"
        assert order_item["total_amount"] == 1499.0
        assert order_item["status"] == "CONFIRMED"
        assert order_item["payment_status"] == "PAID"
        assert order_item["items"][0]["title"] == "Eventora Pro Artist Hoodie"

        # Also verify alias endpoint /api/admin/users/{user_id}/merchandise
        merch_alias_res = await ac.get(f"/api/admin/users/{data['user_id']}/merchandise", headers=admin_headers)
        assert merch_alias_res.status_code == 200
        assert merch_alias_res.json()["total"] == 1

        # 3. Admin checks how many coins user has and coin transaction history (earned vs spent)
        wallet_res = await ac.get(f"/api/admin/users/{data['user_id']}/wallet", headers=admin_headers)
        assert wallet_res.status_code == 200, wallet_res.text
        wallet_data = wallet_res.json()
        assert wallet_data["wallet"]["available_coins"] == 150
        assert wallet_data["wallet"]["total_earned_coins"] == 250
        assert wallet_data["wallet"]["total_redeemed_coins"] == 100
        assert wallet_data["summary"]["total_transactions"] == 2
        assert wallet_data["summary"]["total_credited"] == 250
        assert wallet_data["summary"]["total_debited"] == 100

        txns = wallet_data["transactions"]
        credit_txn = next(t for t in txns if t["direction"] == "CREDIT")
        debit_txn = next(t for t in txns if t["direction"] == "DEBIT")
        assert credit_txn["coin_amount"] == 250
        assert debit_txn["coin_amount"] == 100
        assert "course discount" in debit_txn["description"].lower()

        # 4. Admin checks 360 profile summary
        summary_res = await ac.get(f"/api/admin/users/{data['user_id']}/profile-summary", headers=admin_headers)
        assert summary_res.status_code == 200, summary_res.text
        summary_data = summary_res.json()
        assert summary_data["activity_summary"]["total_courses_registered"] == 1
        assert summary_data["activity_summary"]["total_merchandise_orders"] == 1
        assert summary_data["activity_summary"]["total_merchandise_confirmed"] == 1
        assert summary_data["activity_summary"]["total_merchandise_spent"] == 1499.0
        assert summary_data["wallet"]["available_coins"] == 150
        assert summary_data["wallet"]["total_earned_coins"] == 250
        assert summary_data["wallet"]["total_redeemed_coins"] == 100


async def test_user_profile_and_rewards_history_end_to_end():
    data = await setup_test_data()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        user_headers = {"Authorization": f"Bearer {data['user_token']}"}

        # 1. User views dashboard profile
        dash_res = await ac.get("/api/profile/dashboard", headers=user_headers)
        assert dash_res.status_code == 200, dash_res.text
        dash_data = dash_res.json()

        # My courses
        assert len(dash_data["my_courses"]) == 1
        assert dash_data["my_courses"][0]["title"] == "Full Stack Mastery 2026"

        # My merchandise orders
        assert len(dash_data["my_merchandise_orders"]) == 1
        assert dash_data["my_merchandise_orders"][0]["order_number"] == "MERCH-AUDIT-2026-001"

        # My rewards and coins
        coins_info = dash_data["my_rewards_and_coins"]
        assert coins_info["total_coins"] == 150
        assert coins_info["available_coins"] == 150
        assert coins_info["total_earned_coins"] == 250
        assert coins_info["total_redeemed_coins"] == 100
        assert len(coins_info["coin_history"]) == 2

        # 2. User queries /api/rewards/coins
        coins_res = await ac.get("/api/rewards/coins", headers=user_headers)
        assert coins_res.status_code == 200, coins_res.text
        coins_data = coins_res.json()
        assert coins_data["total_coins"] == 150
        assert coins_data["total_earned_coins"] == 250
        assert coins_data["total_redeemed_coins"] == 100
        assert len(coins_data["history"]) == 2

        credit_item = next(h for h in coins_data["history"] if h.get("direction") == "CREDIT")
        debit_item = next(h for h in coins_data["history"] if h.get("direction") == "DEBIT")
        assert credit_item["coin_amount"] == 250
        assert debit_item["coin_amount"] == 100
        assert debit_item["reference_id"] == "ORD_COURSE_TEST_101"
