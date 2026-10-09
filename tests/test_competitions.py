import requests
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from app.core.config import settings
from app.core.security import create_access_token, get_password_hash
from time import sleep

BASE = "http://127.0.0.1:8000/api"

async def setup():
    client = AsyncIOMotorClient(settings.MONGODB_URI)
    db = client.eventora_db
    
    # Clear collections
    await db["competitions"].delete_many({"title": "Test Advanced Competition"})
    await db["competition_registrations"].delete_many({})
    await db["competition_submissions"].delete_many({})
    await db["judge_scores"].delete_many({})
    await db["leaderboard_results"].delete_many({})
    
    # Create Mock Admin
    await db["admin_accounts"].update_one(
        {"email": "admin@eventora.com"}, 
        {"$set": {"email": "admin@eventora.com", "password_hash": get_password_hash("test")}},
        upsert=True
    )
    
    # Create Mock User (Born in 2000 -> Age 26 in 2026)
    await db["users"].update_one(
        {"email": "sagnik_comp@example.com"},
        {"$set": {
            "email": "sagnik_comp@example.com", 
            "full_name": "Sagnik Comp", 
            "password_hash": get_password_hash("test"), 
            "is_verified": True, 
            "registration_complete": True,
            "date_of_birth": "2000-01-01"
        }},
        upsert=True
    )
    
    # Generate tokens
    admin_token = create_access_token({"sub": "admin@eventora.com", "type": "admin"})
    user_token = create_access_token({"sub": "sagnik_comp@example.com", "type": "user"})
    
    return {"Authorization": f"Bearer {admin_token}"}, {"Authorization": f"Bearer {user_token}"}

def test(name, method, url, expected_status=200, json_data=None, data_form=None, files=None, headers=None):
    try:
        if method == "GET":
            r = requests.get(url, headers=headers)
        elif method == "POST":
            r = requests.post(url, json=json_data, data=data_form, files=files, headers=headers)
        elif method == "DELETE":
            r = requests.delete(url, headers=headers)
        
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
    admin_headers, user_headers = await setup()
    
    # 1. Create Competition
    r_comp = test("Create Competition", "POST", f"{BASE}/admin/advanced-competitions/", headers=admin_headers, json_data={
        "title": "Test Advanced Competition",
        "description": "A very advanced test.",
        "category": "Technology",
        "start_date": "2026-10-10T00:00:00Z",
        "end_date": "2026-12-10T00:00:00Z",
        "registration_start_date": "2026-10-01T00:00:00Z",
        "registration_deadline": "2026-11-01T00:00:00Z",
        "submission_deadline": "2026-11-15T00:00:00Z",
        "min_age": 18,
        "max_age": 30,
        "participation_fee": 0.0,
        "is_published": True,
        "dynamic_submission_fields": [
            {"name": "Project Video", "label": "Video", "type": "VIDEO", "required": True},
            {"name": "GitHub Link", "label": "Code", "type": "URL", "required": True}
        ],
        "judging_criteria": [
            {"name": "Innovation", "max_score": 10.0, "weight": 2.0},
            {"name": "Code Quality", "max_score": 10.0, "weight": 1.0}
        ]
    })
    comp_id = r_comp.json()["_id"] if r_comp and r_comp.status_code == 200 else ""
    
    # 2. Check Eligibility
    print("\n--- ELIGIBILITY & REGISTRATION ---")
    r_elig = test("Check Eligibility", "GET", f"{BASE}/v1/competitions/{comp_id}/eligibility", headers=user_headers)
    if r_elig:
        print("   Status:", r_elig.json().get("status"))
        
    test("Register User", "POST", f"{BASE}/v1/competitions/{comp_id}/register?rules_accepted=true", headers=user_headers)
    
    # 3. Dynamic Submission
    print("\n--- SUBMISSION & JUDGING ---")
    # To mock file uploads using requests
    files = [
        ('files', ('video.mp4', b'dummy video content', 'video/mp4')),
        ('files', ('github.txt', b'github link', 'text/plain'))
    ]
    data = {'field_names': 'Project Video, GitHub Link'}
    
    r_sub = test("Submit Entry", "POST", f"{BASE}/v1/competitions/{comp_id}/submissions", data_form=data, files=files, headers=user_headers)
    sub_id = r_sub.json()["submission_id"] if r_sub and r_sub.status_code == 200 else ""
    
    # 4. Judge Scoring
    # (In a real app, an admin needs to APPROVE the submission first, but our simplistic judge endpoint allows SUBMITTED or APPROVED)
    test("Judge Submits Score", "POST", f"{BASE}/judge/submissions/{sub_id}/scores", headers=user_headers, json_data={
        "scores": {"Innovation": 9.0, "Code Quality": 8.0},
        "comments": "Great project!"
    })
    
    # 5. Leaderboard Recalculation
    print("\n--- LEADERBOARD & CLEANUP ---")
    r_rank = test("Recalculate Leaderboard", "POST", f"{BASE}/admin/advanced-competitions/{comp_id}/recalculate-rankings", headers=admin_headers)
    if r_rank:
        print(f"   Total Ranked: {r_rank.json().get('total_ranked')}")
        
    # 6. Deletion (Cleanup)
    test("Delete Competition", "DELETE", f"{BASE}/admin/advanced-competitions/{comp_id}", headers=admin_headers)
    
asyncio.run(run_tests())
