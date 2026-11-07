"""
test_multi_round_competition_flow.py
====================================
End-to-End Test Suite for Multi-Round Competition Flow:
1. Admin creates competition with 3 rounds:
   - Round 1: Preliminary (100 contestants -> Top 2 advance)
   - Round 2: Semi-Final (Top 1 advances to Grand Finale)
   - Round 3: Grand Finale (Championship Winner)
2. Four contestants register: Contestant A, B, C, D (all qualified for Round 1).
3. Attendance is marked present (works for both offline & online).
4. Round 1 Evaluation:
   - Judge evaluates and scores all 4 contestants.
   - Audience casts votes.
   - Separate Round 1 Leaderboard is verified (1st, 2nd, 3rd, 4th). Top 2 have is_advancing = True.
5. Admin advances top 2 (Contestant A and B) to Round 2:
   - Contestants A and B receive qualified_rounds=[1, 2], current_round=2, round_status="QUALIFIED".
   - Contestants C and D are marked round_status="ELIMINATED".
   - Competition current_round switches to 2.
6. Offline stage integration:
   - Live stage performance queue strictly filters to Round 2 qualified contestants (Contestant A and B).
   - Eliminated contestants (C and D) are excluded from the stage queue.
7. Round 2 Evaluation:
   - Judge scores Contestants A and B in Round 2.
   - Contestant B outperforms Contestant A in Round 2.
   - Separate Round 2 Leaderboard is verified (Contestant B is 1st, Contestant A is 2nd).
   - Independent Round 1 Leaderboard remains intact (Contestant A is 1st, Contestant B is 2nd in Round 1).
8. Admin advances top 1 from Round 2 to Round 3 (Grand Finale):
   - Contestant B qualifies for the Grand Finale.
   - Contestant B is crowned champion!
9. Teardown and cleanup.
"""

import pytest
from datetime import datetime, timezone, timedelta
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
    audience_vote_collection,
    audit_log_collection,
)
from app.core.security import create_access_token, get_password_hash


@pytest.mark.asyncio
async def test_multi_round_competition_flow():
    ts = int(datetime.now().timestamp())
    now = datetime.now(timezone.utc)
    future = now + timedelta(days=30)
    now_iso = now.isoformat()
    future_iso = future.isoformat()

    admin_email = f"admin_rounds_{ts}@upgradetalentsindia.in"
    judge_email = f"judge_rounds_{ts}@upgradetalentsindia.in"
    voter_email = f"voter_rounds_{ts}@upgradetalentsindia.in"

    user_a = f"contestant_a_{ts}@upgradetalentsindia.in"
    user_b = f"contestant_b_{ts}@upgradetalentsindia.in"
    user_c = f"contestant_c_{ts}@upgradetalentsindia.in"
    user_d = f"contestant_d_{ts}@upgradetalentsindia.in"

    created_comp_id = None

    try:
        # ── 1. Create Admin & Judge & Voter ──
        pwd_hash = get_password_hash("SecretPassword123!")

        await admin_collection.insert_one({
            "email": admin_email,
            "full_name": "Rounds Super Admin",
            "name": "Rounds Super Admin",
            "role": "super_admin",
            "is_active": True,
            "password_hash": pwd_hash,
            "created_at": now
        })
        admin_token = create_access_token({"sub": admin_email, "type": "admin", "role": "super_admin"})
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        judge_insert = await user_collection.insert_one({
            "email": judge_email,
            "full_name": "Chief Maestro Judge",
            "name": "Chief Maestro Judge",
            "role": "JUDGE",
            "is_active": True,
            "is_verified": True,
            "created_at": now
        })
        judge_token = create_access_token({"sub": judge_email, "type": "judge", "role": "JUDGE"})
        judge_headers = {"Authorization": f"Bearer {judge_token}"}

        voter_insert = await user_collection.insert_one({
            "email": voter_email,
            "name": "Super Fan Audience",
            "full_name": "Super Fan Audience",
            "phone_number": "9876543210",
            "parent_phone_number": "9876543211",
            "date_of_birth": "1998-05-15",
            "address": "123 Fan Street, Mumbai",
            "role": "USER",
            "is_active": True,
            "created_at": now
        })
        voter_token = create_access_token({"sub": voter_email, "role": "USER"})
        voter_headers = {"Authorization": f"Bearer {voter_token}"}

        # Create contestants
        contestants = [
            (user_a, "Contestant Alice", "Senior"),
            (user_b, "Contestant Bob", "Senior"),
            (user_c, "Contestant Charlie", "Junior"),
            (user_d, "Contestant David", "Junior"),
        ]
        for em, name, cat in contestants:
            await user_collection.insert_one({
                "email": em,
                "full_name": name,
                "role": "USER",
                "is_active": True,
                "is_verified": True,
                "created_at": now
            })

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test", follow_redirects=True) as ac:

            # ── 2. Create Multi-Round Offline Competition ──
            create_payload = {
                "title": f"India Talent Championship {ts}",
                "description": "Multi-round knockout championship from preliminary to grand finale",
                "category": "Singing",
                "is_offline": True,
                "event_mode": "OFFLINE",
                "venue": "National Performing Arts Center, Mumbai",
                "start_date": now_iso,
                "end_date": future_iso,
                "registration_start_date": now_iso,
                "registration_end_date": future_iso,
                "submission_deadline": future_iso,
                "max_participants": 200,
                "is_published": True,
                "current_round": 1,
                "rounds": [
                    {
                        "round_number": 1,
                        "name": "Round 1 - Preliminary Auditions",
                        "description": "Initial screening of all participants. Top 2 advance.",
                        "status": "ACTIVE",
                        "advancing_count": 2,
                        "is_final_round": False
                    },
                    {
                        "round_number": 2,
                        "name": "Round 2 - Semi-Final Battle",
                        "description": "Top performers battle for the grand championship spot.",
                        "status": "UPCOMING",
                        "advancing_count": 1,
                        "is_final_round": False
                    },
                    {
                        "round_number": 3,
                        "name": "Round 3 - Grand Finale",
                        "description": "Final championship performance for the national trophy.",
                        "status": "UPCOMING",
                        "advancing_count": None,
                        "is_final_round": True
                    }
                ],
                "judging_criteria": [
                    {"name": "Vocal Accuracy", "max_score": 50},
                    {"name": "Stage Presence", "max_score": 50}
                ],
                "ranking_mode": "HYBRID",
                "weight_judge": 0.8,
                "weight_audience": 0.2,
                "judge_scoring_enabled": True,
                "judging_status": "OPEN",
                "audience_voting_enabled": True,
                "voting_status": "OPEN",
                "judge_score_weight": 0.8,
                "audience_vote_weight": 0.2,
                "categories": [
                    {"name": "Senior", "price": 500, "age_limit": "18-50"},
                    {"name": "Junior", "price": 300, "age_limit": "5-17"}
                ]
            }

            resp = await ac.post(
                "/api/admin/advanced-competitions/",
                json=create_payload,
                headers=admin_headers
            )
            assert resp.status_code == 200, f"Create competition failed: {resp.text}"
            comp_data = resp.json()
            created_comp_id = str(comp_data.get("_id") or comp_data.get("id"))
            assert created_comp_id, "Missing competition ID"

            # Assign judge
            await judge_assignment_collection.insert_one({
                "competition_id": created_comp_id,
                "judge_email": judge_email,
                "assigned_at": now
            })

            # ── 3. Register Contestants for Round 1 & Mark Attendance ──
            for idx, (em, name, cat) in enumerate(contestants):
                await competition_registration_collection.insert_one({
                    "competition_id": created_comp_id,
                    "user_email": em,
                    "email": em,
                    "participant_name": name,
                    "user_name": name,
                    "category": cat,
                    "stage_order": idx + 1,
                    "stage_status": "NOT_STARTED",
                    "attendance_status": "PRESENT",
                    "is_present": True,
                    "registered_at": now,
                    "current_round": 1,
                    "qualified_rounds": [1],
                    "highest_round": 1,
                    "round_status": "QUALIFIED"
                })

            # Verify Admin views rounds
            r_resp = await ac.get(f"/api/admin/advanced-competitions/{created_comp_id}/rounds", headers=admin_headers)
            assert r_resp.status_code == 200
            rounds_info = r_resp.json()
            assert rounds_info["total_rounds"] == 3
            assert rounds_info["current_round"] == 1
            assert len(rounds_info["rounds"]) == 3
            assert rounds_info["rounds"][0]["status"] == "ACTIVE"
            assert rounds_info["rounds"][0]["advancing_count"] == 2
            assert rounds_info["rounds"][0]["qualified_contestants_count"] == 4

            # Verify Public views rounds
            pub_r = await ac.get(f"/api/v1/competitions/{created_comp_id}/rounds")
            assert pub_r.status_code == 200
            assert len(pub_r.json()["rounds"]) == 3

            # ── 4. Round 1 Evaluation: Judge Scores all 4 Contestants ──
            # Contestant A gets 95 (Alice)
            # Contestant B gets 85 (Bob)
            # Contestant C gets 75 (Charlie)
            # Contestant D gets 60 (David)
            scores_map = {
                user_a: {"Vocal Accuracy": 48, "Stage Presence": 47}, # 95
                user_b: {"Vocal Accuracy": 43, "Stage Presence": 42}, # 85
                user_c: {"Vocal Accuracy": 38, "Stage Presence": 37}, # 75
                user_d: {"Vocal Accuracy": 30, "Stage Presence": 30}, # 60
            }

            for em, score_dict in scores_map.items():
                s_resp = await ac.post(
                    f"/api/judge/{created_comp_id}/scores",
                    json={
                        "participant_id": em,
                        "scores": score_dict,
                        "feedback": f"Solid performance in Round 1 by {em}",
                        "round_number": 1
                    },
                    headers=judge_headers
                )
                assert s_resp.status_code == 200, f"Judge score failed for {em}: {s_resp.text}"

            # Voter votes for Contestant A in Round 1
            v_resp = await ac.post(
                f"/api/v1/competitions/{created_comp_id}/vote",
                json={
                    "participant_id": user_a,
                    "round_number": 1,
                    "source": "WEBSITE"
                },
                headers=voter_headers
            )
            assert v_resp.status_code == 200

            # ── 5. Verify Round 1 Leaderboard ──
            lb1_resp = await ac.get(
                f"/api/admin/advanced-competitions/{created_comp_id}/rounds/1/leaderboard",
                headers=admin_headers
            )
            assert lb1_resp.status_code == 200
            lb1 = lb1_resp.json()
            assert lb1["round_number"] == 1
            assert lb1["total_participants"] == 4
            assert len(lb1["leaderboard"]) == 4

            # Rank 1 must be Alice (Contestant A)
            assert lb1["leaderboard"][0]["email"] == user_a
            assert lb1["leaderboard"][0]["rank"] == 1
            assert lb1["leaderboard"][0]["is_advancing"] is True

            # Rank 2 must be Bob (Contestant B)
            assert lb1["leaderboard"][1]["email"] == user_b
            assert lb1["leaderboard"][1]["rank"] == 2
            assert lb1["leaderboard"][1]["is_advancing"] is True

            # Rank 3 must be Charlie (Contestant C)
            assert lb1["leaderboard"][2]["email"] == user_c
            assert lb1["leaderboard"][2]["rank"] == 3
            assert lb1["leaderboard"][2]["is_advancing"] is False

            # Rank 4 must be David (Contestant D)
            assert lb1["leaderboard"][3]["email"] == user_d
            assert lb1["leaderboard"][3]["rank"] == 4
            assert lb1["leaderboard"][3]["is_advancing"] is False

            # Public round 1 leaderboard check
            pub_lb1 = await ac.get(f"/api/v1/competitions/{created_comp_id}/rounds/1/leaderboard")
            assert pub_lb1.status_code == 200
            assert pub_lb1.json()["leaderboard"][0]["email"] == user_a

            # ── 6. Admin Advances Top 2 Contestants to Round 2 ──
            adv_resp = await ac.post(
                f"/api/admin/advanced-competitions/{created_comp_id}/rounds/1/advance",
                json={
                    "advancing_count": 2,
                    "notes": "Top 2 performers advanced to Round 2 Semi-Finals based on Round 1 rankings"
                },
                headers=admin_headers
            )
            assert adv_resp.status_code == 200, f"Advancement failed: {adv_resp.text}"
            adv_data = adv_resp.json()
            assert adv_data["from_round"] == 1
            assert adv_data["next_round"] == 2
            assert adv_data["advanced_count"] == 2
            assert adv_data["eliminated_count"] == 2
            assert user_a in adv_data["advanced_contestant_ids"]
            assert user_b in adv_data["advanced_contestant_ids"]

            # Verify Round 2 is now ACTIVE, Round 1 is COMPLETED
            r2_check = await ac.get(f"/api/admin/advanced-competitions/{created_comp_id}/rounds", headers=admin_headers)
            assert r2_check.status_code == 200
            r2_info = r2_check.json()
            assert r2_info["current_round"] == 2
            assert r2_info["rounds"][0]["status"] == "COMPLETED"
            assert r2_info["rounds"][1]["status"] == "ACTIVE"
            assert r2_info["rounds"][1]["qualified_contestants_count"] == 2

            # Check contestants in Round 2
            c2_resp = await ac.get(f"/api/admin/advanced-competitions/{created_comp_id}/rounds/2/contestants", headers=admin_headers)
            assert c2_resp.status_code == 200
            c2_list = c2_resp.json()["contestants"]
            assert len(c2_list) == 2
            c2_emails = [c["user_email"] for c in c2_list]
            assert user_a in c2_emails
            assert user_b in c2_emails
            assert user_c not in c2_emails
            assert user_d not in c2_emails

            # ── 7. Offline Stage Integration: Live Stage Queue only picks Round 2 qualified contestants! ──
            next_stage_resp = await ac.post(
                f"/api/admin/advanced-competitions/{created_comp_id}/stage/next-performer",
                headers=admin_headers
            )
            assert next_stage_resp.status_code == 200
            stage_data = next_stage_resp.json()
            assert stage_data["has_performer"] is True
            # The performer on stage must be either Contestant A or Contestant B, NEVER C or D
            curr_stage_email = stage_data["current_performer"]["user_email"]
            assert curr_stage_email in [user_a, user_b], f"Unexpected performer: {curr_stage_email}"

            # ── 8. Round 2 Evaluation: Judge Scores Contestants in Round 2 ──
            # In Round 2, Bob (user_b) shines with 99, Alice (user_a) gets 88
            s2_bob = await ac.post(
                f"/api/judge/{created_comp_id}/scores",
                json={
                    "participant_id": user_b,
                    "scores": {"Vocal Accuracy": 50, "Stage Presence": 49}, # 99
                    "feedback": "Spectacular vocal agility in Round 2!",
                    "round_number": 2
                },
                headers=judge_headers
            )
            assert s2_bob.status_code == 200

            s2_alice = await ac.post(
                f"/api/judge/{created_comp_id}/scores",
                json={
                    "participant_id": user_a,
                    "scores": {"Vocal Accuracy": 44, "Stage Presence": 44}, # 88
                    "feedback": "Graceful performance in Round 2.",
                    "round_number": 2
                },
                headers=judge_headers
            )
            assert s2_alice.status_code == 200

            # ── 9. Verify Independent Round 2 Leaderboard vs Round 1 Leaderboard ──
            # In Round 2 Leaderboard:
            # Bob (user_b) is #1
            # Alice (user_a) is #2
            lb2_resp = await ac.get(
                f"/api/admin/advanced-competitions/{created_comp_id}/rounds/2/leaderboard",
                headers=admin_headers
            )
            assert lb2_resp.status_code == 200
            lb2 = lb2_resp.json()
            assert lb2["round_number"] == 2
            assert lb2["total_participants"] == 2
            assert lb2["leaderboard"][0]["email"] == user_b
            assert lb2["leaderboard"][0]["rank"] == 1
            assert lb2["leaderboard"][0]["is_advancing"] is True  # advancing_count was 1
            assert lb2["leaderboard"][1]["email"] == user_a
            assert lb2["leaderboard"][1]["rank"] == 2
            assert lb2["leaderboard"][1]["is_advancing"] is False

            # In Round 1 Leaderboard:
            # Alice (user_a) is STILL #1
            # Bob (user_b) is STILL #2
            lb1_recheck = await ac.get(
                f"/api/admin/advanced-competitions/{created_comp_id}/rounds/1/leaderboard",
                headers=admin_headers
            )
            assert lb1_recheck.status_code == 200
            assert lb1_recheck.json()["leaderboard"][0]["email"] == user_a
            assert lb1_recheck.json()["leaderboard"][1]["email"] == user_b

            # ── 10. Advance Top 1 to Round 3 (Grand Finale) ──
            adv2_resp = await ac.post(
                f"/api/admin/advanced-competitions/{created_comp_id}/rounds/2/advance",
                json={
                    "advancing_count": 1,
                    "notes": "Bob advances to Round 3 Grand Finale"
                },
                headers=admin_headers
            )
            assert adv2_resp.status_code == 200
            adv2_data = adv2_resp.json()
            assert adv2_data["from_round"] == 2
            assert adv2_data["next_round"] == 3
            assert adv2_data["advanced_count"] == 1
            assert adv2_data["advanced_contestant_ids"] == [user_b]

            # In Round 3, Bob is the finalist
            c3_resp = await ac.get(f"/api/admin/advanced-competitions/{created_comp_id}/rounds/3/contestants", headers=admin_headers)
            assert c3_resp.status_code == 200
            c3_list = c3_resp.json()["contestants"]
            assert len(c3_list) == 1
            assert c3_list[0]["user_email"] == user_b

            # Crown Bob in Round 3 Grand Finale
            s3_bob = await ac.post(
                f"/api/judge/{created_comp_id}/scores",
                json={
                    "participant_id": user_b,
                    "scores": {"Vocal Accuracy": 50, "Stage Presence": 50}, # 100
                    "feedback": "Flawless Grand Finale performance! Champion!",
                    "round_number": 3
                },
                headers=judge_headers
            )
            assert s3_bob.status_code == 200

            lb3_resp = await ac.get(
                f"/api/v1/competitions/{created_comp_id}/rounds/3/leaderboard"
            )
            assert lb3_resp.status_code == 200
            lb3 = lb3_resp.json()
            assert lb3["round_number"] == 3
            assert lb3["is_final_round"] is True
            assert len(lb3["leaderboard"]) == 1
            assert lb3["leaderboard"][0]["email"] == user_b
            assert lb3["leaderboard"][0]["rank"] == 1

    finally:
        # ── Cleanup ──
        if created_comp_id:
            c_oid = ObjectId(created_comp_id) if ObjectId.is_valid(created_comp_id) else None
            q = {"$in": [created_comp_id, c_oid]} if c_oid else created_comp_id
            await competition_collection.delete_many({"_id": q})
            await competition_registration_collection.delete_many({"competition_id": q})
            await competition_submission_collection.delete_many({"competition_id": q})
            await judge_assignment_collection.delete_many({"competition_id": q})
            await judge_score_collection.delete_many({"competition_id": q})
            await audience_vote_collection.delete_many({"competition_id": q})

        all_test_emails = [admin_email, judge_email, voter_email, user_a, user_b, user_c, user_d]
        await admin_collection.delete_many({"email": {"$in": all_test_emails}})
        await user_collection.delete_many({"email": {"$in": all_test_emails}})
