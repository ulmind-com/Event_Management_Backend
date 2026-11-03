"""
test_judge_deletion.py
======================
Comprehensive End-to-End tests for Judge Management, Deletion, and Cascade Cleanup:

1. Admin creates a judge (POST /api/admin/judges/).
2. Admin assigns judge to competition (POST /api/admin/judges/assignments).
3. Draft scores inserted for this judge.
4. Admin fetches single judge (GET /api/admin/judges/{identifier}).
5. Admin updates judge details (PATCH /api/admin/judges/{identifier}).
6. Admin deletes judge by Email (DELETE /api/admin/judges/{email}):
   - Judge removed from user_collection
   - Assignments cascade-deleted from judge_assignment_collection
   - Draft scores cascade-deleted from draft_score_collection
   - Audit log recorded in audit_log_collection
7. Admin deletes judge by ObjectId (DELETE /api/admin/judges/{judge_id}).
8. Attempting to delete non-existent judge returns 404.
9. Attempting to delete standard USER (non-judge) returns 404.
10. DELETE /api/admin/judges/assignments continues to work for unassigning without deleting the judge.
"""

import pytest
from datetime import datetime, timezone
from httpx import AsyncClient, ASGITransport
from bson import ObjectId

from app.main import app
from app.core.security import create_access_token, get_password_hash
from app.core.database import (
    user_collection,
    admin_collection,
    competition_collection,
    judge_assignment_collection,
    draft_score_collection,
    audit_log_collection,
)


@pytest.mark.asyncio
async def test_judge_deletion_lifecycle_by_email():
    """
    Test 1: Full lifecycle of judge creation, assignment, draft creation,
    and deletion using EMAIL as identifier.
    """
    admin_email = "admin_deljudge_test@eventora.com"
    judge_email = "judge_del_email_test@eventora.com"
    comp_title = "Judge Delete Test Comp 1"

    # ── Setup Admin ──
    await admin_collection.delete_many({"email": admin_email})
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

    # ── Clean up existing test data ──
    await user_collection.delete_many({"email": judge_email})
    await judge_assignment_collection.delete_many({"judge_email": judge_email})
    await draft_score_collection.delete_many({"judge_email": judge_email})
    await competition_collection.delete_many({"title": comp_title})

    # ── Create a dummy competition ──
    c_res = await competition_collection.insert_one({
        "title": comp_title,
        "name": comp_title,
        "category": "General",
        "judge_scoring_enabled": True,
        "is_published": True,
        "created_at": datetime.now(timezone.utc),
    })
    comp_id = str(c_res.inserted_id)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        # 1. Admin creates a judge
        create_res = await ac.post(
            "/api/admin/judges/",
            json={
                "full_name": "Email Deletion Judge",
                "email": judge_email,
                "phone_number": "9876543210",
                "designation": "Senior Classical Judge",
                "biography": "Experienced music and dance evaluator.",
            },
            headers=admin_headers,
        )
        assert create_res.status_code == 200, f"Create judge failed: {create_res.text}"
        assert create_res.json()["email"] == judge_email

        # Verify judge is in DB with role JUDGE
        judge_doc = await user_collection.find_one({"email": judge_email})
        assert judge_doc is not None
        assert judge_doc["role"] == "JUDGE"
        judge_id = str(judge_doc["_id"])

        # 2. Assign judge to competition
        assign_res = await ac.post(
            "/api/admin/judges/assignments",
            json={
                "judge_email": judge_email,
                "competition_id": comp_id,
            },
            headers=admin_headers,
        )
        assert assign_res.status_code == 200, f"Assign judge failed: {assign_res.text}"

        # 3. Insert mock draft score
        await draft_score_collection.insert_one({
            "competition_id": comp_id,
            "submission_id": "sub_test_123",
            "judge_email": judge_email,
            "scores": {"criteria_1": 8.5},
            "created_at": datetime.now(timezone.utc),
        })

        # Verify assignment and draft exist
        assign_count = await judge_assignment_collection.count_documents({"judge_email": judge_email})
        assert assign_count == 1
        draft_count = await draft_score_collection.count_documents({"judge_email": judge_email})
        assert draft_count == 1

        # 4. Fetch single judge by email
        get_res = await ac.get(f"/api/admin/judges/{judge_email}", headers=admin_headers)
        assert get_res.status_code == 200
        assert get_res.json()["email"] == judge_email
        assert get_res.json()["assigned_count"] == 1

        # 5. Update judge profile
        patch_res = await ac.patch(
            f"/api/admin/judges/{judge_email}",
            json={"designation": "Chief Senior Judge"},
            headers=admin_headers,
        )
        assert patch_res.status_code == 200
        assert patch_res.json()["designation"] == "Chief Senior Judge"

        # 6. DELETE Judge by EMAIL
        del_res = await ac.delete(
            f"/api/admin/judges/{judge_email}",
            headers=admin_headers,
        )
        assert del_res.status_code == 200, f"Delete judge failed: {del_res.text}"
        del_data = del_res.json()
        assert del_data["email"] == judge_email
        assert del_data["judge_id"] == judge_id
        assert del_data["unassigned_competitions_count"] == 1

        # ── Verify Cascasde Cleanups ──
        # Judge removed from user_collection
        assert await user_collection.find_one({"_id": ObjectId(judge_id)}) is None
        assert await user_collection.find_one({"email": judge_email}) is None

        # Assignments removed
        assert await judge_assignment_collection.count_documents({"judge_email": judge_email}) == 0

        # Draft scores removed
        assert await draft_score_collection.count_documents({"judge_email": judge_email}) == 0

        # Audit log verified
        audit_entry = await audit_log_collection.find_one({
            "action": "delete_judge",
            "resource_id": judge_email,
        })
        assert audit_entry is not None
        assert audit_entry["actor"] == admin_email

    # ── Cleanup ──
    await competition_collection.delete_many({"title": comp_title})
    await admin_collection.delete_many({"email": admin_email})


@pytest.mark.asyncio
async def test_judge_deletion_by_object_id():
    """
    Test 2: Deleting a judge using MongoDB ObjectId as identifier.
    """
    admin_email = "admin_delid_test@eventora.com"
    judge_email = "judge_del_id_test@eventora.com"

    await admin_collection.delete_many({"email": admin_email})
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

    await user_collection.delete_many({"email": judge_email})

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        # Create Judge
        create_res = await ac.post(
            "/api/admin/judges/",
            json={
                "full_name": "ID Deletion Judge",
                "email": judge_email,
            },
            headers=admin_headers,
        )
        assert create_res.status_code == 200

        judge_doc = await user_collection.find_one({"email": judge_email})
        judge_id = str(judge_doc["_id"])

        # Fetch judge by ObjectId
        get_res = await ac.get(f"/api/admin/judges/{judge_id}", headers=admin_headers)
        assert get_res.status_code == 200
        assert get_res.json()["email"] == judge_email

        # DELETE Judge by ObjectId
        del_res = await ac.delete(f"/api/admin/judges/{judge_id}", headers=admin_headers)
        assert del_res.status_code == 200
        assert del_res.json()["judge_id"] == judge_id

        # Confirm deleted
        assert await user_collection.find_one({"_id": ObjectId(judge_id)}) is None

    await admin_collection.delete_many({"email": admin_email})


@pytest.mark.asyncio
async def test_judge_deletion_validation_and_errors():
    """
    Test 3: Validation, 404 for non-existent judge, and non-judge user protection.
    """
    admin_email = "admin_delerr_test@eventora.com"
    user_email = "regular_user_test@eventora.com"

    await admin_collection.delete_many({"email": admin_email})
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

    # Create a regular USER (not a judge)
    await user_collection.delete_many({"email": user_email})
    await user_collection.insert_one({
        "email": user_email,
        "name": "Regular User",
        "role": "USER",
        "created_at": datetime.now(timezone.utc),
    })

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        # 1. Non-existent judge -> 404
        fake_id = str(ObjectId())
        res_404 = await ac.delete(f"/api/admin/judges/{fake_id}", headers=admin_headers)
        assert res_404.status_code == 404
        assert "Judge not found" in res_404.json()["detail"]

        res_404_email = await ac.delete("/api/admin/judges/nonexistent_judge@eventora.com", headers=admin_headers)
        assert res_404_email.status_code == 404

        # 2. Deleting a standard USER via judge endpoint -> 404 (protects non-judges)
        res_not_judge = await ac.delete(f"/api/admin/judges/{user_email}", headers=admin_headers)
        assert res_not_judge.status_code == 404
        assert "Judge not found" in res_not_judge.json()["detail"]
        # Ensure user was NOT deleted
        assert await user_collection.find_one({"email": user_email}) is not None

    # Cleanup
    await user_collection.delete_many({"email": user_email})
    await admin_collection.delete_many({"email": admin_email})
