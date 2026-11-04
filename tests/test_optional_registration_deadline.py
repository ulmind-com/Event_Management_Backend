"""
test_optional_registration_deadline.py
=======================================
End-to-end production test that verifies:

1. Admin can create a competition WITHOUT registration_deadline → 200 OK.
2. Admin can create a competition WITH registration_deadline → 200 OK.
3. When no deadline is set, registration remains OPEN indefinitely (lifecycle
   service returns can_register=True and does not auto-close).
4. When a deadline is set and has passed, registration is auto-CLOSED by the
   lifecycle evaluation.
5. Admin can later add/update a registration_deadline on a competition that
   was created without one.
"""

import pytest
from datetime import datetime, timezone, timedelta
from httpx import AsyncClient, ASGITransport
from bson import ObjectId

from app.main import app
from app.core.security import create_access_token, get_password_hash
from app.core.database import (
    competition_collection,
    admin_collection,
)
from app.services.competition_lifecycle import (
    evaluate_competition_registration_status,
)


@pytest.mark.asyncio
async def test_create_competition_without_registration_deadline():
    """
    Scenario A: Admin creates a competition WITHOUT registration_deadline.
    - Competition should be created successfully (200).
    - registration_deadline should be None/null in the stored document.
    - Lifecycle evaluation should return registration OPEN (no deadline to
      expire).
    """
    admin_email = "admin_optdl_test@upgradetalentsindia.in"
    comp_title = f"No Deadline Comp {int(datetime.now().timestamp())}"

    # ── Setup admin ──
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

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        # ── 1. Create competition WITHOUT registration_deadline ──
        now = datetime.now(timezone.utc)
        payload = {
            "title": comp_title,
            "description": "A competition with no registration deadline",
            "category": "General",
            "start_date": (now - timedelta(days=1)).isoformat(),
            "end_date": (now + timedelta(days=30)).isoformat(),
            "registration_start_date": (now - timedelta(days=1)).isoformat(),
            # NOTE: registration_deadline intentionally omitted
            "submission_deadline": (now + timedelta(days=25)).isoformat(),
            "is_published": True,
            "judge_scoring_enabled": True,
            "audience_voting_enabled": False,
        }
        res = await ac.post(
            "/api/admin/advanced-competitions/",
            json=payload,
            headers=admin_headers,
        )
        assert res.status_code == 200, (
            f"Expected 200 but got {res.status_code}: {res.text}"
        )
        comp_data = res.json()
        comp_id = comp_data["_id"]

        # ── 2. Verify stored document has no registration_deadline ──
        stored = await competition_collection.find_one(
            {"_id": ObjectId(comp_id)}
        )
        assert stored is not None, "Competition not found in DB"
        assert stored.get("registration_deadline") is None, (
            f"Expected None but got {stored.get('registration_deadline')}"
        )

        # ── 3. Lifecycle evaluation → registration should be OPEN ──
        status = evaluate_competition_registration_status(stored)
        assert status["can_register"] is True, (
            f"Expected can_register=True but got: {status}"
        )
        assert status["registration_status"] == "OPEN"
        assert status["registration_deadline"] is None

        # ── 4. Admin registration status endpoint also says OPEN ──
        status_res = await ac.get(
            f"/api/admin/advanced-competitions/{comp_id}/registration/status",
            headers=admin_headers,
        )
        assert status_res.status_code == 200
        res_data = status_res.json()
        assert res_data["registration_status"] == "OPEN"
        assert res_data["can_register"] is True
        assert res_data["registration_deadline"] is None

    # ── Cleanup ──
    await competition_collection.delete_many({"title": comp_title})
    await admin_collection.delete_many({"email": admin_email})


@pytest.mark.asyncio
async def test_create_competition_with_registration_deadline():
    """
    Scenario B: Admin creates a competition WITH registration_deadline.
    - Competition should be created successfully (200).
    - When deadline is in the future → registration OPEN.
    - When deadline is in the past (simulated) → lifecycle auto-closes
      registration.
    """
    admin_email = "admin_withdl_test@upgradetalentsindia.in"
    comp_title = f"With Deadline Comp {int(datetime.now().timestamp())}"

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

    now = datetime.now(timezone.utc)
    future_deadline = now + timedelta(days=10)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        # ── 1. Create competition WITH a future registration_deadline ──
        payload = {
            "title": comp_title,
            "description": "A competition with registration deadline",
            "category": "Dance",
            "start_date": (now - timedelta(days=1)).isoformat(),
            "end_date": (now + timedelta(days=30)).isoformat(),
            "registration_start_date": (now - timedelta(days=1)).isoformat(),
            "registration_deadline": future_deadline.isoformat(),
            "submission_deadline": (now + timedelta(days=25)).isoformat(),
            "is_published": True,
            "judge_scoring_enabled": True,
            "audience_voting_enabled": False,
        }
        res = await ac.post(
            "/api/admin/advanced-competitions/",
            json=payload,
            headers=admin_headers,
        )
        assert res.status_code == 200, (
            f"Expected 200 but got {res.status_code}: {res.text}"
        )
        comp_data = res.json()
        comp_id = comp_data["_id"]

        # ── 2. Verify deadline is stored ──
        stored = await competition_collection.find_one(
            {"_id": ObjectId(comp_id)}
        )
        assert stored is not None
        assert stored.get("registration_deadline") is not None, (
            "registration_deadline should be set in DB"
        )

        # ── 3. Future deadline → registration OPEN ──
        status_future = evaluate_competition_registration_status(stored)
        assert status_future["can_register"] is True
        assert status_future["registration_status"] == "OPEN"
        assert status_future["registration_deadline"] is not None

        # ── 4. Simulate past deadline → registration should be CLOSED ──
        past_time = future_deadline + timedelta(seconds=1)
        status_past = evaluate_competition_registration_status(
            stored, now=past_time
        )
        assert status_past["can_register"] is False, (
            f"Expected can_register=False after deadline: {status_past}"
        )
        assert status_past["registration_status"] == "CLOSED"
        assert "closed" in status_past["reason"].lower() or "Registration closed" in status_past["reason"]

    # ── Cleanup ──
    await competition_collection.delete_many({"title": comp_title})
    await admin_collection.delete_many({"email": admin_email})


@pytest.mark.asyncio
async def test_add_deadline_later_via_update():
    """
    Scenario C: Admin creates a competition without deadline, then adds one
    later via the update endpoint.
    - Create without deadline → OPEN.
    - Update to add a future deadline → still OPEN.
    - Simulate past deadline → CLOSED.
    """
    admin_email = "admin_updatedl_test@upgradetalentsindia.in"
    comp_title = f"Update Deadline Comp {int(datetime.now().timestamp())}"

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

    now = datetime.now(timezone.utc)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        # ── 1. Create without deadline ──
        payload = {
            "title": comp_title,
            "description": "Will add deadline later",
            "category": "Art",
            "start_date": (now - timedelta(days=1)).isoformat(),
            "end_date": (now + timedelta(days=60)).isoformat(),
            "registration_start_date": (now - timedelta(days=1)).isoformat(),
            "submission_deadline": (now + timedelta(days=55)).isoformat(),
            "is_published": True,
            "judge_scoring_enabled": True,
            "audience_voting_enabled": False,
        }
        res = await ac.post(
            "/api/admin/advanced-competitions/",
            json=payload,
            headers=admin_headers,
        )
        assert res.status_code == 200
        comp_id = res.json()["_id"]

        # Confirm no deadline
        stored = await competition_collection.find_one(
            {"_id": ObjectId(comp_id)}
        )
        assert stored.get("registration_deadline") is None

        # ── 2. Update to add a future deadline ──
        future_deadline = now + timedelta(days=15)
        update_res = await ac.put(
            f"/api/admin/advanced-competitions/{comp_id}",
            json={"registration_deadline": future_deadline.isoformat()},
            headers=admin_headers,
        )
        assert update_res.status_code == 200, (
            f"Update failed: {update_res.text}"
        )

        # ── 3. Verify deadline is now stored ──
        updated = await competition_collection.find_one(
            {"_id": ObjectId(comp_id)}
        )
        assert updated.get("registration_deadline") is not None

        # ── 4. Future deadline → still OPEN ──
        status = evaluate_competition_registration_status(updated)
        assert status["can_register"] is True
        assert status["registration_status"] == "OPEN"

        # ── 5. Past deadline → CLOSED ──
        past_time = future_deadline + timedelta(minutes=1)
        status_past = evaluate_competition_registration_status(
            updated, now=past_time
        )
        assert status_past["can_register"] is False
        assert status_past["registration_status"] == "CLOSED"

    # ── Cleanup ──
    await competition_collection.delete_many({"title": comp_title})
    await admin_collection.delete_many({"email": admin_email})
