"""
test_judge_flow_e2e.py
======================
Comprehensive End-to-End Test Suite for Judges Management, Assignment, Authentication,
and Scoring Lifecycle:
1. Admin creates a judge (POST /api/admin/judges/).
2. Duplicate judge email rejection (409 Conflict).
3. Admin lists judges and verifies assigned_count=0 (GET /api/admin/judges/).
4. Admin updates judge profile details (PATCH /api/admin/judges/{email}).
5. Admin seeds competition with criteria and contestants.
6. Unassigned judge access gate: unassigned judge receives 403 Forbidden.
7. Admin assigns judge to competition (POST /api/admin/judges/assignments).
8. Duplicate assignment rejected with 409 Conflict.
9. Judge OTP login: Step 1 (send OTP) -> Step 2 (verify OTP) -> Set password.
10. Judge direct password login (POST /api/judge/auth/login).
11. Judge lists assigned competitions (GET /api/judge/).
12. Judge lists contestants with rich user details (GET /api/judge/{comp_id}/contestants).
13. Judge saves draft score (POST /api/judge/{comp_id}/contestants/{sub_id}/draft).
14. Judge retrieves saved draft score (GET /api/judge/{comp_id}/contestants/{sub_id}/draft).
15. Judge declares Conflict of Interest (COI) skip for a contestant.
16. Judge scoring rejected on skipped COI contestant.
17. Judge submits final official scores (POST /api/judge/submissions/{sub_id}/scores).
18. Strict single-scoring rule: subsequent score submission rejected with 400.
19. Judge submits contestant feedback (POST /api/judge/{comp_id}/contestants/{sub_id}/feedback).
20. Judge progress updated (GET /api/judge/{comp_id}/my-progress).
21. Admin removes judge assignment (DELETE /api/admin/judges/assignments).
22. Admin deletes judge (DELETE /api/admin/judges/{email}) with cascade cleanup.
"""

import pytest
from datetime import datetime, timezone
from httpx import AsyncClient, ASGITransport
from bson import ObjectId

from app.main import app
from app.core.database import (
    user_collection,
    admin_collection,
    competition_collection,
    competition_registration_collection,
    competition_submission_collection,
    judge_assignment_collection,
    judge_score_collection,
    draft_score_collection,
    coi_skip_collection,
    judge_feedback_collection,
    otp_collection,
    audit_log_collection,
)
from app.core.security import create_access_token, get_password_hash


@pytest.mark.asyncio
async def test_complete_judge_flow_end_to_end():
    ts = int(datetime.now().timestamp())
    admin_email = f"admin_judge_flow_{ts}@upgradetalentsindia.in"
    judge_email = f"dr_evaluator_{ts}@upgradetalentsindia.in"
    contestant1_email = f"contestant_alpha_{ts}@upgradetalentsindia.in"
    contestant2_email = f"contestant_beta_{ts}@upgradetalentsindia.in"

    # ── 1. Setup Admin ──
    await admin_collection.insert_one({
        "email": admin_email,
        "name": "Super Admin Judge Lead",
        "role": "super_admin",
        "password_hash": get_password_hash("AdminPass123!"),
        "created_at": datetime.now(timezone.utc),
    })
    admin_token = create_access_token({"sub": admin_email, "type": "admin", "role": "super_admin"})
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # ── 2. Setup Contestant Users ──
    await user_collection.insert_many([
        {
            "email": contestant1_email,
            "name": "Rohan Sharma",
            "full_name": "Rohan Sharma",
            "phone_number": "9876543201",
            "role": "USER",
            "is_verified": True,
            "profile_photo": "https://example.com/photos/rohan.jpg",
            "created_at": datetime.now(timezone.utc),
        },
        {
            "email": contestant2_email,
            "name": "Priya Sen",
            "full_name": "Priya Sen",
            "phone_number": "9876543202",
            "role": "USER",
            "is_verified": True,
            "profile_photo": "https://example.com/photos/priya.jpg",
            "created_at": datetime.now(timezone.utc),
        }
    ])

    # ── 3. Setup Competition with Criteria ──
    comp_doc = {
        "title": f"Grand Master Art Championship {ts}",
        "description": "National classical and digital art championship",
        "category": "Visual Arts",
        "categories": [
            {"name": "Junior", "age_limit": "8-14", "price": 200.0},
            {"name": "Senior", "age_limit": "15-25", "price": 400.0},
        ],
        "judging_criteria": [
            {"name": "Creativity", "max_score": 10.0, "weight": 1.0},
            {"name": "Execution", "max_score": 10.0, "weight": 1.0},
        ],
        "judge_scoring_enabled": True,
        "judging_status": "OPEN",
        "is_published": True,
        "is_visible": True,
        "is_deleted": False,
        "start_date": "2026-11-01T00:00:00Z",
        "end_date": "2026-12-30T23:59:59Z",
        "created_at": datetime.now(timezone.utc),
    }
    comp_res = await competition_collection.insert_one(comp_doc)
    comp_id = str(comp_res.inserted_id)

    # ── 4. Seed Registrations and Submissions ──
    await competition_registration_collection.insert_many([
        {
            "competition_id": comp_id,
            "user_email": contestant1_email,
            "user_name": "Rohan Sharma",
            "registration_reference": f"CRG-ART-{ts}-001",
            "category": "Senior",
            "created_at": datetime.now(timezone.utc),
        },
        {
            "competition_id": comp_id,
            "user_email": contestant2_email,
            "user_name": "Priya Sen",
            "registration_reference": f"CRG-ART-{ts}-002",
            "category": "Junior",
            "created_at": datetime.now(timezone.utc),
        }
    ])

    sub1_res = await competition_submission_collection.insert_one({
        "competition_id": comp_id,
        "user_email": contestant1_email,
        "user_name": "Rohan Sharma",
        "title": "Sunset over Ganges",
        "submission_url": "https://example.com/art/rohan_sunset.jpg",
        "category": "Senior",
        "status": "APPROVED",
        "created_at": datetime.now(timezone.utc),
    })
    sub1_id = str(sub1_res.inserted_id)

    sub2_res = await competition_submission_collection.insert_one({
        "competition_id": comp_id,
        "user_email": contestant2_email,
        "user_name": "Priya Sen",
        "title": "Monsoon Bloom",
        "submission_url": "https://example.com/art/priya_monsoon.png",
        "category": "Junior",
        "status": "APPROVED",
        "created_at": datetime.now(timezone.utc),
    })
    sub2_id = str(sub2_res.inserted_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # ══════════════════════════════════════════════
        # STEP 1: Admin Creates Judge
        # ══════════════════════════════════════════════
        res_create = await client.post(
            "/api/admin/judges/",
            headers=admin_headers,
            json={
                "full_name": "Dr. Anindya Mukherjee",
                "email": judge_email,
                "phone_number": "9830012345",
                "designation": "Associate Professor of Fine Arts",
                "biography": "20 years of experience in judging international exhibitions.",
            }
        )
        assert res_create.status_code == 200, res_create.text
        assert res_create.json()["email"] == judge_email

        # Duplicate email must be rejected with 409
        res_dup = await client.post(
            "/api/admin/judges/",
            headers=admin_headers,
            json={
                "full_name": "Duplicate Judge",
                "email": judge_email
            }
        )
        assert res_dup.status_code == 409

        # ══════════════════════════════════════════════
        # STEP 2: Admin Lists Judges
        # ══════════════════════════════════════════════
        res_list = await client.get("/api/admin/judges/", headers=admin_headers)
        assert res_list.status_code == 200
        judges_data = res_list.json()
        target_judge = next((j for j in judges_data if j["email"] == judge_email), None)
        assert target_judge is not None
        assert target_judge["role"] == "JUDGE"
        assert target_judge["assigned_count"] == 0
        judge_db_id = target_judge["_id"]

        # ══════════════════════════════════════════════
        # STEP 3: Admin Updates Judge Details
        # ══════════════════════════════════════════════
        res_update = await client.patch(
            f"/api/admin/judges/{judge_email}",
            headers=admin_headers,
            json={
                "designation": "Dean of Visual Arts & Cultural Critic",
                "biography": "Over 25 years mentoring emerging artists.",
            }
        )
        assert res_update.status_code == 200
        assert res_update.json()["designation"] == "Dean of Visual Arts & Cultural Critic"

        # ══════════════════════════════════════════════
        # STEP 4: Unassigned Judge Security Gate
        # ══════════════════════════════════════════════
        # Generate temporary judge token before assignment
        unassigned_token = create_access_token({"sub": judge_email, "type": "user"})
        unassigned_headers = {"Authorization": f"Bearer {unassigned_token}"}

        # Attempting to fetch contestants when not assigned must return 403 Forbidden
        res_unassigned = await client.get(
            f"/api/judge/{comp_id}/contestants",
            headers=unassigned_headers
        )
        assert res_unassigned.status_code == 403
        assert "not authorized" in res_unassigned.json()["detail"].lower()

        # Judge assigned competitions list must be empty
        res_assigned_comps = await client.get("/api/judge/", headers=unassigned_headers)
        assert res_assigned_comps.status_code == 200
        assert len(res_assigned_comps.json()) == 0

        # ══════════════════════════════════════════════
        # STEP 5: Admin Assigns Judge to Competition
        # ══════════════════════════════════════════════
        res_assign = await client.post(
            "/api/admin/judges/assignments",
            headers=admin_headers,
            json={
                "judge_email": judge_email,
                "competition_id": comp_id,
            }
        )
        assert res_assign.status_code == 200
        assert "Judge assigned successfully" in res_assign.json()["message"]

        # Duplicate assignment must be rejected with 409
        res_assign_dup = await client.post(
            "/api/admin/judges/assignments",
            headers=admin_headers,
            json={
                "judge_email": judge_email,
                "competition_id": comp_id,
            }
        )
        assert res_assign_dup.status_code == 409

        # Admin checks judge assignments endpoint
        res_judge_assignments = await client.get(
            f"/api/admin/judges/{judge_email}/assignments",
            headers=admin_headers
        )
        assert res_judge_assignments.status_code == 200
        assert comp_id in res_judge_assignments.json()

        # Admin lists judges: assigned_count must now be 1
        res_list2 = await client.get("/api/admin/judges/", headers=admin_headers)
        target_judge2 = next((j for j in res_list2.json() if j["email"] == judge_email), None)
        assert target_judge2["assigned_count"] == 1
        assert comp_id in target_judge2["assigned_competitions"]

        # ══════════════════════════════════════════════
        # STEP 6: Judge Authentication (OTP + Set Password)
        # ══════════════════════════════════════════════
        # Step 6a: Judge requests OTP
        res_otp_step1 = await client.post("/api/judge/auth/login/step1", json={"email": judge_email})
        assert res_otp_step1.status_code == 200
        assert "OTP sent" in res_otp_step1.json()["message"]

        # Fetch OTP code from database
        otp_record = await otp_collection.find_one({"email": judge_email})
        assert otp_record is not None
        otp_val = otp_record["otp"]

        # Step 6b: Judge verifies OTP
        res_otp_step2 = await client.post("/api/judge/auth/login/step2", json={
            "email": judge_email,
            "otp": otp_val
        })
        assert res_otp_step2.status_code == 200
        step2_data = res_otp_step2.json()
        assert step2_data["role"] == "JUDGE"
        assert step2_data["has_password"] is False
        judge_access_token = step2_data["access_token"]
        judge_auth_headers = {"Authorization": f"Bearer {judge_access_token}"}

        # Step 6c: Judge sets permanent password
        res_set_pw = await client.post(
            "/api/judge/auth/set-password",
            headers=judge_auth_headers,
            json={
                "password": "MasterJudge2026#Secure",
                "confirm_password": "MasterJudge2026#Secure",
            }
        )
        assert res_set_pw.status_code == 200
        assert res_set_pw.json()["has_password"] is True

        # Step 6d: Subsequent login directly using Password
        res_pw_login = await client.post(
            "/api/judge/auth/login",
            json={
                "email": judge_email,
                "password": "MasterJudge2026#Secure",
            }
        )
        assert res_pw_login.status_code == 200
        pw_login_data = res_pw_login.json()
        assert pw_login_data["role"] == "JUDGE"
        assert pw_login_data["has_password"] is True
        active_judge_token = pw_login_data["access_token"]
        active_judge_headers = {"Authorization": f"Bearer {active_judge_token}"}

        # ══════════════════════════════════════════════
        # STEP 7: Judge Fetches Assigned Competitions
        # ══════════════════════════════════════════════
        res_judge_comps = await client.get("/api/judge/", headers=active_judge_headers)
        assert res_judge_comps.status_code == 200
        assigned_comp_ids = [c["_id"] for c in res_judge_comps.json()]
        assert comp_id in assigned_comp_ids

        # ══════════════════════════════════════════════
        # STEP 8: Judge Lists Contestants
        # ══════════════════════════════════════════════
        res_contestants = await client.get(
            f"/api/judge/{comp_id}/contestants",
            headers=active_judge_headers
        )
        assert res_contestants.status_code == 200, res_contestants.text
        contestants_list = res_contestants.json()
        assert len(contestants_list) >= 2

        c1 = next((c for c in contestants_list if c["user_email"] == contestant1_email), None)
        assert c1 is not None
        assert c1["user_name"] == "Rohan Sharma"
        assert "CRG-ART-" in c1["registration_reference"]
        assert c1["is_scored"] is False
        assert c1["is_locked"] is False
        assert "rohan_sunset.jpg" in c1["submission_url"]

        c2 = next((c for c in contestants_list if c["user_email"] == contestant2_email), None)
        assert c2 is not None
        assert c2["user_name"] == "Priya Sen"

        # Check initial progress
        res_prog1 = await client.get(f"/api/judge/{comp_id}/my-progress", headers=active_judge_headers)
        assert res_prog1.status_code == 200
        prog1 = res_prog1.json()
        assert prog1["total_contestants"] >= 2
        assert prog1["scored"] == 0
        assert prog1["skipped_coi"] == 0

        # ══════════════════════════════════════════════
        # STEP 9: Judge Saves & Retrieves Draft Score
        # ══════════════════════════════════════════════
        res_draft = await client.post(
            f"/api/judge/{comp_id}/contestants/{sub1_id}/draft",
            headers=active_judge_headers,
            json={
                "scores": {"Creativity": 8.0, "Execution": 7.5},
                "comments": "Great brushwork, drafting final evaluation."
            }
        )
        assert res_draft.status_code == 200
        assert "Draft saved" in res_draft.json()["message"]

        res_get_draft = await client.get(
            f"/api/judge/{comp_id}/contestants/{sub1_id}/draft",
            headers=active_judge_headers
        )
        assert res_get_draft.status_code == 200
        assert res_get_draft.json()["scores"]["Creativity"] == 8.0

        # ══════════════════════════════════════════════
        # STEP 10: Judge Declares Conflict of Interest (COI) Skip
        # ══════════════════════════════════════════════
        res_skip = await client.post(
            f"/api/judge/{comp_id}/contestants/{sub2_id}/skip",
            headers=active_judge_headers,
            json={
                "reason": "Personal acquaintance / past student",
                "notes": "Student attended my workshop in 2024."
            }
        )
        assert res_skip.status_code == 200
        assert "Conflict of Interest" in res_skip.json()["message"]

        # Attempting to score a skipped contestant must be rejected
        res_score_skipped = await client.post(
            f"/api/judge/submissions/{sub2_id}/scores",
            headers=active_judge_headers,
            json={"scores": {"Creativity": 9.0, "Execution": 9.0}}
        )
        assert res_score_skipped.status_code == 400
        assert "COI" in res_score_skipped.json()["detail"] or "conflict" in res_score_skipped.json()["detail"].lower()

        # ══════════════════════════════════════════════
        # STEP 11: Judge Submits Final Official Score
        # ══════════════════════════════════════════════
        res_score = await client.post(
            f"/api/judge/submissions/{sub1_id}/scores",
            headers=active_judge_headers,
            json={
                "scores": {"Creativity": 9.5, "Execution": 9.0},
                "comments": "Masterful depiction of light and water reflection."
            }
        )
        assert res_score.status_code == 200
        score_resp = res_score.json()
        assert score_resp["total_score"] == 18.5
        assert score_resp["is_locked"] is True

        # Verify draft was cleaned up
        draft_in_db = await draft_score_collection.find_one({
            "competition_id": comp_id,
            "submission_id": sub1_id,
            "judge_email": judge_email
        })
        assert draft_in_db is None

        # ══════════════════════════════════════════════
        # STEP 12: Strict Single-Score Enforcement
        # ══════════════════════════════════════════════
        res_score_dup = await client.post(
            f"/api/judge/submissions/{sub1_id}/scores",
            headers=active_judge_headers,
            json={
                "scores": {"Creativity": 10.0, "Execution": 10.0}
            }
        )
        assert res_score_dup.status_code == 400
        assert "already submitted" in res_score_dup.json()["detail"].lower()

        # ══════════════════════════════════════════════
        # STEP 13: Judge Submits Feedback
        # ══════════════════════════════════════════════
        res_fb = await client.post(
            f"/api/judge/{comp_id}/contestants/{sub1_id}/feedback",
            headers=active_judge_headers,
            json={
                "rating": 5,
                "feedback_text": "Exceptional talent! Explore larger canvas sizes next time."
            }
        )
        assert res_fb.status_code == 200
        assert "Feedback submitted" in res_fb.json()["message"]

        # ══════════════════════════════════════════════
        # STEP 14: Judge Progress Updated
        # ══════════════════════════════════════════════
        res_prog2 = await client.get(f"/api/judge/{comp_id}/my-progress", headers=active_judge_headers)
        assert res_prog2.status_code == 200
        prog2 = res_prog2.json()
        assert prog2["scored"] == 1
        assert prog2["skipped_coi"] == 1
        assert prog2["pending"] == 0
        assert prog2["completion_pct"] == 100.0

        # Contestants list reflects scored state
        res_contestants_after = await client.get(
            f"/api/judge/{comp_id}/contestants",
            headers=active_judge_headers
        )
        c1_after = next((c for c in res_contestants_after.json() if c["user_email"] == contestant1_email), None)
        assert c1_after["is_scored"] is True
        assert c1_after["total_score"] == 18.5
        assert c1_after["is_locked"] is True

        c2_after = next((c for c in res_contestants_after.json() if c["user_email"] == contestant2_email), None)
        assert c2_after["is_skipped"] is True
        assert c2_after["status"] == "Skipped"

        # ══════════════════════════════════════════════
        # STEP 15: Admin Removes Judge Assignment
        # ══════════════════════════════════════════════
        res_unassign = await client.request(
            "DELETE",
            "/api/admin/judges/assignments",
            headers=admin_headers,
            json={
                "judge_email": judge_email,
                "competition_id": comp_id,
            }
        )
        assert res_unassign.status_code == 200
        assert "removed successfully" in res_unassign.json()["message"]

        # Now judge access is revoked (403)
        res_revoked = await client.get(
            f"/api/judge/{comp_id}/contestants",
            headers=active_judge_headers
        )
        assert res_revoked.status_code == 403

        # ══════════════════════════════════════════════
        # STEP 16: Admin Deletes Judge (Cascade Cleanup)
        # ══════════════════════════════════════════════
        res_delete_judge = await client.delete(
            f"/api/admin/judges/{judge_email}",
            headers=admin_headers
        )
        assert res_delete_judge.status_code == 200
        assert "deleted successfully" in res_delete_judge.json()["message"]

        # Verify judge is gone from users table
        judge_in_db = await user_collection.find_one({"email": judge_email})
        assert judge_in_db is None

        # Verify get judge returns 404
        res_get_deleted = await client.get(
            f"/api/admin/judges/{judge_email}",
            headers=admin_headers
        )
        assert res_get_deleted.status_code == 404

