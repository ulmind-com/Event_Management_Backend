import re
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
from typing import Optional, Tuple, List
from bson import ObjectId
from app.api.deps import get_current_user, get_current_user_allow_incomplete
from app.core.database import (
    user_collection, registration_collection, booking_collection, order_collection,
    certificate_collection, reward_collection, user_badge_award_collection,
    competition_collection, competition_registration_collection,
    competition_submission_collection, judge_score_collection, merchandise_order_collection,
    coin_ledger_collection, course_collection, leaderboard_result_collection
)
from app.schemas.profile import ProfileUpdateReq, ProfileUpdateRes, DashboardRes
from app.services.cloudinary_srv import upload_image
from app.services.wallet_service import WalletService
from datetime import datetime, timezone, date

from app.core.profile_lifecycle import (
    calculate_age,
    parse_date,
    check_profile_completion,
    get_profile_form_schema,
    enrich_user_with_profile_status,
)

router = APIRouter()


@router.get("/me")
async def get_my_profile(current_user: dict = Depends(get_current_user_allow_incomplete)):
    """Retrieve own profile, including profile completeness status and profile input form schema."""
    if "_id" in current_user:
        del current_user["_id"]
    if "password_hash" in current_user:
        del current_user["password_hash"]

    return enrich_user_with_profile_status(current_user)



@router.put("/me")
async def update_my_profile(
    name: Optional[str] = Form(None),
    phone_number: Optional[str] = Form(None),
    date_of_birth: Optional[str] = Form(None),
    address: Optional[str] = Form(None),
    parent_phone_number: Optional[str] = Form(None),
    profile_photo: Optional[UploadFile] = File(None),
    current_user: dict = Depends(get_current_user_allow_incomplete)
):
    """
    Update own profile. When all required fields (name, phone, dob, address) are present,
    automatically marks registration_complete and profile_completed as True.
    """
    update_data = {}
    if name is not None and name.strip():
        update_data["name"] = name.strip()

    if phone_number is not None and str(phone_number).strip():
        cleaned_phone = re.sub(r"\D", "", str(phone_number).strip()) if 're' in globals() else str(phone_number).strip()
        try:
            update_data["phone_number"] = int(cleaned_phone)
        except Exception:
            update_data["phone_number"] = cleaned_phone

    if date_of_birth is not None and date_of_birth.strip():
        dob_str = date_of_birth.strip()
        parsed_dob = parse_date(dob_str)
        if parsed_dob:
            update_data["date_of_birth"] = parsed_dob.strftime("%Y-%m-%d")
            update_data["age"] = calculate_age(parsed_dob)
        else:
            update_data["date_of_birth"] = dob_str

    if address is not None and address.strip():
        update_data["address"] = address.strip()

    if parent_phone_number is not None and str(parent_phone_number).strip():
        cleaned_p_phone = re.sub(r"\D", "", str(parent_phone_number).strip()) if 're' in globals() else str(parent_phone_number).strip()
        try:
            update_data["parent_phone_number"] = int(cleaned_p_phone)
        except Exception:
            update_data["parent_phone_number"] = cleaned_p_phone

    if profile_photo:
        profile_content = await profile_photo.read()
        if len(profile_content) > 0:
            profile_url = upload_image(
                profile_content,
                folder="eventora/profiles",
                filename=profile_photo.filename,
            )
            if profile_url:
                update_data["profile_photo"] = profile_url
            else:
                raise HTTPException(
                    status_code=500,
                    detail="Failed to upload profile photo to cloud storage. Please try again."
                )

    if not update_data:
        raise HTTPException(status_code=400, detail="No fields provided for update")

    # Evaluate whether profile is now complete
    merged_profile = {**current_user, **update_data}
    is_completed, missing = check_profile_completion(merged_profile)
    if is_completed:
        update_data["registration_complete"] = True
        update_data["is_profile_completed"] = True
        update_data["profile_completed"] = True
        update_data["first_time_login"] = False
        if not current_user.get("profile_completed_at"):
            update_data["profile_completed_at"] = datetime.now(timezone.utc)

    update_data["updated_at"] = datetime.now(timezone.utc)

    await user_collection.update_one({"email": current_user["email"]}, {"$set": update_data})

    updated_user = await user_collection.find_one({"email": current_user["email"]}, {"_id": 0, "password_hash": 0})
    updated_user = enrich_user_with_profile_status(updated_user)

    return {
        "message": "Profile setup completed successfully" if updated_user.get("is_profile_completed") else "Profile updated successfully",
        "is_profile_completed": updated_user.get("is_profile_completed"),
        "requires_profile_completion": updated_user.get("requires_profile_completion"),
        "prompt_profile_form": updated_user.get("prompt_profile_form"),
        "first_time_login": updated_user.get("first_time_login"),
        "missing_fields": updated_user.get("missing_fields", []),
        "next_step": updated_user.get("next_step"),
        "redirect_url": updated_user.get("redirect_url"),
        "profile_form": updated_user.get("profile_form"),
        "user": updated_user
    }


@router.post("/complete")
async def complete_profile_json(
    req: ProfileUpdateReq,
    current_user: dict = Depends(get_current_user_allow_incomplete),
):
    """JSON endpoint to complete or update own profile."""
    update_data = {}
    if req.name and req.name.strip():
        update_data["name"] = req.name.strip()
    if req.phone_number:
        try:
            update_data["phone_number"] = int(req.phone_number)
        except Exception:
            update_data["phone_number"] = req.phone_number
    if req.date_of_birth and req.date_of_birth.strip():
        parsed_dob = parse_date(req.date_of_birth.strip())
        if parsed_dob:
            update_data["date_of_birth"] = parsed_dob.strftime("%Y-%m-%d")
            update_data["age"] = calculate_age(parsed_dob)
        else:
            update_data["date_of_birth"] = req.date_of_birth.strip()
    if req.address and req.address.strip():
        update_data["address"] = req.address.strip()
    if req.parent_phone_number:
        try:
            update_data["parent_phone_number"] = int(req.parent_phone_number)
        except Exception:
            update_data["parent_phone_number"] = req.parent_phone_number

    if not update_data:
        raise HTTPException(status_code=400, detail="No fields provided for profile completion")

    merged = {**current_user, **update_data}
    is_completed, missing = check_profile_completion(merged)
    if is_completed:
        update_data["registration_complete"] = True
        update_data["is_profile_completed"] = True
        update_data["profile_completed"] = True
        update_data["first_time_login"] = False
        if not current_user.get("profile_completed_at"):
            update_data["profile_completed_at"] = datetime.now(timezone.utc)

    update_data["updated_at"] = datetime.now(timezone.utc)
    await user_collection.update_one({"email": current_user["email"]}, {"$set": update_data})

    updated_user = await user_collection.find_one({"email": current_user["email"]}, {"_id": 0, "password_hash": 0})
    updated_user = enrich_user_with_profile_status(updated_user)

    return {
        "message": "Profile setup completed successfully" if updated_user.get("is_profile_completed") else "Profile updated successfully",
        "is_profile_completed": updated_user.get("is_profile_completed"),
        "requires_profile_completion": updated_user.get("requires_profile_completion"),
        "prompt_profile_form": updated_user.get("prompt_profile_form"),
        "first_time_login": updated_user.get("first_time_login"),
        "missing_fields": updated_user.get("missing_fields", []),
        "next_step": updated_user.get("next_step"),
        "redirect_url": updated_user.get("redirect_url"),
        "profile_form": updated_user.get("profile_form"),
        "user": updated_user,
    }

@router.get("/dashboard")
async def get_my_dashboard(current_user: dict = Depends(get_current_user_allow_incomplete)):
    """Retrieve full dashboard data for the user."""
    email = current_user["email"]
    current_user = enrich_user_with_profile_status(current_user)
    is_completed = current_user["is_profile_completed"]
    missing = current_user["missing_fields"]
    form_schema = current_user["profile_form"]
    
    # 1. Fetch Registrations (Events & Courses)
    events = await registration_collection.find({"email": email, "activity_type": "event"}).to_list(length=100)
    for ev in events:
        ev["is_confirmed"] = (ev.get("status") == "CONFIRMED" or ev.get("payment_status") in ("PAID", "NOT_REQUIRED"))

    # Workshops are a kind of course on the backend, so surface both under my_courses.
    courses = await registration_collection.find({"email": email, "activity_type": {"$in": ["course", "workshop"]}}).to_list(length=100)
    for cr in courses:
        cr["is_confirmed"] = (cr.get("status") == "CONFIRMED" or cr.get("payment_status") in ("PAID", "NOT_REQUIRED"))
        act_id = cr.get("activity_id")
        if act_id:
            try:
                c_doc = await course_collection.find_one({"_id": ObjectId(str(act_id))})
            except Exception:
                c_doc = await course_collection.find_one({"_id": act_id})
            if c_doc:
                cr["title"] = c_doc.get("title")
                cr["activity_title"] = c_doc.get("title")
                cr["category"] = c_doc.get("category")
                if "price" not in cr:
                    cr["price"] = c_doc.get("price")
    
    # 2. Fetch Competitions and extract Rank
    competitions = await registration_collection.find({"email": email, "activity_type": "competition"}).to_list(length=100)
    my_ranks = []
    for comp in competitions:
        comp["competition_id"] = str(comp.get("activity_id") or comp.get("competition_id") or "")
        comp["is_confirmed"] = (comp.get("status") == "CONFIRMED" or comp.get("payment_status") in ("PAID", "NOT_REQUIRED"))
        if "rank" in comp:
            my_ranks.append({
                "competition_id": str(comp.get("activity_id")),
                "competition_title": comp.get("activity_title") or comp.get("title") or "Competition",
                "rank": comp["rank"],
                "score": comp.get("score")
            })

    # Also fetch modern advanced competitions
    try:
        adv_regs = await competition_registration_collection.find({"user_email": email}).to_list(length=100)
        for ar in adv_regs:
            comp_id_str = str(ar.get("competition_id", ""))
            c_info = None
            if ObjectId.is_valid(comp_id_str):
                c_info = await competition_collection.find_one({"_id": ObjectId(comp_id_str)})
            
            sub = await competition_submission_collection.find_one({"competition_id": comp_id_str, "user_email": email})
            sub_id = str(sub["_id"]) if sub else ""

            score_match = [{"participant_id": email}, {"user_email": email}]
            if sub_id:
                score_match.append({"submission_id": sub_id})
            score_doc = await judge_score_collection.find_one({"competition_id": comp_id_str, "$or": score_match})

            lb_match = [{"participant_id": email}, {"email": email}]
            if sub_id:
                lb_match.append({"submission_id": sub_id})
            lb_doc = await leaderboard_result_collection.find_one({"competition_id": comp_id_str, "$or": lb_match})

            rank_val = ar.get("rank") or (lb_doc.get("rank") if lb_doc else None) or (score_doc.get("rank") if score_doc else None)
            score_val = ar.get("score") or (lb_doc.get("total_score") or lb_doc.get("score") if lb_doc else None) or (score_doc.get("total_score") if score_doc else None)
            
            c_title = c_info.get("title") if c_info else "Competition"
            is_confirmed = (ar.get("status") == "CONFIRMED" or ar.get("payment_status") in ("PAID", "NOT_REQUIRED"))
            comp_entry = {
                "_id": str(ar["_id"]),
                "registration_reference": ar.get("registration_reference"),
                "competition_id": comp_id_str,
                "title": c_title,
                "name": c_title,
                "status": ar.get("status"),
                "payment_status": ar.get("payment_status"),
                "is_confirmed": is_confirmed,
                "fee": ar.get("fee", 0),
                "has_submitted": bool(sub),
                "submission_id": str(sub["_id"]) if sub else None,
                "submitted_at": sub.get("submitted_at") if sub else None,
                "rank": rank_val,
                "score": score_val,
                "registered_at": ar.get("registered_at"),
            }
            competitions.append(comp_entry)
            if rank_val:
                my_ranks.append({
                    "competition_id": comp_id_str,
                    "competition_title": c_title,
                    "rank": rank_val,
                    "score": score_val
                })
    except Exception:
        pass
    
    # 3. Fetch Bookings (Tickets)
    tickets = await booking_collection.find({"$or": [{"user_email": email}, {"email": email}]}).to_list(length=100)
    for t in tickets:
        t["is_confirmed"] = (t.get("booking_status") == "CONFIRMED" or t.get("payment_status") in ("PAID", "NOT_REQUIRED"))
    
    # 4. Fetch Merchandise Orders
    orders = await order_collection.find({"$or": [{"user_email": email}, {"email": email}]}).to_list(length=100)
    try:
        merch_orders = await merchandise_order_collection.find({"$or": [{"user_email": email}, {"email": email}]}).to_list(length=100)
        existing_order_ids = {str(o.get("_id")) for o in orders}
        for mo in merch_orders:
            if str(mo.get("_id")) not in existing_order_ids:
                orders.append(mo)
    except Exception:
        pass
    
    # 5. Fetch Certificates
    certs = await certificate_collection.find({"$or": [{"email": email}, {"user_email": email}]}).to_list(length=100)
    
    # 6. Fetch Rewards and Calculate Total Coins (integrated with WalletService)
    rewards_docs = await reward_collection.find({"email": email}).to_list(length=100)
    total_coins = sum([r.get("coins", 0) for r in rewards_docs])
    available_coins = 0
    total_earned_coins = 0
    total_redeemed_coins = 0
    try:
        wallet = await WalletService.get_or_create_wallet(email)
        if wallet and "available_coins" in wallet:
            available_coins = int(wallet.get("available_coins", 0))
            total_earned_coins = int(wallet.get("total_earned_coins", 0))
            total_redeemed_coins = int(wallet.get("total_redeemed_coins", 0))
            total_coins = max(total_coins, available_coins)
    except Exception:
        pass

    # Fetch immutable coin ledger transactions (both CREDITS and DEBITS / Redemptions)
    ledger_txns = await coin_ledger_collection.find({"email": email}).sort("created_at", -1).to_list(length=100)
    for lt in ledger_txns:
        if "_id" in lt:
            lt["_id"] = str(lt["_id"])
    
    # 7. Fetch Badges
    badges = await user_badge_award_collection.find({"$or": [{"user_email": email}, {"email": email}], "status": "ACTIVE"}).to_list(length=100)
    
    # Strip _id from all for JSON serialization
    for lst in [events, competitions, courses, tickets, orders, certs, rewards_docs, badges]:
        for item in lst:
            if "_id" in item:
                item["_id"] = str(item["_id"])
            if "activity_id" in item and type(item["activity_id"]) != str:
                item["activity_id"] = str(item["activity_id"])
            
    if "_id" in current_user:
        del current_user["_id"]

    return {
        "profile": current_user,
        "is_profile_completed": is_completed,
        "requires_profile_completion": not is_completed,
        "prompt_profile_form": not is_completed,
        "first_time_login": not is_completed,
        "missing_fields": missing,
        "next_step": "COMPLETE_PROFILE" if not is_completed else "DASHBOARD",
        "redirect_url": "/complete-profile" if not is_completed else "/dashboard",
        "profile_form": form_schema,
        "my_events": events,
        "my_tickets": tickets,
        "my_competitions": competitions,
        "my_ranks": my_ranks,
        "my_courses": courses,
        "my_certificates": certs,
        "my_badges": badges,
        "my_rewards_and_coins": {
            "total_coins": total_coins,
            "available_coins": available_coins or total_coins,
            "total_earned_coins": total_earned_coins,
            "total_redeemed_coins": total_redeemed_coins,
            "reward_history": rewards_docs,
            "coin_history": ledger_txns
        },
        "my_merchandise_orders": orders
    }
