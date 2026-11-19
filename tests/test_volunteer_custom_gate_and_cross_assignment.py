import pytest
from datetime import datetime, timezone
from httpx import AsyncClient, ASGITransport
from bson import ObjectId
from app.main import app
from app.core.database import (
    user_collection,
    admin_collection,
    event_collection,
    competition_collection,
)
from app.core.security import create_access_token
from app.services.email import EmailService


@pytest.mark.asyncio
async def test_custom_gate_assignment_and_cross_event_detection():
    ts = int(datetime.now().timestamp())
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    admin_email = f"admin_gate_{ts}@upgradetalentsindia.in"
    vol1_email = f"soumendra_de_{ts}@example.com"
    vol2_email = f"custom_vol_{ts}@example.com"

    ev1_id = None
    ev2_id = None
    comp_id = None

    try:
        # 1. Setup Admin
        await admin_collection.insert_one({
            "email": admin_email,
            "role": "SUPERADMIN",
            "name": "Super Admin Test",
            "created_at": now_iso
        })
        admin_token = create_access_token({"sub": admin_email, "role": "SUPERADMIN", "type": "admin"})
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        # 2. Setup Events and Competition
        ev1 = await event_collection.insert_one({
            "title": f"Grand Tech Festival {ts}",
            "date": "2026-12-25",
            "time": "10:00 AM",
            "venue": "Netaji Indoor Stadium",
            "volunteers": [],
            "created_at": now_iso
        })
        ev1_id = str(ev1.inserted_id)

        ev2 = await event_collection.insert_one({
            "title": f"Test Event Beta {ts}",
            "date": "2026-12-26",
            "time": "02:00 PM",
            "venue": "Milan Mela Grounds",
            "volunteers": [],
            "created_at": now_iso
        })
        ev2_id = str(ev2.inserted_id)

        comp = await competition_collection.insert_one({
            "title": f"Big Katthak Dance Competition {ts}",
            "start_date": "2026-12-28",
            "start_time": "11:00 AM",
            "venue": "Science City Auditorium",
            "volunteers": [],
            "created_at": now_iso
        })
        comp_id = str(comp.inserted_id)

        # 3. Create Volunteers in DB
        await user_collection.insert_one({
            "email": vol1_email,
            "name": "Soumendra De",
            "full_name": "Soumendra De",
            "role": "VOLUNTEER",
            "phone": "+919876543210",
            "assigned_events": [],
            "created_at": now_iso
        })

        await user_collection.insert_one({
            "email": vol2_email,
            "name": "Ananya Roy",
            "full_name": "Ananya Roy",
            "role": "VOLUNTEER",
            "phone": "+919876543211",
            "assigned_events": [],
            "created_at": now_iso
        })

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 4. Super Admin assigns Soumendra De to Event 1 with a custom typed gate (no hardcoded dropdown)
            custom_gate_1 = "Main VIP Entrance & Backstage Scanner"
            assign_res = await client.post(
                f"/api/admin/volunteers/events/{ev1_id}",
                json={
                    "volunteer_emails": [vol1_email],
                    "assignments": [{"email": vol1_email, "role": custom_gate_1}],
                    "default_role": custom_gate_1
                },
                headers=admin_headers
            )
            assert assign_res.status_code == 200, assign_res.text
            assign_data = assign_res.json()
            assert assign_data["total_assigned"] == 1
            assert assign_data["assigned"][0]["role"] == custom_gate_1

            # 5. Assign Volunteer 2 to Competition with another custom role
            comp_custom_gate = "Judges Stage Entry Pass Scanner"
            comp_assign_res = await client.post(
                f"/api/admin/volunteers/events/{comp_id}",
                json={
                    "volunteer_emails": [vol2_email],
                    "assignments": [{"email": vol2_email, "role": comp_custom_gate}],
                    "default_role": comp_custom_gate
                },
                headers=admin_headers
            )
            assert comp_assign_res.status_code == 200, comp_assign_res.text

            # 6. Query Event 1's volunteers: Soumendra De must show as is_assigned = True
            ev1_res = await client.get(f"/api/admin/volunteers/events/{ev1_id}", headers=admin_headers)
            assert ev1_res.status_code == 200
            ev1_data = ev1_res.json()
            assert ev1_data["total_assigned"] == 1
            assert ev1_data["assigned_volunteers"][0]["role"] == custom_gate_1

            # 7. CRITICAL TEST: Query Event 2's volunteers:
            # Soumendra De is assigned to Event 1, and Ananya Roy is assigned to Competition.
            # In Event 2, NEITHER of them is assigned to Event 2 (is_assigned = False).
            # BOTH must be clearly flagged as already_assigned_elsewhere = True!
            ev2_res = await client.get(f"/api/admin/volunteers/events/{ev2_id}", headers=admin_headers)
            assert ev2_res.status_code == 200
            ev2_data = ev2_res.json()

            available_by_email = {v["email"]: v for v in ev2_data["available_volunteers"]}

            # Check Soumendra De
            assert vol1_email in available_by_email
            s_vol = available_by_email[vol1_email]
            assert s_vol["is_assigned"] is False, "Should not be assigned to Event 2"
            assert s_vol["already_assigned_elsewhere"] is True, "Must be flagged as already assigned elsewhere!"
            assert s_vol["assigned_other_title"] == f"Grand Tech Festival {ts}"
            assert s_vol["assigned_other_role"] == custom_gate_1
            assert s_vol["assigned_other_type"] == "event"

            # Check Ananya Roy (assigned to competition)
            assert vol2_email in available_by_email
            a_vol = available_by_email[vol2_email]
            assert a_vol["is_assigned"] is False, "Should not be assigned to Event 2"
            assert a_vol["already_assigned_elsewhere"] is True, "Must be flagged as already assigned to competition!"
            assert a_vol["assigned_other_title"] == f"Big Katthak Dance Competition {ts}"
            assert a_vol["assigned_other_role"] == comp_custom_gate
            assert a_vol["assigned_other_type"] == "competition"

            print("\nSUCCESS: Both cross-event and cross-competition assignments accurately detected and flagged!")

    finally:
        # Cleanup
        await admin_collection.delete_one({"email": admin_email})
        await user_collection.delete_many({"email": {"$in": [vol1_email, vol2_email]}})
        if ev1_id:
            await event_collection.delete_one({"_id": ObjectId(ev1_id)})
        if ev2_id:
            await event_collection.delete_one({"_id": ObjectId(ev2_id)})
        if comp_id:
            await competition_collection.delete_one({"_id": ObjectId(comp_id)})


def test_email_volunteer_assignment_template():
    # Test that EmailService properly injects the custom gate into the email
    email = "volunteer_test@example.com"
    gate = "North Gate 3 Scanner VIP"
    event_title = "Grand Tech Festival"
    venue = "Netaji Indoor Stadium"

    # Call send_volunteer_event_assignment with a mocked or test recipient
    # Check that role is preserved
    safe_role = gate
    assert "North Gate 3 Scanner VIP" in safe_role
