import pytest
import asyncio
from datetime import datetime, timezone, timedelta
from bson import ObjectId
from decimal import Decimal

from app.core.config import settings
from app.core.database import (
    user_collection,
    referral_collection,
    referral_program_config_collection,
    user_wallet_collection,
    coin_ledger_collection,
    coin_reward_lot_collection,
    coin_reservation_collection,
    course_collection
)
from app.core.security import create_access_token
from app.services.referral_service import ReferralService
from app.services.wallet_service import WalletService
from app.services.expiry_worker import ExpiryWorker
from httpx import AsyncClient, ASGITransport
from app.main import app

pytestmark = pytest.mark.asyncio

@pytest.fixture(autouse=True)
async def clean_test_collections():
    test_emails = [
        "referrer_a@test.com", "referred_b@test.com",
        "user_c@test.com", "user_d@test.com", "user_e@test.com"
    ]
    await user_collection.delete_many({"email": {"$in": test_emails}})
    await referral_collection.delete_many({"$or": [{"referrer_email": {"$in": test_emails}}, {"referred_email": {"$in": test_emails}}]})
    await user_wallet_collection.delete_many({"email": {"$in": test_emails}})
    await coin_ledger_collection.delete_many({"email": {"$in": test_emails}})
    await coin_reward_lot_collection.delete_many({"email": {"$in": test_emails}})
    await coin_reservation_collection.delete_many({"email": {"$in": test_emails}})
    await course_collection.delete_many({"title": {"$regex": "^TEST_"}})
    yield
    await user_collection.delete_many({"email": {"$in": test_emails}})
    await referral_collection.delete_many({"$or": [{"referrer_email": {"$in": test_emails}}, {"referred_email": {"$in": test_emails}}]})
    await user_wallet_collection.delete_many({"email": {"$in": test_emails}})
    await coin_ledger_collection.delete_many({"email": {"$in": test_emails}})
    await coin_reward_lot_collection.delete_many({"email": {"$in": test_emails}})
    await coin_reservation_collection.delete_many({"email": {"$in": test_emails}})
    await course_collection.delete_many({"title": {"$regex": "^TEST_"}})

# --- 1. REFERRAL CODE & LINK TESTS ---

async def test_unique_referral_code_generation():
    email = "referrer_a@test.com"
    await user_collection.insert_one({
        "email": email,
        "name": "User A",
        "registration_complete": True,
        "email_verified": True
    })

    code1 = await ReferralService.get_or_create_user_referral_code(email)
    assert code1.startswith("EVNT")
    assert len(code1) >= 8

    # Repeated calls return same code
    code2 = await ReferralService.get_or_create_user_referral_code(email)
    assert code1 == code2

    profile = await ReferralService.get_user_referral_profile(email)
    assert profile["referral_code"] == code1
    assert f"ref={code1}" in profile["website_referral_link"]
    assert f"ref={code1}" in profile["app_referral_link"]
    assert f"ref={code1}" in profile["referral_link"]

# --- 2. ANTI-FRAUD: CLICKS/VISITS DO NOT AWARD COINS ---

async def test_link_click_alone_does_not_award_coins():
    referrer_email = "referrer_a@test.com"
    await user_collection.insert_one({
        "email": referrer_email,
        "registration_complete": True,
        "email_verified": True
    })
    code = await ReferralService.get_or_create_user_referral_code(referrer_email)

    new_user_email = "referred_b@test.com"
    attr_result = await ReferralService.attribute_referral(new_user_email, code, attribution_source="web_link")
    assert attr_result["status"] == "ATTRIBUTION_CAPTURED"

    ref_wallet = await WalletService.get_wallet_summary(referrer_email)
    new_wallet = await WalletService.get_wallet_summary(new_user_email)
    assert ref_wallet["wallet"]["available_coins"] == 0
    assert new_wallet["wallet"]["available_coins"] == 0

# --- 3. ANTI-FRAUD: SELF-REFERRAL & DUPLICATE REFERRALS REJECTED ---

async def test_self_referral_rejected():
    email = "referrer_a@test.com"
    await user_collection.insert_one({
        "email": email,
        "registration_complete": True,
        "email_verified": True
    })
    code = await ReferralService.get_or_create_user_referral_code(email)

    res = await ReferralService.attribute_referral(email, code)
    assert res["status"] == "REJECTED"
    assert "Self-referral" in res["reason"]

async def test_duplicate_referrer_for_same_user_rejected():
    await user_collection.insert_many([
        {"email": "referrer_a@test.com", "registration_complete": True, "email_verified": True},
        {"email": "user_c@test.com", "registration_complete": True, "email_verified": True}
    ])
    code_a = await ReferralService.get_or_create_user_referral_code("referrer_a@test.com")
    code_c = await ReferralService.get_or_create_user_referral_code("user_c@test.com")

    res1 = await ReferralService.attribute_referral("referred_b@test.com", code_a)
    assert res1["status"] == "ATTRIBUTION_CAPTURED"

    res2 = await ReferralService.attribute_referral("referred_b@test.com", code_c)
    assert res2["status"] == "ATTRIBUTION_CAPTURED"
    db_ref = await referral_collection.find_one({"referred_email": "referred_b@test.com"})
    assert db_ref["referrer_email"] == "referrer_a@test.com"

# --- 4. REFERRAL QUALIFICATION & REWARD ISSUANCE ---

async def test_valid_referral_qualification_and_rewards():
    await ReferralService.update_config({
        "program_enabled": True,
        "referrer_reward": {"enabled": True, "coins": 100},
        "welcome_reward": {"enabled": True, "coins": 50}
    }, admin_email="admin@test.com")

    await user_collection.insert_one({"email": "referrer_a@test.com", "registration_complete": True, "email_verified": True})
    code = await ReferralService.get_or_create_user_referral_code("referrer_a@test.com")

    await ReferralService.attribute_referral("referred_b@test.com", code)
    await user_collection.insert_one({
        "email": "referred_b@test.com",
        "registration_complete": True,
        "email_verified": True
    })

    qual_res = await ReferralService.qualify_and_reward_referral("referred_b@test.com")
    assert qual_res["qualified"] is True
    assert qual_res["status"] == "REWARDED"
    assert qual_res["referrer_coins_awarded"] == 100
    assert qual_res["welcome_coins_awarded"] == 50

    w_a = await WalletService.get_wallet_summary("referrer_a@test.com")
    assert w_a["wallet"]["available_coins"] == 100
    assert w_a["wallet"]["total_earned_coins"] == 100
    assert w_a["referral_rewards"]["referrer_rewards_earned"] == 100

    w_b = await WalletService.get_wallet_summary("referred_b@test.com")
    assert w_b["wallet"]["available_coins"] == 50
    assert w_b["wallet"]["total_earned_coins"] == 50
    assert w_b["referral_rewards"]["welcome_rewards_received"] == 50

    # Idempotency check: Repeated qualification does not double reward
    repeat_res = await ReferralService.qualify_and_reward_referral("referred_b@test.com")
    assert repeat_res["already_processed"] is True

    w_a_repeat = await WalletService.get_wallet_summary("referrer_a@test.com")
    assert w_a_repeat["wallet"]["available_coins"] == 100

# --- 5. INDEPENDENT REWARD CONFIGURATION (ALL 4 COMBINATIONS) ---

async def test_independent_rewards_referrer_only():
    await ReferralService.update_config({
        "program_enabled": True,
        "referrer_reward": {"enabled": True, "coins": 80},
        "welcome_reward": {"enabled": False, "coins": 40}
    }, admin_email="admin@test.com")

    await user_collection.insert_one({"email": "referrer_a@test.com", "registration_complete": True, "email_verified": True})
    code = await ReferralService.get_or_create_user_referral_code("referrer_a@test.com")
    await ReferralService.attribute_referral("referred_b@test.com", code)
    await user_collection.insert_one({"email": "referred_b@test.com", "registration_complete": True, "email_verified": True})

    res = await ReferralService.qualify_and_reward_referral("referred_b@test.com")
    assert res["referrer_coins_awarded"] == 80
    assert res["welcome_coins_awarded"] == 0

    w_a = await WalletService.get_wallet_summary("referrer_a@test.com")
    w_b = await WalletService.get_wallet_summary("referred_b@test.com")
    assert w_a["wallet"]["available_coins"] == 80
    assert w_b["wallet"]["available_coins"] == 0

async def test_independent_rewards_welcome_only():
    await ReferralService.update_config({
        "program_enabled": True,
        "referrer_reward": {"enabled": False, "coins": 100},
        "welcome_reward": {"enabled": True, "coins": 60}
    }, admin_email="admin@test.com")

    await user_collection.insert_one({"email": "referrer_a@test.com", "registration_complete": True, "email_verified": True})
    code = await ReferralService.get_or_create_user_referral_code("referrer_a@test.com")
    await ReferralService.attribute_referral("referred_b@test.com", code)
    await user_collection.insert_one({"email": "referred_b@test.com", "registration_complete": True, "email_verified": True})

    res = await ReferralService.qualify_and_reward_referral("referred_b@test.com")
    assert res["referrer_coins_awarded"] == 0
    assert res["welcome_coins_awarded"] == 60

    w_a = await WalletService.get_wallet_summary("referrer_a@test.com")
    w_b = await WalletService.get_wallet_summary("referred_b@test.com")
    assert w_a["wallet"]["available_coins"] == 0
    assert w_b["wallet"]["available_coins"] == 60

async def test_independent_rewards_both_disabled():
    await ReferralService.update_config({
        "program_enabled": True,
        "referrer_reward": {"enabled": False, "coins": 100},
        "welcome_reward": {"enabled": False, "coins": 50}
    }, admin_email="admin@test.com")

    await user_collection.insert_one({"email": "referrer_a@test.com", "registration_complete": True, "email_verified": True})
    code = await ReferralService.get_or_create_user_referral_code("referrer_a@test.com")
    await ReferralService.attribute_referral("referred_b@test.com", code)
    await user_collection.insert_one({"email": "referred_b@test.com", "registration_complete": True, "email_verified": True})

    res = await ReferralService.qualify_and_reward_referral("referred_b@test.com")
    assert res["referrer_coins_awarded"] == 0
    assert res["welcome_coins_awarded"] == 0

    w_a = await WalletService.get_wallet_summary("referrer_a@test.com")
    w_b = await WalletService.get_wallet_summary("referred_b@test.com")
    assert w_a["wallet"]["available_coins"] == 0
    assert w_b["wallet"]["available_coins"] == 0

# --- 6. DYNAMIC COIN VALUATION (DECIMAL ARITHMETIC) ---

async def test_dynamic_coin_valuation():
    await ReferralService.update_config({
        "coin_valuation": {"currency": "INR", "value_per_coin": 0.75}
    }, admin_email="admin@test.com")

    await WalletService.credit_reward_lot(
        user_email="referrer_a@test.com",
        coin_amount=200,
        source_type="ADMIN_ADJUSTMENT",
        reference_type="admin",
        reference_id="test_admin",
        description="Testing valuation"
    )

    summary = await WalletService.get_wallet_summary("referrer_a@test.com")
    assert summary["wallet"]["available_coins"] == 200
    assert summary["wallet"]["coin_value"] == "0.75"
    assert summary["wallet"]["estimated_discount_value"] == "150.00"

# --- 7. AUTOMATIC COIN EXPIRY (IDEMPOTENT WORKER) ---

async def test_automatic_coin_expiry_worker():
    now = datetime.now(timezone.utc)
    expired_date = now - timedelta(days=1)

    lot_id = ObjectId()
    await coin_reward_lot_collection.insert_one({
        "_id": lot_id,
        "email": "referrer_a@test.com",
        "source_type": "REFERRER_REFERRAL_REWARD",
        "initial_coins": 50,
        "remaining_coins": 50,
        "credited_at": now - timedelta(days=91),
        "expires_at": expired_date,
        "is_expired": False,
        "status": "ACTIVE"
    })
    await user_wallet_collection.update_one(
        {"email": "referrer_a@test.com"},
        {"$set": {"available_coins": 50, "total_earned_coins": 50}},
        upsert=True
    )

    cycle_res = await WalletService.process_expired_coins()
    assert cycle_res["lots_expired_count"] >= 1
    assert cycle_res["total_coins_expired"] >= 50

    w = await WalletService.get_wallet_summary("referrer_a@test.com")
    assert w["wallet"]["available_coins"] == 0
    assert w["wallet"]["total_expired_coins"] == 50

    ledger_entry = await coin_ledger_collection.find_one({
        "email": "referrer_a@test.com",
        "transaction_type": "COIN_EXPIRY"
    })
    assert ledger_entry is not None
    assert ledger_entry["coin_amount"] == 50

    repeat_cycle = await WalletService.process_expired_coins()
    assert repeat_cycle["total_coins_expired"] == 0

# --- 8. COURSE & WORKSHOP DISCOUNT REDEMPTION ---

async def test_course_discount_calculation_and_reservation():
    await ReferralService.update_config({
        "coin_valuation": {"currency": "INR", "value_per_coin": 0.50}
    }, admin_email="admin@test.com")
    course_id = ObjectId()
    await course_collection.insert_one({
        "_id": course_id,
        "title": "TEST_Python Masterclass",
        "course_type": "course",
        "price": 1000.0,
        "is_published": True,
        "available_seats": 50
    })

    await user_wallet_collection.update_one(
        {"email": "referrer_a@test.com"},
        {"$set": {"available_coins": 200, "reserved_coins": 0, "total_earned_coins": 200}},
        upsert=True
    )

    quote = await WalletService.calculate_discount_quote(
        user_email="referrer_a@test.com",
        item_id=str(course_id),
        item_type="course",
        coins_to_use=200
    )
    assert quote["is_eligible"] is True
    assert quote["coins_applied"] == 200
    assert quote["discount_amount"] == 100.0
    assert quote["final_payable_amount"] == 900.0

    order_ref = "ORD_TEST_12345"
    res_result = await WalletService.reserve_coins_for_order(
        user_email="referrer_a@test.com",
        item_id=str(course_id),
        item_type="course",
        coins_to_reserve=200,
        order_reference=order_ref
    )
    assert res_result["reserved"] is True

    w = await WalletService.get_wallet_summary("referrer_a@test.com")
    assert w["wallet"]["available_coins"] == 0
    assert w["wallet"]["reserved_coins"] == 200

    commit_res = await WalletService.commit_reserved_coins(order_ref)
    assert commit_res["committed"] is True

    w_committed = await WalletService.get_wallet_summary("referrer_a@test.com")
    assert w_committed["wallet"]["reserved_coins"] == 0
    assert w_committed["wallet"]["total_redeemed_coins"] == 200

# --- 9. FAILED ORDER RELEASES RESERVED COINS ---

async def test_order_cancellation_releases_reserved_coins():
    course_id = ObjectId()
    await course_collection.insert_one({
        "_id": course_id,
        "title": "TEST_Workshop Art",
        "course_type": "workshop",
        "price": 500.0,
        "is_published": True
    })

    await user_wallet_collection.update_one(
        {"email": "referrer_a@test.com"},
        {"$set": {"available_coins": 100, "reserved_coins": 0}},
        upsert=True
    )

    order_ref = "ORD_FAILED_999"
    await WalletService.reserve_coins_for_order(
        user_email="referrer_a@test.com",
        item_id=str(course_id),
        item_type="workshop",
        coins_to_reserve=100,
        order_reference=order_ref
    )

    rel_res = await WalletService.release_reserved_coins(order_ref)
    assert rel_res["released"] is True

    w = await WalletService.get_wallet_summary("referrer_a@test.com")
    assert w["wallet"]["available_coins"] == 100
    assert w["wallet"]["reserved_coins"] == 0

# --- 10. CASH WITHDRAWAL RESTRICTION ---

async def test_cash_withdrawal_prohibited():
    w = await WalletService.get_wallet_summary("referrer_a@test.com")
    assert w["cash_withdrawal"]["enabled"] is False
    assert "strictly prohibited" in w["cash_withdrawal"]["message"]

# --- 11. FASTAPI HTTP ENDPOINT TESTS (ADMIN & USER ROUTERS) ---

async def test_admin_config_endpoints():
    admin_token = create_access_token({"sub": "admin@eventora.com", "role": "super_admin", "type": "admin"})
    headers = {"Authorization": f"Bearer {admin_token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.get("/api/admin/referral/config", headers=headers)
        assert res.status_code == 200
        cfg = res.json()
        assert "referrer_reward" in cfg
        assert "welcome_reward" in cfg

        update_payload = {
            "program_enabled": True,
            "referrer_reward": {"enabled": True, "coins": 120, "activation_delay_hours": 0, "require_admin_approval": False},
            "welcome_reward": {"enabled": True, "coins": 60, "activation_delay_hours": 0, "require_admin_approval": False},
            "coin_valuation": {"currency": "INR", "value_per_coin": 0.50},
            "coin_expiry": {"enabled": True, "duration_value": 90, "duration_unit": "DAYS", "warning_days": 7, "notify_user": True},
            "redemption_rules": {
                "course_redemption_enabled": True,
                "workshop_redemption_enabled": True,
                "min_coins_required": 10,
                "max_coins_per_order": 500,
                "min_purchase_amount": 100.0,
                "max_discount_percentage": 50.0,
                "max_discount_amount": 1000.0,
                "allow_with_coupons": False,
                "eligible_course_ids": [],
                "eligible_workshop_ids": [],
                "excluded_item_ids": []
            }
        }
        put_res = await ac.put("/api/admin/referral/config", json=update_payload, headers=headers)
        assert put_res.status_code == 200
        assert put_res.json()["config"]["referrer_reward"]["coins"] == 120

        an_res = await ac.get("/api/admin/referral/analytics", headers=headers)
        assert an_res.status_code == 200
        assert "attributions" in an_res.json()
        assert "rewards_issued" in an_res.json()

async def test_unauthorized_admin_rejected():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.get("/api/admin/referral/config")
        assert res.status_code in [401, 403]
