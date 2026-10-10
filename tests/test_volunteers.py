import requests
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from app.core.config import settings
from app.core.security import create_access_token
import uuid

BASE = "http://127.0.0.1:8000/api"

async def run_test():
    client = AsyncIOMotorClient(settings.MONGODB_URI)
    db = client.eventora_db
    
    admin_token = create_access_token({"sub": "admin@eventora.com", "type": "admin"})
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    
    vol_email = f"vol_{uuid.uuid4().hex[:6]}@test.com"
    
    # Add volunteer
    r1 = requests.post(f"{BASE}/admin/volunteers/", json={"email": vol_email}, headers=admin_headers)
    print("Add Volunteer:", r1.status_code, r1.json())
    
    # Volunteer Login Step 1
    r2 = requests.post(f"{BASE}/volunteer/auth/login/step1", json={"email": vol_email})
    print("Volunteer OTP sent:", r2.status_code)
    
    otp_doc = await db["otps"].find_one({"email": vol_email})
    otp = otp_doc["otp"]
    
    # Volunteer Login Step 2
    r3 = requests.post(f"{BASE}/volunteer/auth/login/step2", json={"email": vol_email, "otp": otp})
    print("Volunteer Login:", r3.status_code)
    vol_token = r3.json()["access_token"]
    
    # Delete Volunteer
    r4 = requests.delete(f"{BASE}/admin/volunteers/{vol_email}", headers=admin_headers)
    print("Delete Volunteer:", r4.status_code)

asyncio.run(run_test())
