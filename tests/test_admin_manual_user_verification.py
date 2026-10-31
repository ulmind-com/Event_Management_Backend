import pytest
from httpx import AsyncClient, ASGITransport
from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime, timezone
from bson import ObjectId

from app.core.config import settings
from app.core.security import create_access_token, get_password_hash
from app.core.database import (
    user_collection,
    admin_collection,
    kyc_verification_collection,
    competition_collection,
    competition_registration_collection,
    competition_submission_collection,
    judge_assignment_collection
)
from app.main import app


@pytest.mark.asyncio
async def test_admin_manual_user_verification_lifecycle():
    """
    End-to-End Test for Admin Manual User Govt ID Verification:
    1. Setup Admin and User accounts.
    2. User initially has is_verified=False, verified_badge=False.
    3. Admin checks user list and verifies user via POST /api/admin/users/{user_id}/verify.
    4. Profile check via GET /api/profile/me reflects is_verified=True, verified_tick=True, verified_badge=True.
    5. Profile update by user via PUT /api/profile/me does NOT remove verification.
    6. Admin inspects user profile summary via GET /api/admin/users/{user_id} and sees verification details.
    7. Admin deletes user. When a new account is registered with the same email, it starts unverified.
    8. Admin verifies new account and can optionally unverify via POST /api/admin/users/{user_id}/unverify.
    """
    admin_email = "admin_verify_test@eventora.com"
    user_email = "user_verify_test@eventora.com"

    # Clean up test accounts
    await admin_collection.delete_many({"email": admin_email})
    await user_collection.delete_many({"email": {"$in": [user_email, f"deleted_"]}})
    await user_collection.delete_many({"original_email": user_email})
    await kyc_verification_collection.delete_many({"user_email": user_email})

    # Setup admin account
    await admin_collection.insert_one({
        "email": admin_email,
        "password_hash": get_password_hash("adminpassword123"),
        "role": "super_admin",
        "created_at": datetime.now(timezone.utc)
    })

    # Setup initial user account with complete profile
    user_doc = {
        "email": user_email,
        "name": "Verification Test User",
        "full_name": "Verification Test User",
        "phone_number": "9876543210",
        "date_of_birth": "1995-04-12",
        "address": "789 Salt Lake, Sector V, Kolkata",
        "parent_phone_number": "9876543211",
        "role": "USER",
        "status": "ACTIVE",
        "email_verified": True,
        "registration_complete": True,
        "is_profile_completed": True,
        "profile_completed": True,
        "is_verified": False,
        "is_id_verified": False,
        "is_govt_id_verified": False,
        "verified_badge": False,
        "verified_tick": False,
        "verification_status": "NOT_VERIFIED",
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    insert_res = await user_collection.insert_one(user_doc)
    user_id = str(insert_res.inserted_id)

    admin_token = create_access_token({"sub": admin_email, "type": "admin", "role": "super_admin"})
    user_token = create_access_token({"sub": user_email, "type": "user"})

    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    user_headers = {"Authorization": f"Bearer {user_token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # ── 1. Initial State: User is NOT verified ──
        prof_res = await ac.get("/api/profile/me", headers=user_headers)
        assert prof_res.status_code == 200
        prof_data = prof_res.json()
        assert prof_data["is_verified"] is False
        assert prof_data["verified_badge"] is False
        assert prof_data["verified_tick"] is False
        assert prof_data["verification_status"] == "NOT_VERIFIED"

        # ── 2. Admin Lists Users with filter ──
        admin_users_res = await ac.get("/api/admin/users/?is_verified=false", headers=admin_headers)
        assert admin_users_res.status_code == 200
        unverified_list = admin_users_res.json()
        target_in_list = next((u for u in unverified_list if u.get("email") == user_email), None)
        assert target_in_list is not None
        assert target_in_list["is_verified"] is False

        # ── 3. Admin Manually Verifies User Govt ID ──
        verify_payload = {
            "govt_id_type": "AADHAAR",
            "govt_id_number": "XXXX-XXXX-9012",
            "notes": "Verified Government Aadhaar Card copy physically matched with name and DOB."
        }
        verify_res = await ac.post(
            f"/api/admin/users/{user_id}/verify",
            json=verify_payload,
            headers=admin_headers
        )
        assert verify_res.status_code == 200, f"Verify failed: {verify_res.text}"
        v_data = verify_res.json()
        assert v_data["is_verified"] is True
        assert v_data["is_id_verified"] is True
        assert v_data["verified_badge"] is True
        assert v_data["verified_tick"] is True
        assert v_data["verification_status"] == "VERIFIED"
        assert v_data["verification_details"]["govt_id_type"] == "AADHAAR"
        assert v_data["verification_details"]["govt_id_number"] == "XXXX-XXXX-9012"

        # ── 4. User Profile now shows Verified Badge & Tick ──
        prof_after = await ac.get("/api/profile/me", headers=user_headers)
        assert prof_after.status_code == 200
        prof_after_data = prof_after.json()
        assert prof_after_data["is_verified"] is True
        assert prof_after_data["is_id_verified"] is True
        assert prof_after_data["is_govt_id_verified"] is True
        assert prof_after_data["verified_badge"] is True
        assert prof_after_data["verified_tick"] is True
        assert prof_after_data["verification_status"] == "VERIFIED"
        assert prof_after_data.get("badge_label") == "Verified Profile"

        # Check User Dashboard also returns verified status
        dash_res = await ac.get("/api/profile/dashboard", headers=user_headers)
        assert dash_res.status_code == 200
        dash_profile = dash_res.json()["profile"]
        assert dash_profile["is_verified"] is True
        assert dash_profile["verified_badge"] is True
        assert dash_profile["verified_tick"] is True

        # ── 5. User updates personal details -> verification remains untouched (Lifetime persistence) ──
        up_res = await ac.put(
            "/api/profile/me",
            data={"name": "Verification User Updated", "address": "New Address Lane 5"},
            headers=user_headers
        )
        assert up_res.status_code == 200
        prof_updated = await ac.get("/api/profile/me", headers=user_headers)
        assert prof_updated.status_code == 200
        assert prof_updated.json()["name"] == "Verification User Updated"
        assert prof_updated.json()["is_verified"] is True
        assert prof_updated.json()["verified_badge"] is True
        assert prof_updated.json()["verified_tick"] is True

        # ── 6. Admin User 360° Profile shows Verified status ──
        admin_360 = await ac.get(f"/api/admin/users/{user_id}", headers=admin_headers)
        assert admin_360.status_code == 200
        admin_user_obj = admin_360.json()["user"]
        assert admin_user_obj["is_verified"] is True
        assert admin_user_obj["verified_badge"] is True
        assert admin_user_obj["verified_tick"] is True
        assert admin_user_obj["verification_status"] == "VERIFIED"

        # ── 7. Admin Soft Deletes User ──
        del_res = await ac.delete(f"/api/admin/users/{user_id}", headers=admin_headers)
        assert del_res.status_code == 200

        # Now when a brand NEW account registers with the same email, it starts unverified
        new_user_doc = {
            "email": user_email,
            "name": "New Account Holder",
            "phone_number": "9123456789",
            "date_of_birth": "2001-01-01",
            "address": "Brand New Residence",
            "role": "USER",
            "status": "ACTIVE",
            "email_verified": True,
            "registration_complete": True,
            "is_profile_completed": True,
            "profile_completed": True,
            "is_verified": False,
            "is_id_verified": False,
            "is_govt_id_verified": False,
            "verified_badge": False,
            "verified_tick": False,
            "verification_status": "NOT_VERIFIED",
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }
        new_insert = await user_collection.insert_one(new_user_doc)
        new_user_id = str(new_insert.inserted_id)

        new_user_token = create_access_token({"sub": user_email, "type": "user"})
        new_user_headers = {"Authorization": f"Bearer {new_user_token}"}

        # Verify that new account starts unverified
        new_prof = await ac.get("/api/profile/me", headers=new_user_headers)
        assert new_prof.status_code == 200
        assert new_prof.json()["is_verified"] is False
        assert new_prof.json()["verified_badge"] is False
        assert new_prof.json()["verified_tick"] is False

        # Admin verifies the new account
        verify_new = await ac.post(
            f"/api/admin/users/{new_user_id}/verify",
            json={"govt_id_type": "PAN", "govt_id_number": "ABCDE1234F", "notes": "Verified PAN card for new account."},
            headers=admin_headers
        )
        assert verify_new.status_code == 200
        assert verify_new.json()["is_verified"] is True
        assert verify_new.json()["verified_tick"] is True

        # Check new profile is now verified
        new_prof_verified = await ac.get("/api/profile/me", headers=new_user_headers)
        assert new_prof_verified.status_code == 200
        assert new_prof_verified.json()["is_verified"] is True
        assert new_prof_verified.json()["verified_tick"] is True

        # Admin un-verifies user
        unverify_res = await ac.post(
            f"/api/admin/users/{new_user_id}/unverify",
            json={},
            headers=admin_headers
        )
        assert unverify_res.status_code == 200
        assert unverify_res.json()["is_verified"] is False
        assert unverify_res.json()["verification_status"] == "REVOKED"

    # Cleanup
    await admin_collection.delete_many({"email": admin_email})
    await user_collection.delete_many({"email": {"$in": [user_email, f"deleted_"]}})
    await user_collection.delete_many({"original_email": user_email})
    await kyc_verification_collection.delete_many({"user_email": user_email})


@pytest.mark.asyncio
async def test_verified_user_in_competition_participant_list():
    """
    End-to-End Test for Verified User Badge in Competition Registration & Contestant Lists:
    1. Admin verifies User A after physical Aadhaar card inspection (with zero payload / no form needed).
    2. User B remains unverified.
    3. Both users register for a live competition.
    4. In Admin Competition Participants List (GET /api/admin/advanced-competitions/{comp_id}/participants):
       - User A displays is_verified=True, verified_badge=True, verified_tick=True.
       - User B displays is_verified=False, verified_badge=False, verified_tick=False.
    5. In Judge Contestants List (GET /api/judge/{comp_id}/contestants):
       - User A displays is_verified=True, verified_badge=True, verified_tick=True.
    6. When User A is on stage (GET /api/judge/{comp_id}/live-stage and GET /api/admin/.../live-stage):
       - Both Judge and Admin stage panels reflect is_verified=True and verified_badge=True.
    """
    admin_email = "admin_comp_verify@eventora.com"
    judge_email = "judge_comp_verify@eventora.com"
    user_a_email = "usera_verified@eventora.com"
    user_b_email = "userb_unverified@eventora.com"

    now = datetime.now(timezone.utc)

    # 1. Cleanup old test data
    await admin_collection.delete_many({"email": admin_email})
    await user_collection.delete_many({"email": {"$in": [user_a_email, user_b_email, judge_email]}})
    await competition_collection.delete_many({"title": "Grand Verification Championship"})
    await competition_registration_collection.delete_many({"user_email": {"$in": [user_a_email, user_b_email]}})
    await competition_submission_collection.delete_many({"user_email": {"$in": [user_a_email, user_b_email]}})
    await judge_assignment_collection.delete_many({"judge_email": judge_email})

    # 2. Insert Admin & Judge
    await admin_collection.insert_one({
        "email": admin_email,
        "password_hash": get_password_hash("adminpassword123"),
        "role": "super_admin",
        "created_at": now
    })

    judge_user = {
        "email": judge_email,
        "name": "Honorable Judge",
        "full_name": "Honorable Judge",
        "role": "JUDGE",
        "phone_number": "9876543299",
        "date_of_birth": "1980-01-01",
        "address": "High Court Street, Kolkata",
        "status": "ACTIVE",
        "email_verified": True,
        "is_profile_completed": True,
        "created_at": now
    }
    await user_collection.insert_one(judge_user)

    # 3. Insert User A and User B
    user_a_doc = {
        "email": user_a_email,
        "name": "Verified Superstar",
        "full_name": "Verified Superstar",
        "phone_number": "9998887771",
        "date_of_birth": "1998-05-20",
        "address": "Salt Lake Sector 1, Kolkata",
        "parent_phone_number": "9998887770",
        "role": "USER",
        "status": "ACTIVE",
        "email_verified": True,
        "is_profile_completed": True,
        "is_verified": False,
        "verified_badge": False,
        "verified_tick": False,
        "created_at": now,
        "updated_at": now
    }
    user_b_doc = {
        "email": user_b_email,
        "name": "Unverified Performer",
        "full_name": "Unverified Performer",
        "phone_number": "9998887772",
        "date_of_birth": "2000-08-15",
        "address": "Park Street, Kolkata",
        "parent_phone_number": "9998887773",
        "role": "USER",
        "status": "ACTIVE",
        "email_verified": True,
        "is_profile_completed": True,
        "is_verified": False,
        "verified_badge": False,
        "verified_tick": False,
        "created_at": now,
        "updated_at": now
    }
    res_a = await user_collection.insert_one(user_a_doc)
    user_a_id = str(res_a.inserted_id)
    await user_collection.insert_one(user_b_doc)

    # 4. Insert Competition & Assign Judge
    comp_doc = {
        "title": "Grand Verification Championship",
        "description": "Championship to test participant badge display",
        "status": "ACTIVE",
        "registration_status": "OPEN",
        "category": "MUSIC",
        "registration_deadline": "2027-12-31T23:59:59Z",
        "start_date": "2027-12-31T00:00:00Z",
        "end_date": "2027-12-31T23:59:59Z",
        "participation_fee": 0.0,
        "fee": 0.0,
        "is_paid": False,
        "price": 0.0,
        "audience_voting_enabled": False,
        "judge_scoring_enabled": True,
        "judging_criteria": [
            {"criterion_id": "c1", "name": "Vocal Technique", "max_score": 10, "weight": 1.0}
        ],
        "created_at": now
    }
    comp_res = await competition_collection.insert_one(comp_doc)
    comp_id = str(comp_res.inserted_id)

    await judge_assignment_collection.insert_one({
        "judge_email": judge_email,
        "competition_id": comp_id,
        "assigned_at": now
    })

    # Tokens
    admin_token = create_access_token({"sub": admin_email, "type": "admin", "role": "super_admin"})
    judge_token = create_access_token({"sub": judge_email, "type": "user", "role": "JUDGE"})
    user_a_token = create_access_token({"sub": user_a_email, "type": "user"})
    user_b_token = create_access_token({"sub": user_b_email, "type": "user"})

    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    judge_headers = {"Authorization": f"Bearer {judge_token}"}
    user_a_headers = {"Authorization": f"Bearer {user_a_token}"}
    user_b_headers = {"Authorization": f"Bearer {user_b_token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # ── Step A: Admin verifies User A with zero payload / no form needed ──
        verify_res = await ac.post(
            f"/api/admin/users/{user_a_id}/verify",
            headers=admin_headers
        )
        assert verify_res.status_code == 200, f"Admin verify failed: {verify_res.text}"
        assert verify_res.json()["is_verified"] is True
        assert verify_res.json()["verified_badge"] is True
        assert verify_res.json()["verified_tick"] is True

        # ── Step B: Both users register for competition ──
        reg_a = await competition_registration_collection.insert_one({
            "competition_id": comp_id,
            "user_email": user_a_email,
            "user_name": "Verified Superstar",
            "registration_reference": "CRG-VERIFIED01",
            "status": "CONFIRMED",
            "payment_status": "PAID",
            "fee": 0.0,
            "registered_at": now
        })
        reg_a_id = str(reg_a.inserted_id)

        await competition_registration_collection.insert_one({
            "competition_id": comp_id,
            "user_email": user_b_email,
            "user_name": "Unverified Performer",
            "registration_reference": "CRG-UNVERIFIED02",
            "status": "CONFIRMED",
            "payment_status": "PAID",
            "fee": 0.0,
            "registered_at": now
        })

        # User A has an approved submission
        await competition_submission_collection.insert_one({
            "competition_id": comp_id,
            "user_email": user_a_email,
            "name": "Verified Superstar",
            "registration_reference": "CRG-VERIFIED01",
            "title": "Acoustic Melody",
            "status": "APPROVED",
            "files": {"file": "https://example.com/audio.mp3"},
            "submitted_at": now
        })

        # ── Step C: Admin inspects Competition Participants List ──
        parts_res = await ac.get(f"/api/admin/advanced-competitions/{comp_id}/participants", headers=admin_headers)
        assert parts_res.status_code == 200, f"Participants endpoint failed: {parts_res.text}"
        participants = parts_res.json()["participants"]
        assert len(participants) >= 2

        part_a = next(p for p in participants if p["user_email"] == user_a_email)
        part_b = next(p for p in participants if p["user_email"] == user_b_email)

        # Verified participant shows verified badge & tick
        assert part_a["is_verified"] is True
        assert part_a["verified_badge"] is True
        assert part_a["verified_tick"] is True
        assert part_a["verification_status"] == "VERIFIED"

        # Unverified participant shows unverified
        assert part_b["is_verified"] is False
        assert part_b["verified_badge"] is False
        assert part_b["verified_tick"] is False

        # ── Step D: Judge inspects Contestants List ──
        judge_contestants = await ac.get(f"/api/judge/{comp_id}/contestants", headers=judge_headers)
        assert judge_contestants.status_code == 200, f"Judge contestants failed: {judge_contestants.text}"
        contestants = judge_contestants.json()
        contestant_a = next(c for c in contestants if c["user_email"] == user_a_email)

        assert contestant_a["is_verified"] is True
        assert contestant_a["verified_badge"] is True
        assert contestant_a["verified_tick"] is True
        assert contestant_a["verification_status"] == "VERIFIED"

        # ── Step E: Admin sets Verified User on Live Stage ──
        set_res = await ac.post(
            f"/api/admin/advanced-competitions/{comp_id}/stage/set-performer",
            json={"registration_id": reg_a_id},
            headers=admin_headers
        )
        assert set_res.status_code == 200, f"Set performer failed: {set_res.text}"
        curr_performer = set_res.json()["current_performer"]
        assert curr_performer["is_verified"] is True
        assert curr_performer["verified_badge"] is True
        assert curr_performer["verified_tick"] is True

        # Judge Live Stage Card shows verified badge
        judge_card_res = await ac.get(f"/api/judge/{comp_id}/live-stage", headers=judge_headers)
        assert judge_card_res.status_code == 200
        card_data = judge_card_res.json()["current_performer"]
        assert card_data["is_verified"] is True
        assert card_data["verified_badge"] is True
        assert card_data["verified_tick"] is True

        # Public Live Stage shows verified badge
        public_stage = await ac.get(f"/api/public/competitions/{comp_id}/live-stage")
        assert public_stage.status_code == 200
        pub_curr = public_stage.json()["current_performer"]
        assert pub_curr["is_verified"] is True
        assert pub_curr["verified_badge"] is True
        assert pub_curr["verified_tick"] is True

    # Cleanup
    await admin_collection.delete_many({"email": admin_email})
    await user_collection.delete_many({"email": {"$in": [user_a_email, user_b_email, judge_email]}})
    await competition_collection.delete_many({"title": "Grand Verification Championship"})
    await competition_registration_collection.delete_many({"user_email": {"$in": [user_a_email, user_b_email]}})
    await competition_submission_collection.delete_many({"user_email": {"$in": [user_a_email, user_b_email]}})
    await judge_assignment_collection.delete_many({"judge_email": judge_email})

