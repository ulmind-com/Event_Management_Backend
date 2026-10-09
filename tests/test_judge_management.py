import requests
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from app.core.config import settings
from app.core.security import create_access_token, get_password_hash
import jwt
from datetime import datetime, timezone

BASE = "http://127.0.0.1:8000/api"

async def setup():
    client = AsyncIOMotorClient(settings.MONGODB_URI)
    db = client.eventora_db
    
    # Clean up
    await db["users"].delete_many({"email": "test_judge@example.com"})
    await db["judge_assignments"].delete_many({})
    await db["competitions"].delete_many({"title": "Test Judging Competition"})
    await db["competition_submissions"].delete_many({})
    await db["judge_scores"].delete_many({})
    
    # Mock Admin
    await db["admin_accounts"].update_one(
        {"email": "admin@eventora.com"}, 
        {"$set": {"email": "admin@eventora.com", "password_hash": get_password_hash("test")}},
        upsert=True
    )
    admin_token = create_access_token({"sub": "admin@eventora.com", "type": "admin"})
    
    return {"Authorization": f"Bearer {admin_token}"}

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
            print(f"       {r.text[:200]}")
            return r
    except Exception as e:
        print(f"  ERROR {name}: {e}")
        return None

async def run_tests():
    admin_headers = await setup()
    
    print("\n--- 1. ADMIN CREATES JUDGE ---")
    r_create = test("Create Judge", "POST", f"{BASE}/admin/judges/", headers=admin_headers, json_data={
        "full_name": "Test Judge",
        "email": "test_judge@example.com"
    })
    
    print("\n--- 2. ADMIN SENDS INVITATION (Generates Token) ---")
    test("Send Invitation", "POST", f"{BASE}/admin/judges/test_judge@example.com/send-invitation", headers=admin_headers)
    
    # Mock token since we know the secret for the test
    expire = datetime.now(timezone.utc)
    payload = {"sub": "test_judge@example.com", "type": "judge_setup", "exp": expire.timestamp() + 86400}
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    
    print("\n--- 3. JUDGE SETUPS PASSWORD ---")
    test("Judge Password Setup", "POST", f"{BASE}/judge/setup", json_data={
        "token": token,
        "new_password": "secure_password_123"
    })
    
    # Authenticate Judge
    judge_token = create_access_token({"sub": "test_judge@example.com", "type": "user"})
    judge_headers = {"Authorization": f"Bearer {judge_token}"}
    
    print("\n--- 4. CREATE COMPETITION & SUBMISSION ---")
    r_comp = test("Create Competition", "POST", f"{BASE}/admin/advanced-competitions/", headers=admin_headers, json_data={
        "title": "Test Judging Competition",
        "description": "Test", "category": "Art",
        "start_date": "2026-10-10T00:00:00Z", "end_date": "2026-12-10T00:00:00Z",
        "registration_start_date": "2026-10-01T00:00:00Z", "registration_deadline": "2026-11-01T00:00:00Z",
        "submission_deadline": "2026-11-15T00:00:00Z",
        "judging_criteria": [
            {"name": "Skill", "max_score": 10.0, "weight": 1.0}
        ]
    })
    comp_id = r_comp.json()["_id"]
    
    # Manually inject a submission
    client = AsyncIOMotorClient(settings.MONGODB_URI)
    db = client.eventora_db
    res = await db["competition_submissions"].insert_one({
        "competition_id": comp_id, "user_email": "contestant@example.com", "status": "APPROVED"
    })
    sub_id = str(res.inserted_id)
    
    print("\n--- 5. JUDGE ACCESS (Unassigned vs Assigned) ---")
    test("Unassigned Access Fails", "GET", f"{BASE}/judge/{comp_id}/contestants", expected_status=403, headers=judge_headers)
    
    test("Admin Assigns Judge", "POST", f"{BASE}/admin/judges/assignments", headers=admin_headers, json_data={
        "judge_email": "test_judge@example.com",
        "competition_id": comp_id
    })
    
    test("Assigned Access Succeeds", "GET", f"{BASE}/judge/{comp_id}/contestants", expected_status=200, headers=judge_headers)
    
    print("\n--- 6. JUDGE SCORING & LOCKING ---")
    test("Judge Scores Contestant", "POST", f"{BASE}/judge/submissions/{sub_id}/scores", headers=judge_headers, json_data={
        "scores": {"Skill": 9.5}
    })
    
    test("Admin Locks Scores", "POST", f"{BASE}/admin/advanced-competitions/{comp_id}/judging/lock", headers=admin_headers)
    
    test("Judge Cannot Edit Locked Score", "POST", f"{BASE}/judge/submissions/{sub_id}/scores", expected_status=403, headers=judge_headers, json_data={
        "scores": {"Skill": 8.0}
    })
    
    print("\n--- 7. ADMIN APPROVES & PUBLISHES LEADERBOARD ---")
    test("Admin Approves Results", "POST", f"{BASE}/admin/advanced-competitions/{comp_id}/results/approve", headers=admin_headers)
    test("Admin Publishes Results", "POST", f"{BASE}/admin/advanced-competitions/{comp_id}/results/publish", headers=admin_headers)
    
asyncio.run(run_tests())
