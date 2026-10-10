import requests
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from app.core.config import settings
from app.core.security import create_access_token
from datetime import datetime, timedelta, timezone

BASE = "http://127.0.0.1:8000/api"

async def run_test():
    client = AsyncIOMotorClient(settings.MONGODB_URI)
    db = client.eventora_db
    
    admin_token = create_access_token({"sub": "admin@eventora.com", "type": "admin"})
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    
    # 1. Create a competition that is STILL OPEN
    open_comp = await db["competitions"].insert_one({
        "title": "Open Comp",
        "registration_deadline": datetime.now(timezone.utc) + timedelta(days=1),
        "is_published": True
    })
    
    # 2. Try to download PDF for open comp
    r1 = requests.get(f"{BASE}/admin/advanced-competitions/{open_comp.inserted_id}/participants/export-pdf", headers=admin_headers)
    print("Open Comp Export:", r1.status_code, r1.json())
    
    # 3. Create a competition that is CLOSED
    closed_comp = await db["competitions"].insert_one({
        "title": "Closed Comp",
        "registration_deadline": datetime.now(timezone.utc) - timedelta(days=1),
        "is_published": True
    })
    
    # Add a mock user
    await db["users"].insert_one({
        "email": "testpdf@eventora.com",
        "name": "PDF Tester",
        "phone_number": "9876543210"
    })
    
    # Add a submission
    await db["competition_submissions"].insert_one({
        "competition_id": str(closed_comp.inserted_id),
        "user_email": "testpdf@eventora.com",
        "status": "APPROVED"
    })
    
    # 4. Try to download PDF for closed comp
    r2 = requests.get(f"{BASE}/admin/advanced-competitions/{closed_comp.inserted_id}/participants/export-pdf", headers=admin_headers)
    print("Closed Comp Export Status:", r2.status_code)
    if r2.status_code == 200:
        with open("test_export.pdf", "wb") as f:
            f.write(r2.content)
        print("PDF saved as test_export.pdf")
        
asyncio.run(run_test())
