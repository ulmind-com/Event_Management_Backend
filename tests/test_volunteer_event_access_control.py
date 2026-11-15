import asyncio
import uuid
from bson import ObjectId
from app.api.routes.admin_checkin import get_volunteer_authorized_event_ids
from app.core.database import db, issued_ticket_collection, event_collection, user_collection
from app.core.security import create_access_token
from app.services.ticket import TicketService
import httpx
from httpx import ASGITransport
from app.main import app

async def run_all_tests():
    print("─── 1. Testing Helper Unit Isolation ───")
    # 1. Admin / Super Admin scanner -> unrestricted access (None)
    admin_scanner = {"role": "SUPER_ADMIN", "email": "admin@upgradetalentsindia.in"}
    authorized_eids = await get_volunteer_authorized_event_ids(admin_scanner)
    assert authorized_eids is None, "Super Admin should have unrestricted (None) access"

    # 2. Volunteer with assigned_events array in user doc
    test_eid_1 = str(ObjectId())
    test_eid_2 = str(ObjectId())
    volunteer_scanner = {
        "role": "VOLUNTEER",
        "email": "testvol@example.com",
        "assigned_events": [
            {"event_id": test_eid_1, "event_title": "Event 1"},
            {"event_id": test_eid_2, "event_title": "Event 2"}
        ]
    }
    authorized_eids = await get_volunteer_authorized_event_ids(volunteer_scanner)
    assert authorized_eids is not None
    assert test_eid_1 in authorized_eids
    assert test_eid_2 in authorized_eids

    # 3. Volunteer scanning an event they are NOT assigned to
    unassigned_eid = str(ObjectId())
    assert unassigned_eid not in authorized_eids, "Volunteer should not have access to unassigned event"
    print("✓ Isolated access control helper passed.")

    print("\n─── 2. Testing End-to-End API Routes with AsyncClient ───")
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Setup test event A and test event B in DB
        event_a_id = ObjectId()
        event_b_id = ObjectId()
        event_a_title = f"Championship A {uuid.uuid4().hex[:4]}"
        event_b_title = f"Championship B {uuid.uuid4().hex[:4]}"

        await event_collection.insert_one({
            "_id": event_a_id,
            "title": event_a_title,
            "date": "2026-12-01",
            "venue": "Auditorium A"
        })
        await event_collection.insert_one({
            "_id": event_b_id,
            "title": event_b_title,
            "date": "2026-12-02",
            "venue": "Auditorium B"
        })

        # Setup Volunteer assigned ONLY to Event A
        vol_email = f"vol_{uuid.uuid4().hex[:6]}@test.com"
        await user_collection.insert_one({
            "email": vol_email,
            "role": "VOLUNTEER",
            "name": "Gate Volunteer A",
            "assigned_events": [
                {"event_id": str(event_a_id), "event_title": event_a_title}
            ]
        })
        vol_token = create_access_token({"sub": vol_email, "type": "user"})
        vol_headers = {"Authorization": f"Bearer {vol_token}"}

        # Setup tickets for Event A and Event B
        ticket_a_ref = f"TKT-A-{uuid.uuid4().hex[:6].upper()}"
        ticket_b_ref = f"TKT-B-{uuid.uuid4().hex[:6].upper()}"

        await issued_ticket_collection.insert_one({
            "ticket_ref": ticket_a_ref,
            "event_id": str(event_a_id),
            "event_title": event_a_title,
            "holder_name": "Attendee Alpha",
            "holder_names": ["Attendee Alpha", "Attendee Beta"],
            "quantity": 2,
            "admitted_count": 0,
            "is_checked_in": False,
            "status": "ACTIVE"
        })

        await issued_ticket_collection.insert_one({
            "ticket_ref": ticket_b_ref,
            "event_id": str(event_b_id),
            "event_title": event_b_title,
            "holder_name": "Attendee Gamma",
            "holder_names": ["Attendee Gamma"],
            "quantity": 1,
            "admitted_count": 0,
            "is_checked_in": False,
            "status": "ACTIVE"
        })

        try:
            # TEST CASE 1: Volunteer assigned to Event A scans Event A ticket -> MUST SUCCEED (200)
            res_a = await client.post("/api/admin/tickets/verify", json={"qr_token": ticket_a_ref}, headers=vol_headers)
            assert res_a.status_code == 200, f"Expected 200 for Event A, got {res_a.status_code}: {res_a.text}"
            data_a = res_a.json()
            assert data_a["ticket"]["holder_name"] == "Attendee Alpha"
            assert len(data_a["ticket"]["holder_names"]) == 2
            assert "Attendee Beta" in data_a["ticket"]["holder_names"]
            print("✓ Test Case 1 Passed: Volunteer scanned assigned Event A ticket successfully (200 OK) with all attendee names!")

            # TEST CASE 2: Same volunteer scans Event B ticket -> MUST BE REJECTED (403 Forbidden)
            res_b = await client.post("/api/admin/tickets/verify", json={"qr_token": ticket_b_ref}, headers=vol_headers)
            assert res_b.status_code == 403, f"Expected 403 for Event B, got {res_b.status_code}: {res_b.text}"
            assert "Access Denied" in res_b.json().get("detail", "")
            print(f"✓ Test Case 2 Passed: Event B ticket strictly blocked on backend: 403 Forbidden ({res_b.json().get('detail')})")

            # TEST CASE 3: Same volunteer attempts check-in on Event B ticket -> MUST BE REJECTED (403 Forbidden)
            res_cin_b = await client.post("/api/admin/tickets/check-in", json={"qr_token": ticket_b_ref}, headers=vol_headers)
            assert res_cin_b.status_code == 403, f"Expected 403 for Event B check-in, got {res_cin_b.status_code}: {res_cin_b.text}"
            print("✓ Test Case 3 Passed: Event B admission attempt strictly rejected with 403 Forbidden!")

            # TEST CASE 4: Gate Mismatch Check (volunteer passed event_id of Event B while scanning Event A ticket)
            res_mismatch = await client.post(
                "/api/admin/tickets/verify",
                json={"qr_token": ticket_a_ref, "event_id": str(event_b_id)},
                headers=vol_headers
            )
            assert res_mismatch.status_code == 403, f"Expected 403 for gate mismatch, got {res_mismatch.status_code}"
            assert "Event Mismatch" in res_mismatch.json().get("detail", "")
            print("✓ Test Case 4 Passed: Gate mismatch check strictly rejected with 403 Forbidden!")

            # TEST CASE 5: Volunteer admits Event A ticket -> MUST SUCCEED (200)
            res_cin_a = await client.post("/api/admin/tickets/check-in", json={"qr_token": ticket_a_ref}, headers=vol_headers)
            assert res_cin_a.status_code == 200, f"Expected 200 for Event A check-in, got {res_cin_a.status_code}: {res_cin_a.text}"
            cin_data = res_cin_a.json()
            assert cin_data["is_fully_used"] is True
            assert cin_data["total_admitted"] == 2
            print(f"✓ Test Case 5 Passed: Admitted {cin_data['total_admitted']} attendees on Event A ticket. Fully used!")

            # TEST CASE 6: Re-scan already used ticket -> returns is_checked_in = True, cannot be admitted again
            res_re_verify = await client.post("/api/admin/tickets/verify", json={"qr_token": ticket_a_ref}, headers=vol_headers)
            assert res_re_verify.status_code == 200
            assert res_re_verify.json()["ticket"]["is_checked_in"] is True

            res_reuse = await client.post("/api/admin/tickets/check-in", json={"qr_token": ticket_a_ref}, headers=vol_headers)
            assert res_reuse.status_code == 400
            assert "already been fully used" in res_reuse.json().get("detail", "")
            print("✓ Test Case 6 Passed: Already used ticket rejected with 400 Bad Request!")

        finally:
            # Clean up test artifacts from DB
            await event_collection.delete_many({"_id": {"$in": [event_a_id, event_b_id]}})
            await user_collection.delete_many({"email": vol_email})
            await issued_ticket_collection.delete_many({"ticket_ref": {"$in": [ticket_a_ref, ticket_b_ref]}})
            print("✓ Cleaned up test data.")

    print("\n🎉 ALL 6 COMPREHENSIVE E2E ACCESS-CONTROL TEST SCENARIOS PASSED 100%!")

if __name__ == "__main__":
    asyncio.run(run_all_tests())
