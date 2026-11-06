"""
test_share_links.py
===================
End-to-end tests for Event and Competition direct share links, universal redirection,
and Swagger/OpenAPI documentation:
1. Event share link endpoint returns direct booking URL and pre-configured social share links.
2. Competition share link endpoint returns direct registration URL and social share links.
3. Universal redirect endpoints (/api/public/share/event/{id} and /api/public/share/competition/{id})
   perform HTTP 307 redirection directly into the booking/registration page when opened by a user.
4. Universal endpoints return full JSON metadata when requested with ?json=true or Accept: application/json.
5. All public event & competition listing, grouped-by-category, and detail endpoints
   are automatically enriched with share URLs and deep links.
6. OpenAPI (/openapi.json) validates successfully with all share models and endpoints present.
"""

import pytest
from datetime import datetime, timezone
from httpx import AsyncClient, ASGITransport
from bson import ObjectId

from app.main import app
from app.core.database import (
    event_collection,
    competition_collection,
)


@pytest.mark.asyncio
async def test_event_and_competition_share_links_end_to_end():
    ts = int(datetime.now().timestamp())

    # 1. Seed an Event
    event_doc = {
        "title": f"Grand Gala Festival {ts}",
        "description": "Annual music and dance live extravaganza with top artists",
        "category": "Music Festival",
        "categories": [
            {"name": "VIP Platinum", "price": 2500.0, "capacity": 50},
            {"name": "General Field", "price": 500.0, "capacity": 300},
        ],
        "date": "2026-12-25",
        "time": "18:00",
        "venue": "Science City Auditorium, Kolkata",
        "price": 500.0,
        "capacity": 350,
        "ticket_enabled": True,
        "is_published": True,
        "is_visible": True,
        "is_deleted": False,
        "created_at": datetime.now(timezone.utc),
    }
    ev_res = await event_collection.insert_one(event_doc)
    event_id = str(ev_res.inserted_id)

    # 2. Seed a Competition
    comp_doc = {
        "title": f"National All-India Dance Cup {ts}",
        "description": "Showcase classical and contemporary dance skills across age categories",
        "category": "Dance",
        "categories": [
            {"name": "Junior", "age_limit": "8-14", "price": 300.0},
            {"name": "Senior", "age_limit": "15-25", "price": 500.0},
        ],
        "participation_fee": 300.0,
        "rules": "1. Minimum age 8. 2. Original choreography only.",
        "start_date": "2026-11-10T00:00:00Z",
        "end_date": "2026-12-30T23:59:59Z",
        "registration_status": "OPEN",
        "is_registration_open": True,
        "is_published": True,
        "is_visible": True,
        "is_deleted": False,
        "created_at": datetime.now(timezone.utc),
    }
    comp_res = await competition_collection.insert_one(comp_doc)
    comp_id = str(comp_res.inserted_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # ── Test 1: Event Share Links API ──
        ev_share_resp = await client.get(f"/api/activities/events/{event_id}/share")
        assert ev_share_resp.status_code == 200, ev_share_resp.text
        ev_share_data = ev_share_resp.json()
        assert ev_share_data["id"] == event_id
        assert ev_share_data["title"] == event_doc["title"]
        assert ev_share_data["item_type"] == "event"
        assert f"/events/{event_id}?action=book_tickets#tickets" in ev_share_data["direct_booking_url"]
        assert f"/events/{event_id}?action=book_tickets#tickets" in ev_share_data["share_url"]
        assert f"/api/public/share/event/{event_id}" in ev_share_data["share_redirect_url"]
        assert "whatsapp" in ev_share_data["social_share_links"]
        assert "facebook" in ev_share_data["social_share_links"]
        assert "twitter" in ev_share_data["social_share_links"]
        assert "telegram" in ev_share_data["social_share_links"]
        assert "api.whatsapp.com" in ev_share_data["social_share_links"]["whatsapp"]

        # ── Test 2: Competition Share Links API (activities & v1) ──
        comp_share_resp1 = await client.get(f"/api/activities/competitions/{comp_id}/share")
        assert comp_share_resp1.status_code == 200, comp_share_resp1.text
        comp_share_data1 = comp_share_resp1.json()
        assert comp_share_data1["id"] == comp_id
        assert comp_share_data1["title"] == comp_doc["title"]
        assert comp_share_data1["item_type"] == "competition"
        assert f"/competitions/{comp_id}?action=register#register" in comp_share_data1["direct_registration_url"]
        assert f"/competitions/{comp_id}?action=register#register" in comp_share_data1["share_url"]
        assert f"/api/public/share/competition/{comp_id}" in comp_share_data1["share_redirect_url"]
        assert "whatsapp" in comp_share_data1["social_share_links"]

        comp_share_resp2 = await client.get(f"/api/v1/competitions/{comp_id}/share")
        assert comp_share_resp2.status_code == 200, comp_share_resp2.text
        comp_share_data2 = comp_share_resp2.json()
        assert comp_share_data2["id"] == comp_id
        assert f"/competitions/{comp_id}?action=register#register" in comp_share_data2["direct_registration_url"]

        # ── Test 3: Universal Redirection for Event ──
        # Browser click should redirect directly (HTTP 307)
        ev_redir_resp = await client.get(f"/api/public/share/event/{event_id}", follow_redirects=False)
        assert ev_redir_resp.status_code == 307
        assert f"/events/{event_id}?action=book_tickets#tickets" in ev_redir_resp.headers["location"]

        # /api/v1/share/event/{event_id} also redirects
        ev_v1_redir = await client.get(f"/api/v1/share/event/{event_id}", follow_redirects=False)
        assert ev_v1_redir.status_code == 307
        assert f"/events/{event_id}?action=book_tickets#tickets" in ev_v1_redir.headers["location"]

        # When requesting JSON metadata with ?json=true
        ev_json_resp = await client.get(f"/api/public/share/event/{event_id}?json=true")
        assert ev_json_resp.status_code == 200
        assert ev_json_resp.json()["id"] == event_id
        assert "direct_booking_url" in ev_json_resp.json()

        # ── Test 4: Universal Redirection for Competition ──
        # Browser click should redirect directly (HTTP 307)
        comp_redir_resp = await client.get(f"/api/public/share/competition/{comp_id}", follow_redirects=False)
        assert comp_redir_resp.status_code == 307
        assert f"/competitions/{comp_id}?action=register#register" in comp_redir_resp.headers["location"]

        # /api/v1/share/competition/{comp_id} also redirects
        comp_v1_redir = await client.get(f"/api/v1/share/competition/{comp_id}", follow_redirects=False)
        assert comp_v1_redir.status_code == 307
        assert f"/competitions/{comp_id}?action=register#register" in comp_v1_redir.headers["location"]

        # When requesting JSON metadata with ?json=true
        comp_json_resp = await client.get(f"/api/public/share/competition/{comp_id}?json=true")
        assert comp_json_resp.status_code == 200
        assert comp_json_resp.json()["id"] == comp_id
        assert "direct_registration_url" in comp_json_resp.json()

        # ── Test 5: Event & Competition Details & Listings Auto-Enrichment ──
        # 5a. Event single detail
        ev_detail = await client.get(f"/api/activities/events/{event_id}")
        assert ev_detail.status_code == 200
        assert "share_url" in ev_detail.json()
        assert "direct_booking_url" in ev_detail.json()
        assert "social_share_links" in ev_detail.json()

        # 5b. Event listing
        ev_list = await client.get("/api/activities/events?limit=100")
        assert ev_list.status_code == 200
        matched_ev = next((x for x in ev_list.json() if x["_id"] == event_id), None)
        assert matched_ev is not None
        assert "direct_booking_url" in matched_ev

        # 5c. Event grouped by category
        ev_by_cat = await client.get("/api/activities/events/by-category")
        assert ev_by_cat.status_code == 200
        all_group_events = [ev for group in ev_by_cat.json().get("grouped", []) for ev in group.get("events", [])]
        matched_grouped_ev = next((x for x in all_group_events if x["_id"] == event_id), None)
        assert matched_grouped_ev is not None
        assert "direct_booking_url" in matched_grouped_ev

        # 5d. Competition single detail (/activities and /v1)
        comp_detail1 = await client.get(f"/api/activities/competitions/{comp_id}")
        assert comp_detail1.status_code == 200
        assert "direct_registration_url" in comp_detail1.json()

        comp_detail2 = await client.get(f"/api/v1/competitions/{comp_id}")
        assert comp_detail2.status_code == 200
        assert "direct_registration_url" in comp_detail2.json()

        # 5e. Competition listing & grouped by category
        comp_list = await client.get("/api/activities/competitions?limit=100")
        assert comp_list.status_code == 200
        matched_comp = next((x for x in comp_list.json() if x["_id"] == comp_id), None)
        assert matched_comp is not None
        assert "direct_registration_url" in matched_comp

        comp_by_cat1 = await client.get("/api/activities/competitions/by-category")
        assert comp_by_cat1.status_code == 200
        all_group_comps = [c for group in comp_by_cat1.json().get("grouped", []) for c in group.get("competitions", [])]
        matched_grouped_comp = next((x for x in all_group_comps if x["_id"] == comp_id), None)
        assert matched_grouped_comp is not None
        assert "direct_registration_url" in matched_grouped_comp

        comp_by_cat2 = await client.get("/api/v1/competitions/by-category")
        assert comp_by_cat2.status_code == 200
        all_group_comps2 = [c for group in comp_by_cat2.json().get("grouped", []) for c in group.get("competitions", [])]
        matched_grouped_comp2 = next((x for x in all_group_comps2 if x["_id"] == comp_id), None)
        assert matched_grouped_comp2 is not None
        assert "direct_registration_url" in matched_grouped_comp2

        # 5f. Homepage events enrichment
        hp_resp = await client.get("/api/v1/homepage")
        assert hp_resp.status_code == 200
        hp_events = hp_resp.json().get("upcoming_events", []) or hp_resp.json().get("events", [])
        matched_hp_ev = next((x for x in hp_events if x.get("_id") == event_id), None)
        if matched_hp_ev:
            assert "direct_booking_url" in matched_hp_ev

        # ── Test 6: Swagger / OpenAPI Schema Verification ──
        openapi_resp = await client.get("/openapi.json")
        assert openapi_resp.status_code == 200
        openapi_data = openapi_resp.json()
        paths = openapi_data.get("paths", {})
        assert "/api/activities/events/{event_id}/share" in paths
        assert "/api/activities/competitions/{comp_id}/share" in paths
        assert "/api/public/share/event/{event_id}" in paths
        assert "/api/public/share/competition/{comp_id}" in paths
        assert "/api/v1/competitions/{comp_id}/share" in paths
        schemas = openapi_data.get("components", {}).get("schemas", {})
        assert "ShareLinkResponse" in schemas
        assert "SocialShareLinks" in schemas

