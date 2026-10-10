from pymongo import MongoClient

# Simulate DB state for Rank & Certificate
from app.core.config import settings; client = MongoClient(settings.MONGODB_URI)
db = client.eventora_db

# Get email of any recent user
user = db.users.find_one()
if user:
    email = user["email"]

    # Insert dummy data for rank and certificate
    comp_reg = db.registrations.find_one({"email": email, "activity_type": "competition"})
    if not comp_reg:
        comp_id = str(db.competitions.insert_one({"title": "Coding Contest", "prize": "$100"}).inserted_id)
        db.registrations.insert_one({
            "email": email, 
            "activity_id": comp_id, 
            "activity_type": "competition", 
            "status": "completed",
            "rank": 1,
            "score": 98
        })

    cert = db.certificates.find_one({"email": email})
    if not cert:
        db.certificates.insert_one({"email": email, "title": "1st Place - Coding Contest", "issue_date": "2026-10-10"})
        db.rewards.insert_one({"email": email, "coins": 500, "reason": "1st Place Winner"})

    print("Dummy data inserted successfully.")
else:
    print("No users found in database.")
