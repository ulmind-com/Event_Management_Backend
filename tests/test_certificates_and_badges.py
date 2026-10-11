import pytest
import io
from httpx import AsyncClient, ASGITransport
from datetime import datetime, timezone
from bson import ObjectId

from app.main import app
from app.core.database import (
    user_collection,
    admin_collection,
    competition_collection,
    competition_registration_collection,
    competition_submission_collection,
    leaderboard_result_collection,
    certificate_collection,
    award_certificate_config_collection,
    user_badge_award_collection,
    badge_policy_collection,
    booking_collection,
    issued_ticket_collection,
)
from app.core.security import create_access_token
from app.services.certificate_pdf_service import CertificatePDFService

pytestmark = pytest.mark.asyncio

async def setup_competition_and_users():
    user1_email = "participant1@certtest.com"
    user2_email = "participant2@certtest.com"
    user3_email = "nosubmit@certtest.com"
    admin_email = "admin_cert@certtest.com"

    await user_collection.delete_many({"email": {"$in": [user1_email, user2_email, user3_email]}})
    await admin_collection.delete_many({"email": admin_email})

    u1_res = await user_collection.insert_one({
        "name": "Alice Winner",
        "email": user1_email,
        "phone": "+919876543210",
        "role": "USER",
        "status": "ACTIVE",
        "registration_complete": True,
        "created_at": datetime.now(timezone.utc)
    })
    u2_res = await user_collection.insert_one({
        "name": "Bob RunnerUp",
        "email": user2_email,
        "phone": "+919876543211",
        "role": "USER",
        "status": "ACTIVE",
        "registration_complete": True,
        "created_at": datetime.now(timezone.utc)
    })
    u3_res = await user_collection.insert_one({
        "name": "Charlie Ineligible",
        "email": user3_email,
        "role": "USER",
        "status": "ACTIVE",
        "registration_complete": True,
        "created_at": datetime.now(timezone.utc)
    })

    await admin_collection.insert_one({
        "email": admin_email,
        "role": "SUPER_ADMIN",
        "created_at": datetime.now(timezone.utc)
    })

    user1_token = create_access_token({"sub": user1_email, "type": "user"})
    user2_token = create_access_token({"sub": user2_email, "type": "user"})
    admin_token = create_access_token({"sub": admin_email, "type": "admin"})

    comp_res = await competition_collection.insert_one({
        "title": "National Hackathon 2026",
        "category": "TECH",
        "is_published": True,
        "results_published": False,
        "created_at": datetime.now(timezone.utc)
    })
    comp_id = str(comp_res.inserted_id)

    await certificate_collection.delete_many({"activity_id": comp_id})
    await award_certificate_config_collection.delete_many({"competition_id": comp_id})
    await user_badge_award_collection.delete_many({"user_email": {"$in": [user1_email, user2_email]}})

    # Registrations
    await competition_registration_collection.insert_one({
        "competition_id": comp_id,
        "user_email": user1_email,
        "status": "REGISTERED",
        "created_at": datetime.now(timezone.utc)
    })
    await competition_registration_collection.insert_one({
        "competition_id": comp_id,
        "user_email": user2_email,
        "status": "REGISTERED",
        "created_at": datetime.now(timezone.utc)
    })
    await competition_registration_collection.insert_one({
        "competition_id": comp_id,
        "user_email": user3_email,
        "status": "REGISTERED",
        "created_at": datetime.now(timezone.utc)
    })

    # Submissions
    s1 = await competition_submission_collection.insert_one({
        "competition_id": comp_id,
        "user_email": user1_email,
        "status": "SUBMITTED",
        "submitted_at": datetime.now(timezone.utc)
    })
    s2 = await competition_submission_collection.insert_one({
        "competition_id": comp_id,
        "user_email": user2_email,
        "status": "SUBMITTED",
        "submitted_at": datetime.now(timezone.utc)
    })

    # Leaderboard
    await leaderboard_result_collection.delete_many({"competition_id": comp_id})
    await leaderboard_result_collection.insert_one({
        "competition_id": comp_id,
        "submission_id": s1.inserted_id,
        "total_score": 98.5,
        "rank": 1,
        "status": "APPROVED"
    })
    await leaderboard_result_collection.insert_one({
        "competition_id": comp_id,
        "submission_id": s2.inserted_id,
        "total_score": 92.0,
        "rank": 2,
        "status": "APPROVED"
    })

    return {
        "user1_id": str(u1_res.inserted_id),
        "user2_id": str(u2_res.inserted_id),
        "user3_id": str(u3_res.inserted_id),
        "user1_email": user1_email,
        "user2_email": user2_email,
        "user3_email": user3_email,
        "user1_token": user1_token,
        "user2_token": user2_token,
        "admin_token": admin_token,
        "comp_id": comp_id
    }

async def test_certificate_eligibility_before_results_published():
    data = await setup_competition_and_users()
    comp_id = data["comp_id"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get(
            f"/api/admin/competitions/{comp_id}/certificate-eligibility",
            headers={"Authorization": f"Bearer {data['admin_token']}"}
        )
        assert res.status_code == 200
        body = res.json()
        assert body["results_published"] is False
        assert body["eligible_count"] == 0

async def test_certificate_eligibility_after_results_published():
    data = await setup_competition_and_users()
    comp_id = data["comp_id"]

    # Mark results published
    await competition_collection.update_one(
        {"_id": ObjectId(comp_id)},
        {"$set": {"results_published": True, "results_published_at": datetime.now(timezone.utc)}}
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get(
            f"/api/admin/competitions/{comp_id}/certificate-eligibility",
            headers={"Authorization": f"Bearer {data['admin_token']}"}
        )
        assert res.status_code == 200
        body = res.json()
        assert body["results_published"] is True
        assert body["eligible_count"] == 2
        emails = [p["user_email"] for p in body["eligible_participants"]]
        assert data["user1_email"] in emails
        assert data["user2_email"] in emails

        ineligible_emails = [p["user_email"] for p in body["ineligible_participants"]]
        assert data["user3_email"] in ineligible_emails

async def test_award_configuration_update():
    data = await setup_competition_and_users()
    comp_id = data["comp_id"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        put_res = await ac.put(
            f"/api/admin/competitions/{comp_id}/award-config",
            headers={"Authorization": f"Bearer {data['admin_token']}"},
            json={
                "enabled": True,
                "top_rank_limit": 2,
                "tie_handling": "OFFICIAL_RANKING",
                "custom_titles": {"1": "Champion", "2": "Runner Up"}
            }
        )
        assert put_res.status_code == 200
        body = put_res.json()
        assert body["top_rank_limit"] == 2
        assert body["custom_titles"]["1"] == "Champion"

async def test_dual_certificate_generation_and_idempotency():
    data = await setup_competition_and_users()
    comp_id = data["comp_id"]

    await competition_collection.update_one(
        {"_id": ObjectId(comp_id)},
        {"$set": {"results_published": True, "results_published_at": datetime.now(timezone.utc)}}
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        gen_res = await ac.post(
            f"/api/admin/competitions/{comp_id}/certificates/generate",
            headers={"Authorization": f"Bearer {data['admin_token']}"},
            json={"notify_participants": False}
        )
        assert gen_res.status_code == 200
        gen_body = gen_res.json()
        assert gen_body["success"] is True
        assert gen_body["participation_certificates_created"] == 2
        assert gen_body["award_certificates_created"] == 2

        # Verify User1 received TWO certificates: Participation & Award
        u1_certs = await certificate_collection.find({"user_email": data["user1_email"], "activity_id": comp_id}).to_list(length=10)
        assert len(u1_certs) == 2
        types = {c["certificate_type"] for c in u1_certs}
        assert types == {"PARTICIPATION", "AWARD"}

        # IDEMPOTENCY: Run generate again
        gen_again = await ac.post(
            f"/api/admin/competitions/{comp_id}/certificates/generate",
            headers={"Authorization": f"Bearer {data['admin_token']}"},
            json={"notify_participants": False}
        )
        assert gen_again.status_code == 200
        again_body = gen_again.json()
        assert again_body["participation_certificates_created"] == 0
        assert again_body["award_certificates_created"] == 0
        assert again_body["already_existing_certificates_skipped"] == 4

async def test_public_certificate_verification():
    data = await setup_competition_and_users()
    comp_id = data["comp_id"]

    await competition_collection.update_one(
        {"_id": ObjectId(comp_id)},
        {"$set": {"results_published": True, "results_published_at": datetime.now(timezone.utc)}}
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        await ac.post(
            f"/api/admin/competitions/{comp_id}/certificates/generate",
            headers={"Authorization": f"Bearer {data['admin_token']}"},
            json={"notify_participants": False}
        )

        award_cert = await certificate_collection.find_one({
            "user_email": data["user1_email"],
            "activity_id": comp_id,
            "certificate_type": "AWARD"
        })
        assert award_cert is not None
        token = award_cert["verification_token"]

        verify_res = await ac.get(f"/api/public/certificates/verify/{token}")
        assert verify_res.status_code == 200
        v_body = verify_res.json()
        assert v_body["verification_status"] == "VALID"
        assert v_body["participant_name"] == "Alice Winner"
        assert v_body["certificate_type"] == "AWARD"
        assert v_body["rank"] == 1
        assert "password" not in v_body

async def test_certificate_revocation():
    data = await setup_competition_and_users()
    comp_id = data["comp_id"]

    await competition_collection.update_one(
        {"_id": ObjectId(comp_id)},
        {"$set": {"results_published": True, "results_published_at": datetime.now(timezone.utc)}}
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        await ac.post(
            f"/api/admin/competitions/{comp_id}/certificates/generate",
            headers={"Authorization": f"Bearer {data['admin_token']}"},
            json={"notify_participants": False}
        )

        award_cert = await certificate_collection.find_one({
            "user_email": data["user1_email"],
            "activity_id": comp_id,
            "certificate_type": "AWARD"
        })
        cert_id = award_cert["certificate_id"]
        token = award_cert["verification_token"]

        # Revoke certificate
        revoke_res = await ac.post(
            f"/api/admin/certificates/{cert_id}/revoke",
            headers={"Authorization": f"Bearer {data['admin_token']}"},
            json={"reason": "Disqualified post competition due to rule breach."}
        )
        assert revoke_res.status_code == 200
        assert revoke_res.json()["status"] == "REVOKED"

        # Check public verification reflects REVOKED
        verify_res = await ac.get(f"/api/public/certificates/verify/{token}")
        assert verify_res.status_code == 200
        assert verify_res.json()["verification_status"] == "REVOKED"

async def test_user_certificate_library_and_download():
    data = await setup_competition_and_users()
    comp_id = data["comp_id"]

    await competition_collection.update_one(
        {"_id": ObjectId(comp_id)},
        {"$set": {"results_published": True, "results_published_at": datetime.now(timezone.utc)}}
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        await ac.post(
            f"/api/admin/competitions/{comp_id}/certificates/generate",
            headers={"Authorization": f"Bearer {data['admin_token']}"},
            json={"notify_participants": False}
        )

        part_cert = await certificate_collection.find_one({
            "user_email": data["user1_email"],
            "activity_id": comp_id,
            "certificate_type": "PARTICIPATION"
        })
        cert_id = part_cert["certificate_id"]

        # User 1 can view certificates
        me_res = await ac.get(
            "/api/users/me/certificates",
            headers={"Authorization": f"Bearer {data['user1_token']}"}
        )
        assert me_res.status_code == 200
        assert me_res.json()["total"] >= 1

        # User 1 can download PDF
        dl_res = await ac.get(
            f"/api/users/me/certificates/{cert_id}/download",
            headers={"Authorization": f"Bearer {data['user1_token']}"}
        )
        assert dl_res.status_code == 200
        assert dl_res.headers["content-type"] == "application/pdf"
        assert dl_res.content.startswith(b"%PDF-")

        # User 2 CANNOT access User 1's certificate
        unauth_dl = await ac.get(
            f"/api/users/me/certificates/{cert_id}/download",
            headers={"Authorization": f"Bearer {data['user2_token']}"}
        )
        assert unauth_dl.status_code == 404

async def test_top_participation_badge_qualification():
    data = await setup_competition_and_users()
    user1_email = data["user1_email"]
    comp_id = data["comp_id"]

    await competition_collection.update_one(
        {"_id": ObjectId(comp_id)},
        {"$set": {"results_published": True, "results_published_at": datetime.now(timezone.utc)}}
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Set threshold to 2
        await ac.put(
            "/api/admin/badges/config",
            headers={"Authorization": f"Bearer {data['admin_token']}"},
            json={
                "enabled": True,
                "qualification_mode": "THRESHOLD",
                "minimum_qualifying_participations": 2,
                "count_distinct_activities": True,
                "require_completed_activity": True
            }
        )

        # 2nd activity: event check in
        await issued_ticket_collection.insert_one({
            "ticket_ref": "TICK-TEST-999",
            "holder_email": user1_email,
            "booking_id": "BOOK-DISTINCT-02",
            "is_checked_in": True,
            "check_in_time": datetime.now(timezone.utc)
        })

        # Reconcile badges
        rec_res = await ac.post(
            "/api/admin/badges/reconcile",
            headers={"Authorization": f"Bearer {data['admin_token']}"}
        )
        assert rec_res.status_code == 200
        assert rec_res.json()["newly_awarded"] >= 1

        # User 1 can view badge
        badge_res = await ac.get(
            "/api/users/me/badges",
            headers={"Authorization": f"Bearer {data['user1_token']}"}
        )
        assert badge_res.status_code == 200
        badges = badge_res.json()["badges"]
        badge_ids = [b["badge_id"] for b in badges]
        assert "TOP_PARTICIPATION" in badge_ids

async def test_admin_user_profile_summary_and_timeline():
    data = await setup_competition_and_users()
    user1_id = data["user1_id"]
    comp_id = data["comp_id"]

    await competition_collection.update_one(
        {"_id": ObjectId(comp_id)},
        {"$set": {"results_published": True, "results_published_at": datetime.now(timezone.utc)}}
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Generate certs first
        await ac.post(
            f"/api/admin/competitions/{comp_id}/certificates/generate",
            headers={"Authorization": f"Bearer {data['admin_token']}"},
            json={"notify_participants": False}
        )

        # Profile summary
        summary_res = await ac.get(
            f"/api/admin/users/{user1_id}/profile-summary",
            headers={"Authorization": f"Bearer {data['admin_token']}"}
        )
        assert summary_res.status_code == 200
        s_body = summary_res.json()
        assert s_body["user"]["email"] == data["user1_email"]
        act = s_body["activity_summary"]
        assert act["total_competitions_registered"] >= 1
        assert act["total_competitions_participated"] >= 1
        assert act["total_competitions_completed"] >= 1
        assert s_body["achievements"]["total_certificates"] >= 2

        # Activity timeline
        timeline_res = await ac.get(
            f"/api/admin/users/{user1_id}/activity",
            headers={"Authorization": f"Bearer {data['admin_token']}"}
        )
        assert timeline_res.status_code == 200
        tl_body = timeline_res.json()
        assert tl_body["total"] >= 1
        types = [item["activity_type"] for item in tl_body["timeline"]]
        assert "COMPETITION_REGISTRATION" in types
        assert "COMPETITION_SUBMISSION" in types

        # Competitions history
        comps_res = await ac.get(
            f"/api/admin/users/{user1_id}/competitions",
            headers={"Authorization": f"Bearer {data['admin_token']}"}
        )
        assert comps_res.status_code == 200
        assert comps_res.json()["total"] >= 1
        comps_list = comps_res.json()["competitions"]
        comp_item = next((c for c in comps_list if c.get("official_rank") == 1), comps_list[0])
        assert comp_item["official_rank"] == 1
        assert comp_item["has_participation_certificate"] is True
        assert comp_item["has_award_certificate"] is True
