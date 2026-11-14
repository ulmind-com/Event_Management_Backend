"""
test_attendance_order_flow.py
==============================
Validates that when admin takes participant attendance, the new present list
(final_present_list, stage lineup, public attendance list, and judge final contestants)
strictly places the participant who gave attendance FIRST at the very top (first position),
even if they registered later than other participants.
"""

import pytest
import asyncio
from datetime import datetime, timezone, timedelta
from httpx import AsyncClient, ASGITransport
from bson import ObjectId

from app.main import app
from app.core.database import (
    user_collection,
    admin_collection,
    competition_collection,
    competition_registration_collection,
    judge_assignment_collection,
)
from app.core.security import create_access_token, get_password_hash


@pytest.mark.asyncio
async def test_first_attendance_is_first_in_list():
    ts = int(datetime.now().timestamp())
    now = datetime.now(timezone.utc)
    future = now + timedelta(days=10)

    admin_email = f"admin_order_{ts}@test.com"
    judge_email = f"judge_order_{ts}@test.com"
    user_a_email = f"user_a_reg_first_{ts}@test.com"
    user_b_email = f"user_b_reg_second_{ts}@test.com"
    user_c_email = f"user_c_reg_third_{ts}@test.com"

    created_comp_id = None

    try:
        # 1. Setup Admin & Judge
        await admin_collection.insert_one({
            "email": admin_email,
            "name": "Admin Tester",
            "role": "super_admin",
            "password_hash": get_password_hash("Pass123!"),
            "created_at": now,
        })
        admin_token = create_access_token({"sub": admin_email, "type": "admin", "role": "super_admin"})
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        await user_collection.insert_many([
            {
                "email": judge_email,
                "name": "Judge One",
                "role": "JUDGE",
                "password_hash": get_password_hash("Pass123!"),
                "is_verified": True,
                "created_at": now,
            },
            {
                "email": user_a_email,
                "name": "User A (Registered First)",
                "role": "USER",
                "created_at": now - timedelta(hours=3),
            },
            {
                "email": user_b_email,
                "name": "User B (Registered Second)",
                "role": "USER",
                "created_at": now - timedelta(hours=2),
            },
            {
                "email": user_c_email,
                "name": "User C (Registered Third)",
                "role": "USER",
                "created_at": now - timedelta(hours=1),
            },
        ])
        judge_token = create_access_token({"sub": judge_email, "type": "judge", "role": "JUDGE"})
        judge_headers = {"Authorization": f"Bearer {judge_token}"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            # 2. Create Offline Competition
            comp_payload = {
                "title": f"Attendance Order Championship {ts}",
                "description": "Validates first-to-attend is first-in-list",
                "category": "Singing",
                "event_mode": "OFFLINE",
                "is_offline": True,
                "venue": "Science City Auditorium, Kolkata",
                "start_date": now.isoformat(),
                "end_date": future.isoformat(),
                "registration_start_date": now.isoformat(),
                "registration_end_date": future.isoformat(),
                "submission_deadline": future.isoformat(),
                "registration_fee": 0,
                "max_participants": 50,
                "categories": [
                    {"name": "Solo", "age_limit": "12-30", "price": 0.0}
                ],
                "audience_voting_enabled": False,
                "judge_scoring_enabled": True,
                "judging_status": "OPEN",
                "is_published": True,
                "judging_criteria": [
                    {"name": "Skill", "max_score": 100, "weight": 1.0}
                ]
            }
            comp_res = await client.post("/api/admin/advanced-competitions/", json=comp_payload, headers=admin_headers)
            assert comp_res.status_code == 200, f"Create comp failed: {comp_res.text}"
            comp_data = comp_res.json()
            created_comp_id = str(comp_data.get("_id") or comp_data.get("id"))

            # Assign Judge
            await client.post(
                "/api/admin/judges/assignments",
                json={"judge_email": judge_email, "competition_id": created_comp_id},
                headers=admin_headers
            )

            # 3. Register Participants chronologically in DB:
            # User A registered 3 hours ago
            r_a = await competition_registration_collection.insert_one({
                "competition_id": created_comp_id,
                "user_email": user_a_email,
                "user_name": "User A (Registered First)",
                "category": "Solo",
                "registration_reference": f"CRG-A-{ts}",
                "status": "CONFIRMED",
                "payment_status": "PAID",
                "payment_method": "ONLINE",
                "stage_status": "NOT_STARTED",
                "stage_order": 0,
                "is_present": False,
                "attendance_status": "ABSENT",
                "registered_at": now - timedelta(hours=3),
            })
            reg_a_id = str(r_a.inserted_id)

            # User B registered 2 hours ago
            r_b = await competition_registration_collection.insert_one({
                "competition_id": created_comp_id,
                "user_email": user_b_email,
                "user_name": "User B (Registered Second)",
                "category": "Solo",
                "registration_reference": f"CRG-B-{ts}",
                "status": "CONFIRMED",
                "payment_status": "PAID",
                "payment_method": "ONLINE",
                "stage_status": "NOT_STARTED",
                "stage_order": 0,
                "is_present": False,
                "attendance_status": "ABSENT",
                "registered_at": now - timedelta(hours=2),
            })
            reg_b_id = str(r_b.inserted_id)

            # User C registered 1 hour ago
            r_c = await competition_registration_collection.insert_one({
                "competition_id": created_comp_id,
                "user_email": user_c_email,
                "user_name": "User C (Registered Third)",
                "category": "Solo",
                "registration_reference": f"CRG-C-{ts}",
                "status": "CONFIRMED",
                "payment_status": "PAID",
                "payment_method": "ONLINE",
                "stage_status": "NOT_STARTED",
                "stage_order": 0,
                "is_present": False,
                "attendance_status": "ABSENT",
                "registered_at": now - timedelta(hours=1),
            })
            reg_c_id = str(r_c.inserted_id)

            # 4. On Event Day: User C gives attendance FIRST!
            mark_c = await client.patch(
                f"/api/admin/advanced-competitions/{created_comp_id}/participants/{reg_c_id}/attendance",
                json={"is_present": True, "attendance_status": "PRESENT", "notes": "C arrived first"},
                headers=admin_headers
            )
            assert mark_c.status_code == 200
            assert mark_c.json()["is_present"] is True

            await asyncio.sleep(0.05)

            # Then User A gives attendance SECOND!
            mark_a = await client.patch(
                f"/api/admin/advanced-competitions/{created_comp_id}/participants/{reg_a_id}/attendance",
                json={"is_present": True, "attendance_status": "PRESENT", "notes": "A arrived second"},
                headers=admin_headers
            )
            assert mark_a.status_code == 200
            assert mark_a.json()["is_present"] is True

            # User B remains ABSENT

            # ── 5. Verify Admin Attendance Dashboard: final_present_list ──
            # User C MUST BE FIRST in final_present_list!
            # User A MUST BE SECOND in final_present_list!
            dash_res = await client.get(
                f"/api/admin/advanced-competitions/{created_comp_id}/attendance",
                headers=admin_headers
            )
            assert dash_res.status_code == 200
            dash_data = dash_res.json()
            assert dash_data["present_count"] == 2
            assert dash_data["absent_count"] == 1
            present_list = dash_data["final_present_list"]
            assert len(present_list) == 2

            assert present_list[0]["user_email"] == user_c_email, (
                f"Expected User C (first to attend) at index 0, got {present_list[0]['user_email']}"
            )
            assert present_list[0]["stage_order"] == 1

            assert present_list[1]["user_email"] == user_a_email, (
                f"Expected User A (second to attend) at index 1, got {present_list[1]['user_email']}"
            )
            assert present_list[1]["stage_order"] == 2

            # ── 6. Verify Public Final Lineup ──
            pub_res = await client.get(f"/api/public/competitions/{created_comp_id}/final-participants")
            assert pub_res.status_code == 200
            pub_data = pub_res.json()
            pub_list = pub_data["final_participants"]
            assert len(pub_list) == 2
            assert pub_list[0]["user_email"] == user_c_email, (
                f"Public list: expected User C first, got {pub_list[0]['user_email']}"
            )
            assert pub_list[0]["stage_order"] == 1
            assert pub_list[1]["user_email"] == user_a_email
            assert pub_list[1]["stage_order"] == 2

            # ── 7. Verify Judge Final Lineup ──
            judge_res = await client.get(
                f"/api/judge/{created_comp_id}/final-contestants",
                headers=judge_headers
            )
            assert judge_res.status_code == 200
            judge_list = judge_res.json()
            assert len(judge_list) == 2
            assert judge_list[0]["user_email"] == user_c_email, (
                f"Judge list: expected User C first, got {judge_list[0]['user_email']}"
            )
            assert judge_list[1]["user_email"] == user_a_email

            # ── 8. Verify Admin Stage Dashboard Lineup ──
            stage_res = await client.get(
                f"/api/admin/advanced-competitions/{created_comp_id}/stage",
                headers=admin_headers
            )
            assert stage_res.status_code == 200
            stage_data = stage_res.json()
            stage_lineup = stage_data["final_present_list"]
            assert len(stage_lineup) == 2
            assert stage_lineup[0]["user_email"] == user_c_email
            assert stage_lineup[1]["user_email"] == user_a_email

            # ── 9. Verify Live Stage Advances: User C called first! ──
            adv1 = await client.post(
                f"/api/admin/advanced-competitions/{created_comp_id}/stage/next-performer",
                headers=admin_headers
            )
            assert adv1.status_code == 200
            assert adv1.json()["current_performer"]["user_email"] == user_c_email, (
                f"Stage advance 1: expected User C on stage first, got {adv1.json()['current_performer']['user_email']}"
            )

            # Advance to next performer: User A called second!
            adv2 = await client.post(
                f"/api/admin/advanced-competitions/{created_comp_id}/stage/next-performer",
                headers=admin_headers
            )
            assert adv2.status_code == 200
            assert adv2.json()["current_performer"]["user_email"] == user_a_email, (
                f"Stage advance 2: expected User A on stage second, got {adv2.json()['current_performer']['user_email']}"
            )

    finally:
        # Teardown
        if created_comp_id:
            c_oids = [created_comp_id]
            if ObjectId.is_valid(created_comp_id):
                c_oids.append(ObjectId(created_comp_id))
            await competition_collection.delete_many({"_id": {"$in": c_oids}})
            await competition_registration_collection.delete_many({"competition_id": {"$in": [str(created_comp_id), created_comp_id]}})
            await judge_assignment_collection.delete_many({"competition_id": {"$in": [str(created_comp_id), created_comp_id]}})
        await admin_collection.delete_one({"email": admin_email})
        await user_collection.delete_many({"email": {"$in": [judge_email, user_a_email, user_b_email, user_c_email]}})
