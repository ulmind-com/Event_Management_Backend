"""
test_offline_attendance_flow.py
================================
Comprehensive End-to-End Test Suite for Offline Event & Competition Participant Attendance:
1. Admin creates offline competition with venue & categories.
2. Multiple participants register for competition.
3. Admin views initial attendance dashboard (present=0, absent=total).
4. Admin marks participant 1 present (tick toggle).
5. Admin verifies updated attendance counts & final_present_list.
6. Admin filters participants by attendance_status (PRESENT vs ABSENT).
7. Judge views contestants and verifies presence fields.
8. Judge accesses final lineup (/final-contestants & only_present=True) - only present participants shown.
9. Public & users view official final present lineup (/final-participants & /live-stage).
10. Live Stage Queue strictly advances ONLY present participants to stage (absent excluded).
11. Admin bulk marks attendance.
12. Admin unticks participant (marks ABSENT).
13. Admin creates offline Event and tests event attendance dashboard, ticking & bulk marking.
14. Full teardown and cleanup.
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
    event_collection,
    booking_collection,
    issued_ticket_collection,
    audit_log_collection,
)
from app.core.security import create_access_token, get_password_hash


@pytest.mark.asyncio
async def test_complete_offline_attendance_flow():
    ts = int(datetime.now().timestamp())
    now = datetime.now(timezone.utc)
    future = now + timedelta(days=30)
    now_iso = now.isoformat()
    future_iso = future.isoformat()

    admin_email = f"admin_attend_{ts}@upgradetalentsindia.in"
    judge_email = f"judge_attend_{ts}@upgradetalentsindia.in"
    user1_email = f"contestant_present_{ts}@upgradetalentsindia.in"
    user2_email = f"contestant_absent_{ts}@upgradetalentsindia.in"
    user3_email = f"event_attendee_{ts}@upgradetalentsindia.in"

    created_comp_id = None
    created_event_id = None

    try:
        # ── 1. Setup Admin ──
        await admin_collection.insert_one({
            "email": admin_email,
            "name": "Head Event Manager",
            "role": "super_admin",
            "password_hash": get_password_hash("AdminPass123!"),
            "created_at": datetime.now(timezone.utc),
        })
        admin_token = create_access_token({"sub": admin_email, "type": "admin", "role": "super_admin"})
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        # ── 2. Setup Judge & Users ──
        await user_collection.insert_many([
            {
                "email": judge_email,
                "name": "Judge Master",
                "full_name": "Judge Master",
                "role": "JUDGE",
                "password_hash": get_password_hash("JudgePass123!"),
                "is_verified": True,
                "created_at": datetime.now(timezone.utc),
            },
            {
                "email": user1_email,
                "name": "Sourav Das",
                "full_name": "Sourav Das",
                "phone_number": "9830011221",
                "role": "USER",
                "is_verified": True,
                "profile_photo": "https://example.com/photos/sourav.jpg",
                "created_at": datetime.now(timezone.utc),
            },
            {
                "email": user2_email,
                "name": "Moumita Sen",
                "full_name": "Moumita Sen",
                "phone_number": "9830011222",
                "role": "USER",
                "is_verified": False,
                "profile_photo": "https://example.com/photos/moumita.jpg",
                "created_at": datetime.now(timezone.utc),
            },
            {
                "email": user3_email,
                "name": "Rahul Verma",
                "full_name": "Rahul Verma",
                "phone_number": "9830011223",
                "role": "USER",
                "is_verified": True,
                "created_at": datetime.now(timezone.utc),
            }
        ])
        judge_token = create_access_token({"sub": judge_email, "type": "judge", "role": "JUDGE"})
        judge_headers = {"Authorization": f"Bearer {judge_token}"}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # ── 3. Create Offline Competition with Venue ──
            comp_payload = {
                "title": f"Bengal Beatbox Championship {ts}",
                "description": "State level beatbox championship held live at venue",
                "category": "Beatboxing",
                "is_offline": True,
                "event_mode": "OFFLINE",
                "venue": "Nazrul Mancha, Kolkata",
                "start_date": now_iso,
                "end_date": future_iso,
                "registration_start_date": now_iso,
                "registration_end_date": future_iso,
                "submission_deadline": future_iso,
                "categories": [
                    {"name": "Solo", "age_limit": "12-30", "price": 300.0}
                ],
                "judge_scoring_enabled": True,
                "judging_status": "OPEN",
                "is_published": True
            }
            comp_res = await client.post("/api/admin/advanced-competitions/", json=comp_payload, headers=admin_headers)
            assert comp_res.status_code == 200, f"Create competition failed: {comp_res.text}"
            comp_data = comp_res.json()
            created_comp_id = comp_data.get("_id") or comp_data.get("id") or comp_data.get("competition_id")
            assert created_comp_id is not None
            assert comp_data.get("is_offline") is True
            assert comp_data.get("venue") == "Nazrul Mancha, Kolkata"

            # ── 4. Assign Judge to Competition ──
            assign_res = await client.post(
                "/api/admin/judges/assignments",
                json={"judge_email": judge_email, "competition_id": str(created_comp_id)},
                headers=admin_headers
            )
            assert assign_res.status_code == 200

            # ── 5. Register Two Contestants in DB ──
            now = datetime.now(timezone.utc)
            reg1_doc = {
                "competition_id": str(created_comp_id),
                "user_email": user1_email,
                "user_name": "Sourav Das",
                "category": "Solo",
                "registration_reference": f"CRG-ATT1-{ts}",
                "status": "CONFIRMED",
                "payment_status": "PAID",
                "payment_method": "ONLINE",
                "stage_status": "NOT_STARTED",
                "stage_order": 1,
                "is_present": False,
                "attendance_status": "ABSENT",
                "registered_at": now
            }
            r1 = await competition_registration_collection.insert_one(reg1_doc)
            reg1_id = str(r1.inserted_id)

            reg2_doc = {
                "competition_id": str(created_comp_id),
                "user_email": user2_email,
                "user_name": "Moumita Sen",
                "category": "Solo",
                "registration_reference": f"CRG-ATT2-{ts}",
                "status": "CONFIRMED",
                "payment_status": "PAID",
                "payment_method": "ONLINE",
                "stage_status": "NOT_STARTED",
                "stage_order": 2,
                "is_present": False,
                "attendance_status": "ABSENT",
                "registered_at": now
            }
            r2 = await competition_registration_collection.insert_one(reg2_doc)
            reg2_id = str(r2.inserted_id)

            # ── 6. Check Initial Attendance Dashboard (Present=0, Absent=2) ──
            dash_res = await client.get(f"/api/admin/advanced-competitions/{created_comp_id}/attendance", headers=admin_headers)
            assert dash_res.status_code == 200, f"Attendance dashboard failed: {dash_res.text}"
            dash_data = dash_res.json()
            assert dash_data["total_registered"] == 2
            assert dash_data["present_count"] == 0
            assert dash_data["absent_count"] == 2
            assert len(dash_data["final_present_list"]) == 0
            assert len(dash_data["all_participants"]) == 2
            assert dash_data["is_offline"] is True
            assert dash_data["venue"] == "Nazrul Mancha, Kolkata"

            # ── 7. Admin marks Participant 1 as PRESENT (tick toggle) ──
            tick_res = await client.patch(
                f"/api/admin/advanced-competitions/{created_comp_id}/participants/{reg1_id}/attendance",
                json={"is_present": True, "attendance_status": "PRESENT", "notes": "Arrived at venue on time"},
                headers=admin_headers
            )
            assert tick_res.status_code == 200, f"Attendance tick failed: {tick_res.text}"
            tick_data = tick_res.json()
            assert tick_data["success"] is True
            assert tick_data["is_present"] is True
            assert tick_data["attendance_status"] == "PRESENT"
            assert tick_data["present_count"] == 1
            assert tick_data["absent_count"] == 1
            assert tick_data["attended_by"] == admin_email
            assert tick_data["attended_at"] is not None

            # ── 8. Re-check Attendance Dashboard: final_present_list has 1 item ──
            dash_res2 = await client.get(f"/api/admin/advanced-competitions/{created_comp_id}/attendance", headers=admin_headers)
            assert dash_res2.status_code == 200
            dash2_data = dash_res2.json()
            assert dash2_data["present_count"] == 1
            assert dash2_data["absent_count"] == 1
            assert len(dash2_data["final_present_list"]) == 1
            assert dash2_data["final_present_list"][0]["user_email"] == user1_email
            assert dash2_data["final_present_list"][0]["is_present"] is True

            # ── 9. Filter Participants via list_competition_participants ──
            # Present filter:
            present_filter_res = await client.get(
                f"/api/admin/advanced-competitions/{created_comp_id}/participants?attendance_status=PRESENT",
                headers=admin_headers
            )
            assert present_filter_res.status_code == 200
            present_list = present_filter_res.json()["participants"]
            assert len(present_list) == 1
            assert present_list[0]["user_email"] == user1_email
            assert present_list[0]["is_present"] is True

            # Absent filter:
            absent_filter_res = await client.get(
                f"/api/admin/advanced-competitions/{created_comp_id}/participants?attendance_status=ABSENT",
                headers=admin_headers
            )
            assert absent_filter_res.status_code == 200
            absent_list = absent_filter_res.json()["participants"]
            assert len(absent_list) == 1
            assert absent_list[0]["user_email"] == user2_email
            assert absent_list[0]["is_present"] is False

            # ── 10. Judge Views Contestants & Final Lineup ──
            # All contestants:
            judge_all_res = await client.get(f"/api/judge/{created_comp_id}/contestants", headers=judge_headers)
            assert judge_all_res.status_code == 200
            judge_contestants = judge_all_res.json()
            assert len(judge_contestants) >= 2
            # Find contestant 1 and contestant 2 in judge view
            c1_match = next((c for c in judge_contestants if c.get("user_email") == user1_email), None)
            c2_match = next((c for c in judge_contestants if c.get("user_email") == user2_email), None)
            assert c1_match is not None
            assert c1_match["is_present"] is True
            assert c1_match["attendance_status"] == "PRESENT"
            assert c2_match is not None
            assert c2_match["is_present"] is False

            # Final lineup only (/final-contestants):
            final_contestants_res = await client.get(f"/api/judge/{created_comp_id}/final-contestants", headers=judge_headers)
            assert final_contestants_res.status_code == 200
            final_contestants = final_contestants_res.json()
            assert len(final_contestants) == 1
            assert final_contestants[0]["user_email"] == user1_email
            assert final_contestants[0]["is_present"] is True

            # ── 11. Public & Users View Confirmed Final Present Lineup ──
            pub_final_res = await client.get(f"/api/public/competitions/{created_comp_id}/final-participants")
            assert pub_final_res.status_code == 200, f"Public final lineup failed: {pub_final_res.text}"
            pub_final_data = pub_final_res.json()
            assert pub_final_data["present_count"] == 1
            assert pub_final_data["total_registered"] == 2
            assert len(pub_final_data["final_participants"]) == 1
            assert pub_final_data["final_participants"][0]["user_email"] == user1_email
            assert pub_final_data["venue"] == "Nazrul Mancha, Kolkata"

            # ── 12. Stage Queue: Strictly Advances ONLY Present Contestants to Stage ──
            # In offline mode, advance_to_next_performer must pick Sourav Das (present) and SKIP Moumita Sen (absent)
            next_stage_res = await client.post(
                f"/api/admin/advanced-competitions/{created_comp_id}/stage/next-performer",
                headers=admin_headers
            )
            assert next_stage_res.status_code == 200, f"Advance stage failed: {next_stage_res.text}"
            next_stage_data = next_stage_res.json()
            assert next_stage_data["stage_status"] == "LIVE"
            assert next_stage_data["current_performer"]["user_email"] == user1_email
            assert next_stage_data["current_performer"]["is_present"] is True

            # Advance stage again - contestant 2 is ABSENT so no more present contestants exist!
            end_stage_res = await client.post(
                f"/api/admin/advanced-competitions/{created_comp_id}/stage/next-performer",
                headers=admin_headers
            )
            assert end_stage_res.status_code == 200
            end_stage_data = end_stage_res.json()
            # Since contestant 1 finished and contestant 2 is absent, current performer is None and stage ended/completed!
            assert end_stage_data.get("current_performer") is None

            # ── 13. Admin Bulk Marks Attendance ──
            bulk_res = await client.post(
                f"/api/admin/advanced-competitions/{created_comp_id}/attendance/bulk",
                json={"registration_ids": [reg1_id, reg2_id], "is_present": True, "attendance_status": "PRESENT"},
                headers=admin_headers
            )
            assert bulk_res.status_code == 200
            bulk_data = bulk_res.json()
            assert bulk_data["success"] is True
            assert bulk_data["present_count"] == 2
            assert bulk_data["absent_count"] == 0

            # ── 14. Admin Unticks Participant 1 (marks ABSENT) ──
            untick_res = await client.patch(
                f"/api/admin/advanced-competitions/{created_comp_id}/participants/{reg1_id}/attendance",
                json={"is_present": False, "attendance_status": "ABSENT"},
                headers=admin_headers
            )
            assert untick_res.status_code == 200
            untick_data = untick_res.json()
            assert untick_data["is_present"] is False
            assert untick_data["attendance_status"] == "ABSENT"
            assert untick_data["present_count"] == 1
            assert untick_data["absent_count"] == 1

            # ── 15. Offline Event Attendance Flow ──
            event_payload = {
                "title": f"Sunidhi Chauhan Live in Concert {ts}",
                "description": "Live musical concert in Kolkata",
                "category": "Concert",
                "date": "2026-12-25",
                "time": "18:30",
                "venue": "Biswa Bangla Mela Prangan, Kolkata",
                "location": "Biswa Bangla Mela Prangan, Kolkata",
                "price": 999.0,
                "is_offline": True,
                "event_mode": "OFFLINE",
                "ticket_enabled": True
            }
            ev_res = await client.post("/api/admin/events", json=event_payload, headers=admin_headers)
            assert ev_res.status_code == 200, f"Create event failed: {ev_res.text}"
            ev_data = ev_res.json()
            created_event_id = ev_data.get("_id") or ev_data.get("id")

            # Seed a booking for this event
            booking_doc = {
                "booking_reference": f"BKG-EV-{ts}",
                "event_id": str(created_event_id),
                "event_title": f"Sunidhi Chauhan Live in Concert {ts}",
                "user_email": user3_email,
                "user_name": "Rahul Verma",
                "ticket_type": "VIP",
                "quantity": 1,
                "total_amount": 999.0,
                "payment_status": "PAID",
                "booking_status": "CONFIRMED",
                "is_present": False,
                "attendance_status": "ABSENT",
                "booked_at": now
            }
            bk_res = await booking_collection.insert_one(booking_doc)
            booking_id = str(bk_res.inserted_id)

            # Check Event Attendance Dashboard
            ev_dash_res = await client.get(f"/api/admin/events/{created_event_id}/attendance", headers=admin_headers)
            assert ev_dash_res.status_code == 200, f"Event attendance dashboard failed: {ev_dash_res.text}"
            ev_dash_data = ev_dash_res.json()
            assert ev_dash_data["total_registered"] == 1
            assert ev_dash_data["present_count"] == 0
            assert ev_dash_data["absent_count"] == 1
            assert len(ev_dash_data["final_present_list"]) == 0

            # Admin marks event attendee as PRESENT (tick toggle)
            ev_tick_res = await client.patch(
                f"/api/admin/events/{created_event_id}/attendance/{booking_id}",
                json={"is_present": True, "attendance_status": "PRESENT"},
                headers=admin_headers
            )
            assert ev_tick_res.status_code == 200, f"Event attendee tick failed: {ev_tick_res.text}"
            ev_tick_data = ev_tick_res.json()
            assert ev_tick_data["is_present"] is True
            assert ev_tick_data["attendance_status"] == "PRESENT"
            assert ev_tick_data["attended_by"] == admin_email

            # Check Event Attendance Dashboard again
            ev_dash_res2 = await client.get(f"/api/admin/events/{created_event_id}/attendance", headers=admin_headers)
            assert ev_dash_res2.status_code == 200
            ev_dash2_data = ev_dash_res2.json()
            assert ev_dash2_data["present_count"] == 1
            assert ev_dash2_data["absent_count"] == 0
            assert len(ev_dash2_data["final_present_list"]) == 1
            assert ev_dash2_data["final_present_list"][0]["user_email"] == user3_email

    finally:
        # ── Teardown & Cleanup ──
        await admin_collection.delete_many({"email": admin_email})
        await user_collection.delete_many({"email": {"$in": [judge_email, user1_email, user2_email, user3_email]}})
        if created_comp_id:
            await competition_collection.delete_many({"_id": {"$in": [ObjectId(created_comp_id), created_comp_id]}})
            await competition_registration_collection.delete_many({"competition_id": str(created_comp_id)})
            await competition_submission_collection.delete_many({"competition_id": str(created_comp_id)})
            await judge_assignment_collection.delete_many({"competition_id": str(created_comp_id)})
        if created_event_id:
            await event_collection.delete_many({"_id": {"$in": [ObjectId(created_event_id), created_event_id]}})
            await booking_collection.delete_many({"event_id": str(created_event_id)})
            await issued_ticket_collection.delete_many({"event_id": str(created_event_id)})
