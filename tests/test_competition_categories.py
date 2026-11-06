"""
test_competition_categories.py
==============================
End-to-end production tests for Competition Categories system:

1. Admin creates competition with custom categories and category-specific age limits.
2. Admin updates categories.
3. Category-specific eligibility and age limit enforcement (rejection on age violation).
4. User registers selecting specific category.
5. Competition listing by category and /competitions/by-category endpoint.
6. User profile returns registered competitions grouped by category.
7. Category-wise leaderboard calculation and results publication (separate ranks per category).
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
    competition_submission_collection,
    judge_score_collection,
    leaderboard_result_collection,
    admin_collection,
    user_collection,
    judge_assignment_collection,
    db,
)
from app.services.competition_lifecycle import safe_object_id


@pytest.mark.asyncio
async def test_competition_category_creation_and_update():
    """
    Scenario 1: Admin creates competition with multiple categories & age limits,
    and updates them.
    """
    admin_email = f"admin_cat_{int(datetime.now().timestamp())}@test.com"
    comp_title = f"Dance Championship {int(datetime.now().timestamp())}"

    # Setup admin
    await admin_collection.insert_one({
        "email": admin_email,
        "password_hash": get_password_hash("adminpass123"),
        "role": "super_admin",
        "created_at": datetime.now(timezone.utc),
    })
    admin_token = create_access_token({
        "sub": admin_email,
        "type": "admin",
        "role": "super_admin",
    })
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        now = datetime.now(timezone.utc)
        payload = {
            "title": comp_title,
            "description": "Multi-category dance contest",
            "categories": [
                {"name": "Junior", "min_age": 6, "max_age": 14, "description": "Ages 6 to 14"},
                {"name": "Senior", "min_age": 15, "max_age": 25, "description": "Ages 15 to 25"},
                {"name": "Open", "min_age": None, "max_age": None, "description": "All ages welcome"},
            ],
            "start_date": (now - timedelta(days=1)).isoformat(),
            "end_date": (now + timedelta(days=30)).isoformat(),
            "registration_start_date": (now - timedelta(days=1)).isoformat(),
            "submission_deadline": (now + timedelta(days=25)).isoformat(),
            "is_published": True,
            "judge_scoring_enabled": True,
            "audience_voting_enabled": False,
        }

        # 1. Create competition
        res = await ac.post("/api/admin/advanced-competitions/", json=payload, headers=admin_headers)
        assert res.status_code == 200, f"Expected 200 but got {res.status_code}: {res.text}"
        comp_data = res.json()
        comp_id = comp_data["_id"]

        # Verify categories saved in DB with auto-formatted age_limit strings
        stored = await competition_collection.find_one({"_id": ObjectId(comp_id)})
        assert stored is not None
        assert len(stored.get("categories", [])) == 3

        cat_names = [c["name"] for c in stored["categories"]]
        assert "Junior" in cat_names
        assert "Senior" in cat_names
        assert "Open" in cat_names

        junior_cat = next(c for c in stored["categories"] if c["name"] == "Junior")
        assert junior_cat["min_age"] == 6
        assert junior_cat["max_age"] == 14
        assert "6-14" in junior_cat["age_limit"]

        senior_cat = next(c for c in stored["categories"] if c["name"] == "Senior")
        assert senior_cat["min_age"] == 15
        assert senior_cat["max_age"] == 25

        # 2. Update competition categories
        update_payload = {
            "categories": [
                {"name": "Junior", "min_age": 7, "max_age": 14},
                {"name": "Senior", "min_age": 15, "max_age": 28},
                {"name": "Veterans", "min_age": 50, "max_age": 90},
            ]
        }
        update_res = await ac.put(f"/api/admin/advanced-competitions/{comp_id}", json=update_payload, headers=admin_headers)
        assert update_res.status_code == 200

        updated_stored = await competition_collection.find_one({"_id": ObjectId(comp_id)})
        assert len(updated_stored["categories"]) == 3
        updated_names = [c["name"] for c in updated_stored["categories"]]
        assert "Veterans" in updated_names
        veterans_cat = next(c for c in updated_stored["categories"] if c["name"] == "Veterans")
        assert veterans_cat["min_age"] == 50
        assert veterans_cat["max_age"] == 90


@pytest.mark.asyncio
async def test_category_eligibility_and_registration_flow():
    """
    Scenario 2: Tests category-specific age eligibility check and participant registration.
    - Junior (age 7-14)
    - Senior (age 15-28)
    - User 1 (age 10): eligible for Junior, rejected for Senior
    - User 2 (age 20): rejected for Junior, eligible for Senior
    - User 3 tries to register with non-existent category -> 400 Bad Request
    """
    admin_email = f"admin_cat2_{int(datetime.now().timestamp())}@test.com"
    comp_title = f"Talent Show {int(datetime.now().timestamp())}"

    # Setup admin
    await admin_collection.insert_one({
        "email": admin_email,
        "password_hash": get_password_hash("adminpass123"),
        "role": "super_admin",
        "created_at": datetime.now(timezone.utc),
    })
    admin_token = create_access_token({"sub": admin_email, "type": "admin", "role": "super_admin"})
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        now = datetime.now(timezone.utc)
        comp_res = await ac.post("/api/admin/advanced-competitions/", json={
            "title": comp_title,
            "description": "Multi-category talent show",
            "categories": [
                {"name": "Junior", "min_age": 7, "max_age": 14},
                {"name": "Senior", "min_age": 15, "max_age": 28},
            ],
            "start_date": (now - timedelta(days=1)).isoformat(),
            "end_date": (now + timedelta(days=30)).isoformat(),
            "registration_start_date": (now - timedelta(days=1)).isoformat(),
            "submission_deadline": (now + timedelta(days=25)).isoformat(),
            "is_published": True,
            "judge_scoring_enabled": True,
            "audience_voting_enabled": False,
        }, headers=admin_headers)
        comp_id = comp_res.json()["_id"]

        # Setup User 1 (Age 10, born 2016)
        user1_email = f"kid_{int(datetime.now().timestamp())}@test.com"
        await user_collection.delete_many({"email": user1_email})
        await user_collection.insert_one({
            "email": user1_email,
            "name": "Kid Dancer",
            "phone_number": 9876543210,
            "date_of_birth": "2016-01-01",
            "address": "123 Kid Lane",
            "parent_phone_number": 9876543211,
            "role": "USER",
            "registration_complete": True,
            "is_profile_completed": True,
            "profile_completed": True,
        })
        user1_token = create_access_token({"sub": user1_email, "email": user1_email, "role": "USER"})
        user1_headers = {"Authorization": f"Bearer {user1_token}"}

        # Setup User 2 (Age 20, born 2006)
        user2_email = f"adult_{int(datetime.now().timestamp())}@test.com"
        await user_collection.delete_many({"email": user2_email})
        await user_collection.insert_one({
            "email": user2_email,
            "name": "Adult Dancer",
            "phone_number": 9876543220,
            "date_of_birth": "2006-01-01",
            "address": "456 Adult Blvd",
            "parent_phone_number": 9876543221,
            "role": "USER",
            "registration_complete": True,
            "is_profile_completed": True,
            "profile_completed": True,
        })
        user2_token = create_access_token({"sub": user2_email, "email": user2_email, "role": "USER"})
        user2_headers = {"Authorization": f"Bearer {user2_token}"}

        # 1. User 1 checks Junior eligibility -> eligible
        e1_junior = await ac.get(f"/api/v1/competitions/{comp_id}/eligibility?category=Junior", headers=user1_headers)
        assert e1_junior.status_code == 200
        assert e1_junior.json()["eligible"] is True

        # 2. User 1 checks Senior eligibility -> not eligible (too young)
        e1_senior = await ac.get(f"/api/v1/competitions/{comp_id}/eligibility?category=Senior", headers=user1_headers)
        assert e1_senior.status_code == 200
        assert e1_senior.json()["eligible"] is False
        assert "Minimum age requirement" in e1_senior.json()["reason"]

        # 3. User 2 checks Junior eligibility -> not eligible (too old)
        e2_junior = await ac.get(f"/api/v1/competitions/{comp_id}/eligibility?category=Junior", headers=user2_headers)
        assert e2_junior.status_code == 200
        assert e2_junior.json()["eligible"] is False
        assert "Maximum age requirement" in e2_junior.json()["reason"]

        # 4. User 1 registers for Junior category -> SUCCESS
        r1 = await ac.post(
            f"/api/v1/competitions/{comp_id}/register?category=Junior&rules_accepted=true",
            headers=user1_headers
        )
        assert r1.status_code == 200
        assert r1.json()["category"] == "Junior"

        # Verify DB document
        reg1_doc = await competition_registration_collection.find_one({
            "competition_id": comp_id,
            "user_email": user1_email
        })
        assert reg1_doc is not None
        assert reg1_doc.get("category") == "Junior"

        # 5. User 2 registers for Senior category -> SUCCESS
        r2 = await ac.post(
            f"/api/v1/competitions/{comp_id}/register?category=Senior&rules_accepted=true",
            headers=user2_headers
        )
        assert r2.status_code == 200
        assert r2.json()["category"] == "Senior"

        # 6. User 2 tries to register another account for Junior category -> 403 Forbidden
        user3_email = f"adult2_{int(datetime.now().timestamp())}@test.com"
        await user_collection.insert_one({
            "email": user3_email,
            "name": "Adult Two",
            "phone_number": 9876543230,
            "date_of_birth": "2000-01-01",
            "address": "789 Senior Ave",
            "parent_phone_number": 9876543231,
            "role": "USER",
            "registration_complete": True,
            "is_profile_completed": True,
            "profile_completed": True,
        })
        user3_token = create_access_token({"sub": user3_email, "email": user3_email, "role": "USER"})
        user3_headers = {"Authorization": f"Bearer {user3_token}"}

        r3_fail = await ac.post(
            f"/api/v1/competitions/{comp_id}/register?category=Junior&rules_accepted=true",
            headers=user3_headers
        )
        assert r3_fail.status_code == 403
        assert "Maximum age requirement" in r3_fail.text

        # 7. Registration with invalid category -> 400 Bad Request
        r_invalid = await ac.post(
            f"/api/v1/competitions/{comp_id}/register?category=InvalidCat&rules_accepted=true",
            headers=user3_headers
        )
        assert r_invalid.status_code == 400
        assert "Invalid" in r_invalid.text


@pytest.mark.asyncio
async def test_category_wise_leaderboard_and_profile():
    """
    Scenario 3: Tests end-to-end category-wise leaderboard calculation and results:
    - User A (Junior) gets 90 score.
    - User B (Junior) gets 98 score.
    - User C (Senior) gets 88 score.
    - Leaderboard shows:
      - Junior category: User B (Rank 1), User A (Rank 2)
      - Senior category: User C (Rank 1)
    - Filtering by category returns that category's rankings.
    - Results publishing preserves category-wise results.
    - User profile returns categorized competitions.
    """
    ts = int(datetime.now().timestamp())
    admin_email = f"admin_cat3_{ts}@test.com"
    comp_title = f"Singing Idol {ts}"

    # Setup admin
    await admin_collection.insert_one({
        "email": admin_email,
        "password_hash": get_password_hash("adminpass123"),
        "role": "super_admin",
        "created_at": datetime.now(timezone.utc),
    })
    admin_token = create_access_token({"sub": admin_email, "type": "admin", "role": "super_admin"})
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        now = datetime.now(timezone.utc)
        comp_res = await ac.post("/api/admin/advanced-competitions/", json={
            "title": comp_title,
            "description": "Multi-category singing contest",
            "categories": [
                {"name": "Junior", "min_age": 5, "max_age": 14},
                {"name": "Senior", "min_age": 15, "max_age": 30},
            ],
            "start_date": (now - timedelta(days=1)).isoformat(),
            "end_date": (now + timedelta(days=30)).isoformat(),
            "registration_start_date": (now - timedelta(days=1)).isoformat(),
            "submission_deadline": (now + timedelta(days=25)).isoformat(),
            "is_published": True,
            "judge_scoring_enabled": True,
            "audience_voting_enabled": False,
            "judging_criteria": [
                {"name": "Vocal Quality", "max_score": 100, "weight": 1.0}
            ]
        }, headers=admin_headers)
        comp_id = comp_res.json()["_id"]

        # User A (Junior, born 2015, age 11)
        uA_email = f"user_a_{ts}@test.com"
        await user_collection.insert_one({
            "email": uA_email, "name": "Alice Junior", "phone_number": 9000000001,
            "date_of_birth": "2015-05-05", "address": "Alice St", "parent_phone_number": 9000000011,
            "role": "USER", "registration_complete": True, "is_profile_completed": True, "profile_completed": True,
        })
        tok_A = create_access_token({"sub": uA_email, "email": uA_email, "role": "USER"})
        h_A = {"Authorization": f"Bearer {tok_A}"}

        # User B (Junior, born 2014, age 12)
        uB_email = f"user_b_{ts}@test.com"
        await user_collection.insert_one({
            "email": uB_email, "name": "Bob Junior", "phone_number": 9000000002,
            "date_of_birth": "2014-05-05", "address": "Bob St", "parent_phone_number": 9000000012,
            "role": "USER", "registration_complete": True, "is_profile_completed": True, "profile_completed": True,
        })
        tok_B = create_access_token({"sub": uB_email, "email": uB_email, "role": "USER"})
        h_B = {"Authorization": f"Bearer {tok_B}"}

        # User C (Senior, born 2002, age 24)
        uC_email = f"user_c_{ts}@test.com"
        await user_collection.insert_one({
            "email": uC_email, "name": "Charlie Senior", "phone_number": 9000000003,
            "date_of_birth": "2002-05-05", "address": "Charlie St", "parent_phone_number": 9000000013,
            "role": "USER", "registration_complete": True, "is_profile_completed": True, "profile_completed": True,
        })
        tok_C = create_access_token({"sub": uC_email, "email": uC_email, "role": "USER"})
        h_C = {"Authorization": f"Bearer {tok_C}"}

        # Register users under their categories
        await ac.post(f"/api/v1/competitions/{comp_id}/register?category=Junior&rules_accepted=true", headers=h_A)
        await ac.post(f"/api/v1/competitions/{comp_id}/register?category=Junior&rules_accepted=true", headers=h_B)
        await ac.post(f"/api/v1/competitions/{comp_id}/register?category=Senior&rules_accepted=true", headers=h_C)

        # Create mock submissions for them
        subA = await competition_submission_collection.insert_one({
            "competition_id": comp_id, "user_email": uA_email, "category": "Junior",
            "status": "APPROVED", "files": {}, "submitted_at": datetime.now(timezone.utc)
        })
        subB = await competition_submission_collection.insert_one({
            "competition_id": comp_id, "user_email": uB_email, "category": "Junior",
            "status": "APPROVED", "files": {}, "submitted_at": datetime.now(timezone.utc)
        })
        subC = await competition_submission_collection.insert_one({
            "competition_id": comp_id, "user_email": uC_email, "category": "Senior",
            "status": "APPROVED", "files": {}, "submitted_at": datetime.now(timezone.utc)
        })

        # Add judge scores:
        # Alice (Junior): 90
        # Bob (Junior): 98
        # Charlie (Senior): 88
        judge_email = f"judge_{ts}@test.com"
        await judge_score_collection.insert_one({
            "competition_id": comp_id, "submission_id": str(subA.inserted_id), "participant_id": uA_email,
            "judge_email": judge_email, "scores": {"Vocal Quality": 90}, "total_score": 90.0,
            "is_locked": True, "scored_at": datetime.now(timezone.utc)
        })
        await judge_score_collection.insert_one({
            "competition_id": comp_id, "submission_id": str(subB.inserted_id), "participant_id": uB_email,
            "judge_email": judge_email, "scores": {"Vocal Quality": 98}, "total_score": 98.0,
            "is_locked": True, "scored_at": datetime.now(timezone.utc)
        })
        await judge_score_collection.insert_one({
            "competition_id": comp_id, "submission_id": str(subC.inserted_id), "participant_id": uC_email,
            "judge_email": judge_email, "scores": {"Vocal Quality": 88}, "total_score": 88.0,
            "is_locked": True, "scored_at": datetime.now(timezone.utc)
        })

        # 1. Check provisional leaderboard
        lb_res = await ac.get(f"/api/v1/competitions/{comp_id}/leaderboard")
        assert lb_res.status_code == 200
        lb_data = lb_res.json()

        assert "category_leaderboards" in lb_data
        cat_lbs = lb_data["category_leaderboards"]
        assert "Junior" in cat_lbs
        assert "Senior" in cat_lbs

        # Junior: Bob (98) should be Rank 1, Alice (90) should be Rank 2
        junior_ranks = cat_lbs["Junior"]
        assert len(junior_ranks) == 2
        assert junior_ranks[0]["participant_id"] == uB_email
        assert junior_ranks[0]["rank"] == 1
        assert junior_ranks[1]["participant_id"] == uA_email
        assert junior_ranks[1]["rank"] == 2

        # Senior: Charlie (88) should be Rank 1
        senior_ranks = cat_lbs["Senior"]
        assert len(senior_ranks) == 1
        assert senior_ranks[0]["participant_id"] == uC_email
        assert senior_ranks[0]["rank"] == 1

        # 2. Check querying leaderboard with category filter: ?category=Junior
        junior_filter_res = await ac.get(f"/api/v1/competitions/{comp_id}/leaderboard?category=Junior")
        assert junior_filter_res.status_code == 200
        junior_filter_data = junior_filter_res.json()
        assert len(junior_filter_data["leaderboard"]) == 2
        assert junior_filter_data["leaderboard"][0]["participant_id"] == uB_email
        assert junior_filter_data["leaderboard"][0]["rank"] == 1

        # 3. Check public competition group-by-category endpoint
        by_cat_res = await ac.get("/api/activities/competitions/by-category")
        assert by_cat_res.status_code == 200
        by_cat_data = by_cat_res.json()
        assert "categories" in by_cat_data
        assert "Junior" in by_cat_data["by_category"]

        # 4. Check user profile dashboard category grouping
        dash_res = await ac.get("/api/profile/dashboard", headers=h_A)
        assert dash_res.status_code == 200
        dash_data = dash_res.json()
        assert "my_competitions_by_category" in dash_data
        assert "Junior" in dash_data["my_competitions_by_category"]

        # 5. Check user dedicated /my-competitions endpoint
        my_comps_res = await ac.get("/api/profile/my-competitions", headers=h_A)
        assert my_comps_res.status_code == 200
        my_comps_data = my_comps_res.json()
        assert "by_category" in my_comps_data
        assert "Junior" in my_comps_data["by_category"]

        # 6. Admin approves results
        appr_res = await ac.post(f"/api/admin/advanced-competitions/{comp_id}/results/approve", headers=admin_headers)
        assert appr_res.status_code == 200
        assert appr_res.json()["total_ranked"] == 3

        # 7. Admin publishes results
        pub_res = await ac.post(f"/api/admin/advanced-competitions/{comp_id}/results/publish", headers=admin_headers)
        assert pub_res.status_code == 200

        # 8. Check official published results endpoint with category filter
        res_junior = await ac.get(f"/api/v1/competitions/{comp_id}/results?category=Junior")
        assert res_junior.status_code == 200
        junior_results = res_junior.json()
        assert "category_leaderboards" in junior_results
        assert len(junior_results["leaderboard"]) == 2
        assert junior_results["leaderboard"][0]["participant_id"] == uB_email


@pytest.mark.asyncio
async def test_category_age_limit_dob_calculation_and_direct_participation():
    """
    Scenario 4: User's exact prompt scenario:
    - Admin creates a competition category 'Junior' with age limit '12-15' (string format).
    - User with DOB making them age 13 participates in category 'Junior' -> SUCCESS.
    - User with DOB making them age 10 participates in category 'Junior' -> REJECTED (403 Forbidden).
    - User with DOB making them age 17 participates in category 'Junior' -> REJECTED (403 Forbidden).
    - User without DOB in profile supplies DOB during registration -> SUCCESS and profile updated.
    - Registration document and submission document permanently store category, date_of_birth, and calculated age.
    """
    admin_email = f"admin_dob_{int(datetime.now().timestamp())}@test.com"
    comp_title = f"Singing Championship {int(datetime.now().timestamp())}"

    # Setup admin
    await admin_collection.insert_one({
        "email": admin_email,
        "password_hash": get_password_hash("adminpass123"),
        "role": "super_admin",
        "created_at": datetime.now(timezone.utc),
    })
    admin_token = create_access_token({"sub": admin_email, "type": "admin", "role": "super_admin"})
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        now = datetime.now(timezone.utc)
        # 1. Admin creates competition with custom category "Junior" and age_limit "12-15"
        comp_payload = {
            "title": comp_title,
            "description": "National singing championship with age-specific categories",
            "categories": [
                {
                    "name": "Junior",
                    "age_limit": "12-15",
                    "description": "For participants between 12 and 15 years of age"
                },
                {
                    "name": "Senior",
                    "age_limit": "16-25",
                    "description": "For participants between 16 and 25 years of age"
                }
            ],
            "start_date": (now - timedelta(days=1)).isoformat(),
            "end_date": (now + timedelta(days=30)).isoformat(),
            "registration_start_date": (now - timedelta(days=1)).isoformat(),
            "submission_deadline": (now + timedelta(days=25)).isoformat(),
            "is_published": True,
            "judge_scoring_enabled": True,
            "audience_voting_enabled": False,
        }
        res = await ac.post("/api/admin/advanced-competitions/", json=comp_payload, headers=admin_headers)
        assert res.status_code == 200, f"Failed: {res.text}"
        comp_data = res.json()
        comp_id = comp_data["_id"]

        # Verify age_limit '12-15' was auto-parsed into min_age: 12 and max_age: 15
        comp_db = await competition_collection.find_one({"_id": ObjectId(comp_id)})
        junior_cfg = next(c for c in comp_db["categories"] if c["name"] == "Junior")
        assert junior_cfg["min_age"] == 12
        assert junior_cfg["max_age"] == 15

        # 2. Eligible User (Age 13: Born 2013-05-15) participates in category 'Junior'
        user13_email = f"user13_{int(datetime.now().timestamp())}@test.com"
        await user_collection.insert_one({
            "email": user13_email,
            "name": "Thirteen Year Old",
            "date_of_birth": "2013-05-15",
            "phone_number": "9876543213",
            "address": "13 Youth Road",
            "parent_phone_number": "9876543214",
            "role": "USER",
            "registration_complete": True,
            "is_profile_completed": True,
            "profile_completed": True,
        })
        token13 = create_access_token({"sub": user13_email, "email": user13_email, "role": "USER"})
        h13 = {"Authorization": f"Bearer {token13}"}

        # Check category eligibility endpoint
        elig_res = await ac.get(f"/api/v1/competitions/{comp_id}/categories/Junior/eligibility", headers=h13)
        assert elig_res.status_code == 200
        assert elig_res.json()["eligible"] is True
        assert elig_res.json()["calculated_age"] == 13

        # Direct participate in category 'Junior'
        part_res = await ac.post(
            f"/api/v1/competitions/{comp_id}/categories/Junior/participate?rules_accepted=true",
            headers=h13
        )
        assert part_res.status_code == 200
        assert part_res.json()["category"] == "Junior"

        # Verify saved registration document has category, date_of_birth, and calculated age
        reg13 = await competition_registration_collection.find_one({
            "competition_id": comp_id,
            "user_email": user13_email
        })
        assert reg13 is not None
        assert reg13["category"] == "Junior"
        assert reg13["date_of_birth"] == "2013-05-15"
        assert reg13["age"] == 13

        # 3. Too Young User (Age 10: Born 2016-05-15) tries to participate in category 'Junior' -> 403
        user10_email = f"user10_{int(datetime.now().timestamp())}@test.com"
        await user_collection.insert_one({
            "email": user10_email,
            "name": "Ten Year Old",
            "date_of_birth": "2016-05-15",
            "phone_number": "9876543210",
            "address": "10 Kid Road",
            "parent_phone_number": "9876543211",
            "role": "USER",
            "registration_complete": True,
            "is_profile_completed": True,
            "profile_completed": True,
        })
        token10 = create_access_token({"sub": user10_email, "email": user10_email, "role": "USER"})
        h10 = {"Authorization": f"Bearer {token10}"}

        # Check eligibility -> eligible = False
        elig10 = await ac.get(f"/api/v1/competitions/{comp_id}/categories/Junior/eligibility", headers=h10)
        assert elig10.status_code == 200
        assert elig10.json()["eligible"] is False
        assert "Minimum age requirement" in elig10.json()["reason"]
        assert elig10.json()["calculated_age"] == 10

        # Attempt to register/participate -> 403 Forbidden
        reject10 = await ac.post(
            f"/api/v1/competitions/{comp_id}/categories/Junior/participate?rules_accepted=true",
            headers=h10
        )
        assert reject10.status_code == 403
        assert "Minimum age requirement" in reject10.json()["detail"]

        # 4. Too Old User (Age 17: Born 2009-05-15) tries to participate in category 'Junior' -> 403
        user17_email = f"user17_{int(datetime.now().timestamp())}@test.com"
        await user_collection.insert_one({
            "email": user17_email,
            "name": "Seventeen Year Old",
            "date_of_birth": "2009-05-15",
            "phone_number": "9876543217",
            "address": "17 Teen Road",
            "parent_phone_number": "9876543218",
            "role": "USER",
            "registration_complete": True,
            "is_profile_completed": True,
            "profile_completed": True,
        })
        token17 = create_access_token({"sub": user17_email, "email": user17_email, "role": "USER"})
        h17 = {"Authorization": f"Bearer {token17}"}

        reject17 = await ac.post(
            f"/api/v1/competitions/{comp_id}/categories/Junior/participate?rules_accepted=true",
            headers=h17
        )
        assert reject17.status_code == 403
        assert "Maximum age requirement" in reject17.json()["detail"]

        # 5. User with NO DOB in profile provides DOB in registration request
        user_nodob_email = f"user_nodob_{int(datetime.now().timestamp())}@test.com"
        await user_collection.insert_one({
            "email": user_nodob_email,
            "name": "No Profile DOB Yet",
            "phone_number": "9876543299",
            "address": "99 Unknown Road",
            "parent_phone_number": "9876543298",
            "role": "USER",
            "registration_complete": True,
        })
        token_nodob = create_access_token({"sub": user_nodob_email, "email": user_nodob_email, "role": "USER"})
        h_nodob = {"Authorization": f"Bearer {token_nodob}"}

        # First try without DOB -> rejected because DOB is needed
        no_dob_reject = await ac.post(
            f"/api/v1/competitions/{comp_id}/categories/Junior/participate",
            json={"rules_accepted": True},
            headers=h_nodob
        )
        assert no_dob_reject.status_code == 403

        # Now pass date_of_birth="2013-05-15" in request body -> SUCCESS
        with_dob_success = await ac.post(
            f"/api/v1/competitions/{comp_id}/categories/Junior/participate",
            json={"rules_accepted": True, "date_of_birth": "2013-05-15"},
            headers=h_nodob
        )
        assert with_dob_success.status_code == 200
        assert with_dob_success.json()["category"] == "Junior"

        # Verify user profile was updated with date_of_birth
        usr_doc = await user_collection.find_one({"email": user_nodob_email})
        assert usr_doc.get("date_of_birth") == "2013-05-15"

        # Verify registration doc has category and calculated age 13
        reg_nodob = await competition_registration_collection.find_one({
            "competition_id": comp_id,
            "user_email": user_nodob_email
        })
        assert reg_nodob["category"] == "Junior"
        assert reg_nodob["age"] == 13
        assert reg_nodob["date_of_birth"] == "2013-05-15"
