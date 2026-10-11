from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
from typing import Optional
from app.api.deps import get_current_user
from app.core.database import user_collection, registration_collection, booking_collection, order_collection, certificate_collection, reward_collection, user_badge_award_collection
from app.schemas.profile import ProfileUpdateReq, ProfileUpdateRes, DashboardRes
from app.services.cloudinary_srv import upload_image
from datetime import datetime, timezone

router = APIRouter()

@router.get("/me")
async def get_my_profile(current_user: dict = Depends(get_current_user)):
    """Retrieve own profile."""
    if "_id" in current_user:
        del current_user["_id"]
    return current_user

@router.put("/me")
async def update_my_profile(
    name: Optional[str] = Form(None),
    phone_number: Optional[int] = Form(None),
    address: Optional[str] = Form(None),
    parent_phone_number: Optional[int] = Form(None),
    profile_photo: Optional[UploadFile] = File(None),
    current_user: dict = Depends(get_current_user)
):
    """Update own profile."""
    update_data = {}
    if name is not None: update_data["name"] = name
    if phone_number is not None: update_data["phone_number"] = phone_number
    if address is not None: update_data["address"] = address
    if parent_phone_number is not None: update_data["parent_phone_number"] = parent_phone_number
    
    if profile_photo:
        profile_content = await profile_photo.read()
        if len(profile_content) > 0:
            profile_url = upload_image(profile_content, folder="eventora/profiles")
            if profile_url:
                update_data["profile_photo"] = profile_url
                
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields provided for update")
        
    update_data["updated_at"] = datetime.now(timezone.utc)
    
    await user_collection.update_one({"email": current_user["email"]}, {"$set": update_data})
    
    updated_user = await user_collection.find_one({"email": current_user["email"]}, {"_id": 0})
    return {"message": "Profile updated successfully", "user": updated_user}

@router.get("/dashboard")
async def get_my_dashboard(current_user: dict = Depends(get_current_user)):
    """Retrieve full dashboard data for the user."""
    email = current_user["email"]
    
    # 1. Fetch Registrations (Events & Courses)
    events = await registration_collection.find({"email": email, "activity_type": "event"}).to_list(length=100)
    courses = await registration_collection.find({"email": email, "activity_type": "course"}).to_list(length=100)
    
    # 2. Fetch Competitions and extract Rank
    competitions = await registration_collection.find({"email": email, "activity_type": "competition"}).to_list(length=100)
    my_ranks = []
    for comp in competitions:
        if "rank" in comp:
            my_ranks.append({
                "competition_id": str(comp.get("activity_id")),
                "rank": comp["rank"],
                "score": comp.get("score")
            })
    
    # 3. Fetch Bookings (Tickets)
    tickets = await booking_collection.find({"email": email}).to_list(length=100)
    
    # 4. Fetch Merchandise Orders
    orders = await order_collection.find({"email": email}).to_list(length=100)
    
    # 5. Fetch Certificates
    certs = await certificate_collection.find({"$or": [{"email": email}, {"user_email": email}]}).to_list(length=100)
    
    # 6. Fetch Rewards and Calculate Total Coins
    rewards_docs = await reward_collection.find({"email": email}).to_list(length=100)
    total_coins = sum([r.get("coins", 0) for r in rewards_docs])
    
    # 7. Fetch Badges
    badges = await user_badge_award_collection.find({"$or": [{"user_email": email}, {"email": email}], "status": "ACTIVE"}).to_list(length=100)
    
    # Strip _id from all for JSON serialization
    for lst in [events, competitions, courses, tickets, orders, certs, rewards_docs, badges]:
        for item in lst:
            item["_id"] = str(item["_id"])
            if "activity_id" in item and type(item["activity_id"]) != str:
                item["activity_id"] = str(item["activity_id"])
            
    if "_id" in current_user:
        del current_user["_id"]

    return {
        "profile": current_user,
        "my_events": events,
        "my_tickets": tickets,
        "my_competitions": competitions,
        "my_ranks": my_ranks,
        "my_courses": courses,
        "my_certificates": certs,
        "my_badges": badges,
        "my_rewards_and_coins": {
            "total_coins": total_coins,
            "reward_history": rewards_docs
        },
        "my_merchandise_orders": orders
    }
