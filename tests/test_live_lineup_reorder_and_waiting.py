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
)
from app.core.security import create_access_token, get_password_hash


async def main():
    ts = int(datetime.now().timestamp())
    now = datetime.now(timezone.utc)
    future = now + timedelta(days=5)

    admin_email = f"admin_lineup_{ts}@test.com"
    user_1_email = f"user_1_reg_{ts}@test.com"
    user_2_email = f"user_2_reg_{ts}@test.com"
    user_7_email = f"user_7_reg_{ts}@test.com"
    user_399_email = f"user_399_reg_{ts}@test.com"

    created_comp_id = None

    try:
        # 1. Setup Admin
        await admin_collection.insert_one({
            "email": admin_email,
            "name": "Super Admin Tester",
            "role": "super_admin",
            "password_hash": get_password_hash("Pass123!"),
            "created_at": now,
        })
        admin_token = create_access_token({"sub": admin_email, "type": "admin", "role": "super_admin"})
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        # 2. Setup Users
        await user_collection.insert_many([
            {"email": user_1_email, "name": "User 1 (Registered First)", "role": "USER", "created_at": now - timedelta(hours=4)},
            {"email": user_2_email, "name": "User 2 (Registered Second)", "role": "USER", "created_at": now - timedelta(hours=3)},
            {"email": user_7_email, "name": "User 7 (Registered Seventh)", "role": "USER", "created_at": now - timedelta(hours=2)},
            {"email": user_399_email, "name": "User 399 (Registered 399th)", "role": "USER", "created_at": now - timedelta(hours=1)},
        ])

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            # 3. Create Competition
            comp_res = await client.post(
                "/api/admin/advanced-competitions/",
                headers=admin_headers,
                json={
                    "title": f"Live Stage Lineup Championship {ts}",
                    "description": "Validates dynamic present ordering and waiting queue",
                    "category": "Singing",
                    "event_mode": "OFFLINE",
                    "is_offline": True,
                    "venue": "Kolkata Stadium",
                    "start_date": now.isoformat(),
                    "end_date": future.isoformat(),
                    "registration_start_date": now.isoformat(),
                    "registration_end_date": future.isoformat(),
                    "submission_deadline": future.isoformat(),
                    "registration_fee": 0,
                    "max_participants": 500,
                    "is_published": True,
                    "judge_scoring_enabled": True,
                    "judging_status": "OPEN",
                }
            )
            assert comp_res.status_code == 200, f"Failed to create comp: {comp_res.text}"
            comp_data = comp_res.json()
            created_comp_id = str(comp_data.get("_id") or comp_data.get("id"))

            # 4. Register users in order: 1, 2, 7, 399
            regs = {}
            for idx, (em, u_name) in enumerate([
                (user_1_email, "User 1"),
                (user_2_email, "User 2"),
                (user_7_email, "User 7"),
                (user_399_email, "User 399"),
            ], start=1):
                ins = await competition_registration_collection.insert_one({
                    "competition_id": created_comp_id,
                    "user_email": em,
                    "user_name": u_name,
                    "status": "CONFIRMED",
                    "payment_status": "PAID",
                    "is_present": False,
                    "attendance_status": "ABSENT",
                    "registered_at": now - timedelta(minutes=(100 - idx * 10)),
                    "stage_order": idx,
                    "registration_reference": f"CRG-TEST-{idx:03d}",
                })
                regs[em] = str(ins.inserted_id)

            print("✓ All 4 users registered successfully.")

            # Initial State: 0 present. All 4 should be in public live stage in registration order!
            stage_res0 = await client.get(f"/api/v1/competitions/{created_comp_id}/live-stage")
            assert stage_res0.status_code == 200, f"Error: {stage_res0.text}"
            data0 = stage_res0.json()
            lineup0 = data0.get("lineup", [])
            assert len(lineup0) == 4, f"Expected 4 in initial lineup, got {len(lineup0)}"
            assert lineup0[0]["user_email"] == user_1_email
            assert lineup0[1]["user_email"] == user_2_email
            assert lineup0[2]["user_email"] == user_7_email
            assert lineup0[3]["user_email"] == user_399_email
            assert all(p["attendance_status"] == "WAITING_CHECKIN" for p in lineup0)
            print("✓ Initial State verified: All 4 users in registration queue as WAITING_CHECKIN.")

            # STEP 1: Mark User 7 Present first!
            p_res1 = await client.patch(
                f"/api/admin/advanced-competitions/{created_comp_id}/participants/{regs[user_7_email]}/attendance",
                headers=admin_headers,
                json={"is_present": True, "attendance_status": "PRESENT"}
            )
            assert p_res1.status_code == 200, f"Failed to mark present: {p_res1.text}"

            stage_res1 = await client.get(f"/api/v1/competitions/{created_comp_id}/live-stage")
            assert stage_res1.status_code == 200
            data1 = stage_res1.json()
            lineup1 = data1.get("lineup", [])
            assert len(lineup1) == 4, f"Expected 4 users in lineup, got {len(lineup1)}"
            assert lineup1[0]["user_email"] == user_7_email, f"User 7 should be #1! Got {lineup1[0]['user_email']}"
            assert lineup1[0]["is_present"] is True
            assert lineup1[0]["order"] == 1
            # Remaining waiting participants slide down in registration sequence:
            assert lineup1[1]["user_email"] == user_1_email, f"User 1 should be #2! Got {lineup1[1]['user_email']}"
            assert lineup1[1]["order"] == 2
            assert lineup1[1]["attendance_status"] == "WAITING_CHECKIN"
            assert lineup1[2]["user_email"] == user_2_email, f"User 2 should be #3! Got {lineup1[2]['user_email']}"
            assert lineup1[2]["order"] == 3
            assert lineup1[3]["user_email"] == user_399_email, f"User 399 should be #4! Got {lineup1[3]['user_email']}"
            assert lineup1[3]["order"] == 4
            print("✓ STEP 1 PASSED: User 7 jumped to #1! User 1, 2, 399 slid down to #2, #3, #4.")

            # STEP 2: Mark User 399 Present second!
            await asyncio.sleep(0.05) # ensure distinct timestamp
            p_res2 = await client.patch(
                f"/api/admin/advanced-competitions/{created_comp_id}/participants/{regs[user_399_email]}/attendance",
                headers=admin_headers,
                json={"is_present": True, "attendance_status": "PRESENT"}
            )
            assert p_res2.status_code == 200

            stage_res2 = await client.get(f"/api/v1/competitions/{created_comp_id}/live-stage")
            assert stage_res2.status_code == 200
            data2 = stage_res2.json()
            lineup2 = data2.get("lineup", [])
            assert len(lineup2) == 4
            assert lineup2[0]["user_email"] == user_7_email, "User 7 must stay #1 (first to check in)"
            assert lineup2[0]["order"] == 1
            assert lineup2[1]["user_email"] == user_399_email, "User 399 must be #2 (second to check in)"
            assert lineup2[1]["order"] == 2
            assert lineup2[1]["is_present"] is True
            # Waiting participants slide down:
            assert lineup2[2]["user_email"] == user_1_email, "User 1 is now #3 in queue (waiting)"
            assert lineup2[2]["order"] == 3
            assert lineup2[3]["user_email"] == user_2_email, "User 2 is now #4 in queue (waiting)"
            assert lineup2[3]["order"] == 4
            print("✓ STEP 2 PASSED: User 7 is #1, User 399 is #2, User 1 is #3 (waiting), User 2 is #4 (waiting).")

            # STEP 3: Mark User 1 Present third!
            await asyncio.sleep(0.05)
            p_res3 = await client.patch(
                f"/api/admin/advanced-competitions/{created_comp_id}/participants/{regs[user_1_email]}/attendance",
                headers=admin_headers,
                json={"is_present": True, "attendance_status": "PRESENT"}
            )
            assert p_res3.status_code == 200

            stage_res3 = await client.get(f"/api/v1/competitions/{created_comp_id}/live-stage")
            assert stage_res3.status_code == 200
            data3 = stage_res3.json()
            lineup3 = data3.get("lineup", [])
            assert len(lineup3) == 4
            assert lineup3[0]["user_email"] == user_7_email, "#1 must be User 7"
            assert lineup3[1]["user_email"] == user_399_email, "#2 must be User 399"
            assert lineup3[2]["user_email"] == user_1_email, "#3 must be User 1 (checked in 3rd)"
            assert lineup3[2]["is_present"] is True
            assert lineup3[3]["user_email"] == user_2_email, "#4 must be User 2 (still waiting)"
            assert lineup3[3]["is_present"] is False
            print("✓ STEP 3 PASSED: Order is #1 User 7, #2 User 399, #3 User 1, #4 User 2 (waiting).")

            # STEP 4: Stage performance calling
            adv1 = await client.post(f"/api/admin/advanced-competitions/{created_comp_id}/stage/next-performer", headers=admin_headers)
            assert adv1.status_code == 200
            assert adv1.json()["current_performer"]["user_email"] == user_7_email, "1st performer must be User 7!"
            print("✓ STEP 4.1 PASSED: 1st performer on stage is User 7.")

            adv2 = await client.post(f"/api/admin/advanced-competitions/{created_comp_id}/stage/next-performer", headers=admin_headers)
            assert adv2.status_code == 200
            assert adv2.json()["current_performer"]["user_email"] == user_399_email, "2nd performer must be User 399!"
            print("✓ STEP 4.2 PASSED: 2nd performer on stage is User 399.")

            print("\n🎉 ALL E2E LINEUP & WAITING QUEUE TESTS PASSED WITH 100% SUCCESS! 🎉")

    finally:
        if created_comp_id:
            c_oids = [created_comp_id]
            if ObjectId.is_valid(created_comp_id):
                c_oids.append(ObjectId(created_comp_id))
            await competition_collection.delete_many({"_id": {"$in": c_oids}})
            await competition_registration_collection.delete_many({"competition_id": {"$in": [str(created_comp_id), created_comp_id]}})
        await admin_collection.delete_one({"email": admin_email})
        await user_collection.delete_many({"email": {"$in": [user_1_email, user_2_email, user_7_email, user_399_email]}})


if __name__ == "__main__":
    asyncio.run(main())
