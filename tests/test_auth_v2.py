import requests
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from app.core.config import settings
from app.core.security import get_password_hash
import uuid

BASE = "http://127.0.0.1:8000/api"

async def setup():
    client = AsyncIOMotorClient(settings.MONGODB_URI)
    db = client.eventora_db
    test_email = f"test_user_{uuid.uuid4().hex[:6]}@example.com"
    judge_email = f"test_judge_{uuid.uuid4().hex[:6]}@example.com"
    
    # Pre-create judge
    await db["users"].insert_one({
        "email": judge_email,
        "role": "JUDGE",
        "registration_complete": True
    })
    
    return db, test_email, judge_email

def test(name, method, url, expected_status=200, data=None):
    if method == "POST": r = requests.post(url, json=data) if isinstance(data, dict) else requests.post(url, data=data)
    else: r = requests.get(url)
    
    if r.status_code == expected_status:
        print(f"  PASS [{r.status_code}] {name}")
        return r
    else:
        print(f"  FAIL [{r.status_code}] {name} (expected {expected_status})")
        print(f"       {r.text[:300]}")
        return r

async def run_tests():
    db, test_email, judge_email = await setup()
    
    print("\n--- NORMAL USER FLOW ---")
    # Step 1: Send OTP
    test("Send OTP", "POST", f"{BASE}/auth/register/step1", data={"email": test_email}, expected_status=200)
    
    # Get OTP from DB
    otp_doc = await db["otps"].find_one({"email": test_email})
    otp = otp_doc["otp"]
    
    # Step 2: Verify OTP
    r2 = test("Verify OTP", "POST", f"{BASE}/auth/register/step2", data={"email": test_email, "otp": otp}, expected_status=200)
    reg_token = r2.json()["registration_token"]
    
    # Step 3: Register Details
    # Because it's multipart/form-data, we need to pass a file. Let's mock a file using requests
    files = {
        "govt_id_proof": ("test.txt", b"test", "text/plain"),
    }
    data = {
        "password": "Password123",
        "confirm_password": "Password123",
        "name": "Test User",
        "phone_number": "1234567890",
        "date_of_birth": "2000-01-01",
        "address": "123 Test St"
    }
    headers = {"Authorization": f"Bearer {reg_token}"}
    r3 = requests.post(f"{BASE}/auth/register/step3", data=data, files=files, headers=headers)
    if r3.status_code == 200:
        print("  PASS [200] Register Step 3")
    else:
        print(f"  FAIL [{r3.status_code}] Register Step 3: {r3.text}")

    # Login
    test("Login Success", "POST", f"{BASE}/auth/login", data={"email": test_email, "password": "Password123"}, expected_status=200)
    test("Login Wrong Password", "POST", f"{BASE}/auth/login", data={"email": test_email, "password": "WrongPassword"}, expected_status=401)
    
    print("\n--- JUDGE LOGIN FLOW ---")
    test("Judge Step 1", "POST", f"{BASE}/judge/auth/login/step1", data={"email": judge_email}, expected_status=200)
    otp_doc_j = await db["otps"].find_one({"email": judge_email})
    otp_j = otp_doc_j["otp"]
    test("Judge Step 2", "POST", f"{BASE}/judge/auth/login/step2", data={"email": judge_email, "otp": otp_j}, expected_status=200)

asyncio.run(run_tests())
