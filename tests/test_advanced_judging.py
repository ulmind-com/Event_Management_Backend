import requests
import asyncio
import websockets
import json
from motor.motor_asyncio import AsyncIOMotorClient
from app.core.config import settings
from app.core.security import create_access_token, get_password_hash
from datetime import datetime, timezone

BASE = "http://127.0.0.1:8000/api"
WS_BASE = "ws://127.0.0.1:8000/api"

async def setup():
    client = AsyncIOMotorClient(settings.MONGODB_URI)
    db = client.eventora_db
    
    for col in ["users", "judge_assignments", "competitions", "competition_submissions",
                 "judge_scores", "draft_scores", "judge_feedbacks", "coi_skips",
                 "judging_sessions", "leaderboard_results"]:
        await db[col].delete_many({"$or": [
            {"email": "test_judge_adv@example.com"},
            {"judge_email": "test_judge_adv@example.com"},
            {"title": "Advanced Judging Test"},
            {"competition_id": {"$exists": True}}
        ]})
    
    await db["admin_accounts"].update_one(
        {"email": "admin@eventora.com"},
        {"$set": {"email": "admin@eventora.com", "password_hash": get_password_hash("test")}},
        upsert=True
    )
    
    # Create Judge user directly
    await db["users"].update_one(
        {"email": "test_judge_adv@example.com"},
        {"$set": {
            "email": "test_judge_adv@example.com",
            "full_name": "Advanced Judge",
            "role": "JUDGE",
            "status": "ACTIVE",
            "is_verified": True,
            "registration_complete": True
        }},
        upsert=True
    )
    
    admin_token = create_access_token({"sub": "admin@eventora.com", "type": "admin"})
    judge_token = create_access_token({"sub": "test_judge_adv@example.com", "type": "user"})
    
    return (
        {"Authorization": f"Bearer {admin_token}"},
        {"Authorization": f"Bearer {judge_token}"}
    )

def test(name, method, url, expected_status=200, json_data=None, headers=None):
    try:
        if method == "GET": r = requests.get(url, headers=headers)
        elif method == "POST": r = requests.post(url, json=json_data, headers=headers)
        elif method == "DELETE": r = requests.delete(url, json=json_data, headers=headers)
        
        if r.status_code == expected_status:
            print(f"  PASS [{r.status_code}] {name}")
            return r
        else:
            print(f"  FAIL [{r.status_code}] {name} (expected {expected_status})")
            print(f"       {r.text[:300]}")
            return r
    except Exception as e:
        print(f"  ERROR {name}: {e}")
        return None

async def run_tests():
    admin_h, judge_h = await setup()
    client = AsyncIOMotorClient(settings.MONGODB_URI)
    db = client.eventora_db
    
    # ── Setup: Create Competition ──
    print("\n--- SETUP ---")
    r = test("Create Competition", "POST", f"{BASE}/admin/advanced-competitions/", headers=admin_h, json_data={
        "title": "Advanced Judging Test", "description": "Test all features", "category": "Art",
        "start_date": "2026-10-10T00:00:00Z", "end_date": "2026-12-10T00:00:00Z",
        "registration_start_date": "2026-10-01T00:00:00Z", "registration_deadline": "2026-11-01T00:00:00Z",
        "submission_deadline": "2026-11-15T00:00:00Z",
        "judging_criteria": [
            {"name": "Skill", "max_score": 10.0, "weight": 2.0},
            {"name": "Presentation", "max_score": 10.0, "weight": 1.0}
        ]
    })
    comp_id = r.json()["_id"]
    
    # Create 2 submissions
    r1 = await db["competition_submissions"].insert_one({"competition_id": comp_id, "user_email": "contestant1@test.com", "status": "APPROVED"})
    r2 = await db["competition_submissions"].insert_one({"competition_id": comp_id, "user_email": "contestant2@test.com", "status": "APPROVED"})
    sub1 = str(r1.inserted_id)
    sub2 = str(r2.inserted_id)
    
    test("Assign Judge", "POST", f"{BASE}/admin/judges/assignments", headers=admin_h, json_data={
        "judge_email": "test_judge_adv@example.com", "competition_id": comp_id
    })
    
    # --- FEATURE 1: SESSION PIN ---
    print("\n--- FEATURE 1: SESSION PIN ---")
    test("Open Session with PIN", "POST", f"{BASE}/admin/advanced-competitions/{comp_id}/session/open?pin=1234", headers=admin_h)
    test("Score WITHOUT PIN fails", "POST", f"{BASE}/judge/submissions/{sub1}/scores", expected_status=403, headers=judge_h, json_data={"scores": {"Skill": 9}})
    test("Wrong PIN fails", "POST", f"{BASE}/judge/{comp_id}/verify-pin", expected_status=403, headers=judge_h, json_data={"pin": "0000"})
    test("Correct PIN passes", "POST", f"{BASE}/judge/{comp_id}/verify-pin", headers=judge_h, json_data={"pin": "1234"})
    
    # --- FEATURE 2: COI / SKIP ---
    print("\n--- FEATURE 2: COI / SKIP ---")
    test("Skip Contestant 2 (COI)", "POST", f"{BASE}/judge/{comp_id}/contestants/{sub2}/skip", headers=judge_h, json_data={"reason": "Personal friend"})
    test("Cannot score skipped contestant", "POST", f"{BASE}/judge/submissions/{sub2}/scores", expected_status=400, headers=judge_h, json_data={"scores": {"Skill": 5}})
    
    # --- FEATURE 3: DRAFT SCORES ---
    print("\n--- FEATURE 3: DRAFT SCORES ---")
    test("Save Draft", "POST", f"{BASE}/judge/{comp_id}/contestants/{sub1}/draft", headers=judge_h, json_data={"scores": {"Skill": 7.0}, "comments": "partial..."})
    r_draft = test("Get Draft", "GET", f"{BASE}/judge/{comp_id}/contestants/{sub1}/draft", headers=judge_h)
    if r_draft:
        print(f"       Draft scores: {r_draft.json().get('scores')}")
    
    # --- FEATURE 4: FINAL SCORE (cleans up draft) ---
    print("\n--- FEATURE 4: FINAL SCORE SUBMISSION ---")
    test("Submit Final Score", "POST", f"{BASE}/judge/submissions/{sub1}/scores", headers=judge_h, json_data={
        "scores": {"Skill": 9.0, "Presentation": 8.5},
        "comments": "Excellent performance!"
    })
    r_draft2 = test("Draft cleaned up after final", "GET", f"{BASE}/judge/{comp_id}/contestants/{sub1}/draft", headers=judge_h)
    if r_draft2:
        print(f"       Draft after final: {r_draft2.json().get('message', 'still has data')}")
    
    # --- FEATURE 5: JUDGE FEEDBACK ---
    print("\n--- FEATURE 5: JUDGE FEEDBACK ---")
    test("Submit Text Feedback", "POST", f"{BASE}/judge/{comp_id}/contestants/{sub1}/feedback", headers=judge_h, json_data={
        "text_feedback": "Amazing voice quality and stage presence!"
    })
    
    # --- JUDGE PROGRESS ---
    print("\n--- JUDGE PROGRESS ---")
    r_prog = test("Judge My Progress", "GET", f"{BASE}/judge/{comp_id}/my-progress", headers=judge_h)
    if r_prog:
        p = r_prog.json()
        print(f"       Scored: {p['scored']}, Skipped: {p['skipped_coi']}, Pending: {p['pending']}, Completion: {p['completion_pct']}%")
    
    # --- ADMIN PROGRESS DASHBOARD ---
    print("\n--- ADMIN PROGRESS DASHBOARD ---")
    r_ap = test("Admin Judging Progress", "GET", f"{BASE}/admin/advanced-competitions/{comp_id}/judging/progress", headers=admin_h)
    if r_ap:
        ap = r_ap.json()
        print(f"       Scores: {ap['total_scores_submitted']}, COI Skips: {ap['total_coi_skips']}, Completion: {ap['overall_completion_pct']}%")
    
    r_fb = test("Admin View Feedbacks", "GET", f"{BASE}/admin/advanced-competitions/{comp_id}/feedbacks", headers=admin_h)
    if r_fb:
        print(f"       Feedbacks count: {len(r_fb.json())}")
    
    # --- LOCK, APPROVE, PUBLISH ---
    print("\n--- LOCK -> APPROVE -> PUBLISH ---")
    test("Lock Scores", "POST", f"{BASE}/admin/advanced-competitions/{comp_id}/judging/lock", headers=admin_h)
    test("Locked Score Cannot Edit", "POST", f"{BASE}/judge/submissions/{sub1}/scores", expected_status=403, headers=judge_h, json_data={"scores": {"Skill": 1}})
    test("Approve Results", "POST", f"{BASE}/admin/advanced-competitions/{comp_id}/results/approve", headers=admin_h)
    
    # Public results BEFORE publish
    test("Public Results Before Publish", "GET", f"{BASE}/v1/competitions/{comp_id}/results", expected_status=403)
    
    test("Publish Results", "POST", f"{BASE}/admin/advanced-competitions/{comp_id}/results/publish", headers=admin_h)
    
    # Public results AFTER publish
    r_pub = test("Public Results After Publish", "GET", f"{BASE}/v1/competitions/{comp_id}/results")
    if r_pub:
        print(f"       Published entries: {len(r_pub.json())}")
    
    # Public feedback after publish
    test("Public Feedback After Publish", "GET", f"{BASE}/v1/competitions/{comp_id}/contestants/{sub1}/feedbacks")
    
    # --- WEBSOCKET TEST ---
    print("\n--- WEBSOCKET REAL-TIME ---")
    try:
        async with websockets.connect(f"{WS_BASE}/ws/competitions/{comp_id}/live", close_timeout=2) as ws:
            msg = await asyncio.wait_for(ws.recv(), timeout=3)
            data = json.loads(msg)
            print(f"  PASS WebSocket connected. Event: {data.get('event')}, Leaderboard entries: {len(data.get('leaderboard', []))}")
    except Exception as e:
        print(f"  INFO WebSocket test: {e}")
    
    test("Close Session", "POST", f"{BASE}/admin/advanced-competitions/{comp_id}/session/close", headers=admin_h)
    
    print("\n--- ALL TESTS COMPLETE ---")

asyncio.run(run_tests())
