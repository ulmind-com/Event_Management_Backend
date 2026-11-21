import pytest
from httpx import AsyncClient, ASGITransport
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
from datetime import datetime, timezone, timedelta
import uuid

from app.core.config import settings
from app.core.security import create_access_token
from app.core.database import (
    event_collection,
    ticket_collection,
    booking_collection,
    issued_ticket_collection,
    seat_hold_collection,
    user_collection,
)
from app.main import app

@pytest.mark.asyncio
async def test_seat_hold_and_release_flow():
    client = AsyncIOMotorClient(settings.MONGODB_URI)
    db = client.eventora_db

    test_eid = str(ObjectId())
    user_email_1 = "seat_user_1@test.com"
    user_email_2 = "seat_user_2@test.com"

    # Clean up
    await seat_hold_collection.delete_many({"event_id": test_eid})
    await booking_collection.delete_many({"event_id": test_eid})

    # Setup dummy event in event_collection
    await event_collection.insert_one({
        "_id": ObjectId(test_eid),
        "title": "Grand Gala Concert 2026",
        "date": "2026-12-15",
        "time": "18:00",
        "venue": "Kolkata Arena",
        "is_published": True,
        "registration_enabled": True,
        "ticket_enabled": True,
        "capacity": 200,
        "categories": [{"name": "VIP Seating", "price": 500.0, "capacity": 50}]
    })

    token_1 = str(uuid.uuid4())
    token_2 = str(uuid.uuid4())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. User 1 holds seats A-01 and A-02
        res = await ac.post(f"/api/activities/events/{test_eid}/hold-seats", json={
            "seats": ["A-01", "A-02"],
            "hold_token": token_1,
            "user_email": user_email_1
        })
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["success"] is True
        assert data["held_seats"] == ["A-01", "A-02"]
        assert data["expires_in"] == 300

        # 2. Check booked seats from User 2 perspective (User 1's seats must be occupied)
        res_u2 = await ac.get(f"/api/activities/events/{test_eid}/booked-seats?hold_token={token_2}")
        assert res_u2.status_code == 200
        u2_booked = res_u2.json()["booked_seats"]
        assert "A-01" in u2_booked
        assert "A-02" in u2_booked

        # 3. Check booked seats from User 1 perspective (User 1's seats must NOT be in booked list)
        res_u1 = await ac.get(f"/api/activities/events/{test_eid}/booked-seats?hold_token={token_1}")
        assert res_u1.status_code == 200
        u1_booked = res_u1.json()["booked_seats"]
        assert "A-01" not in u1_booked
        assert "A-02" not in u1_booked

        # 4. User 2 tries to hold seat A-01 -> Must return 409 Conflict
        res_conflict = await ac.post(f"/api/activities/events/{test_eid}/hold-seats", json={
            "seats": ["A-01", "A-03"],
            "hold_token": token_2,
            "user_email": user_email_2
        })
        assert res_conflict.status_code == 409
        assert "reserved by another attendee" in res_conflict.json()["detail"]

        # 5. User 1 releases seat A-01
        res_rel = await ac.post(f"/api/activities/events/{test_eid}/release-seats", json={
            "hold_token": token_1,
            "seats": ["A-01"]
        })
        assert res_rel.status_code == 200
        assert res_rel.json()["released_count"] == 1

        # Now User 2 can hold seat A-01
        res_u2_hold = await ac.post(f"/api/activities/events/{test_eid}/hold-seats", json={
            "seats": ["A-01"],
            "hold_token": token_2,
            "user_email": user_email_2
        })
        assert res_u2_hold.status_code == 200
        assert res_u2_hold.json()["held_seats"] == ["A-01"]

        # 6. Release all remaining holds
        await ac.post(f"/api/activities/events/{test_eid}/release-seats", json={"hold_token": token_1})
        await ac.post(f"/api/activities/events/{test_eid}/release-seats", json={"hold_token": token_2})

    # Cleanup
    await event_collection.delete_many({"_id": ObjectId(test_eid)})
    await seat_hold_collection.delete_many({"event_id": test_eid})
