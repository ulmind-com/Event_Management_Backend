from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query, UploadFile, File, Form
from typing import Optional, List, Dict, Any
import re
import json
from bson import ObjectId
from app.core.admin_auth import get_current_admin
from app.core.database import (
    competition_collection, 
    competition_registration_collection,
    competition_submission_collection,
    judge_score_collection,
    audience_vote_collection,
    leaderboard_result_collection,
    user_collection,
)
from app.schemas.competition import CompetitionCreateReq, CompetitionUpdateReq
from app.services.cloudinary_srv import upload_image
from app.services.competition_lifecycle import (
    evaluate_competition_registration_status,
    safe_object_id,
)
from app.services.competition_submission_config import (
    normalize_submission_settings,
    enrich_competition_submission_config,
)
from datetime import datetime, timezone

router = APIRouter()

@router.post("/")
async def create_competition(req: CompetitionCreateReq, admin: dict = Depends(get_current_admin)):
    """Creates a new advanced competition with rules, criteria, dynamic fields, and card/banner images."""
    comp_dict = req.model_dump()

    # Normalize submission fields according to enabled options (file, video link, audio mp3)
    comp_dict = normalize_submission_settings(comp_dict, is_update=False)

    # Sync card and banner image aliases
    if comp_dict.get("card_image_url") and not comp_dict.get("image_url"):
        comp_dict["image_url"] = comp_dict["card_image_url"]
    elif comp_dict.get("image_url") and not comp_dict.get("card_image_url"):
        comp_dict["card_image_url"] = comp_dict["image_url"]

    if comp_dict.get("banner_image_url") and not comp_dict.get("banner_url"):
        comp_dict["banner_url"] = comp_dict["banner_image_url"]
    elif comp_dict.get("banner_url") and not comp_dict.get("banner_image_url"):
        comp_dict["banner_image_url"] = comp_dict["banner_url"]
    
    # Validation for ranking modes
    if comp_dict.get("audience_voting_enabled") and comp_dict.get("judge_scoring_enabled"):
        w_a = comp_dict.get("audience_vote_weight", 0.0)
        w_j = comp_dict.get("judge_score_weight", 0.0)
        if abs((w_a + w_j) - 1.0) > 0.001 and abs((w_a + w_j) - 100.0) > 0.001:
            raise HTTPException(status_code=400, detail="Combined weights must total 1.0 or 100%")
    elif not comp_dict.get("audience_voting_enabled") and not comp_dict.get("judge_scoring_enabled"):
        raise HTTPException(status_code=400, detail="At least one ranking mode must be enabled.")
        
    comp_dict["voting_status"] = "OPEN" if comp_dict.get("audience_voting_enabled") else "CLOSED"
    comp_dict["judging_status"] = "OPEN" if comp_dict.get("judge_scoring_enabled") else "CLOSED"
    comp_dict["result_status"] = "PENDING"
    comp_dict["registration_status"] = comp_dict.get("registration_status") or "OPEN"
    comp_dict["is_registration_open"] = True if comp_dict.get("is_registration_open") is not False else False
    comp_dict["registered_count"] = int(comp_dict.get("registered_count") or 0)
    
    comp_dict["created_at"] = datetime.now(timezone.utc)
    comp_dict["updated_at"] = datetime.now(timezone.utc)
    
    res = await competition_collection.insert_one(comp_dict)
    comp_dict["_id"] = str(res.inserted_id)
    return comp_dict


@router.post("/with-media")
async def create_competition_with_media(
    competition_data: str = Form(..., description="Competition details JSON string matching CompetitionCreateReq"),
    card_image: Optional[UploadFile] = File(None, description="Competition Card / Thumbnail image file"),
    banner_image: Optional[UploadFile] = File(None, description="Competition Banner image file"),
    admin: dict = Depends(get_current_admin),
):
    """
    Creates a new competition and uploads card image and/or banner image to Cloudinary in a single multipart request.
    """
    try:
        parsed_data = json.loads(competition_data)
        req = CompetitionCreateReq.model_validate(parsed_data)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid competition data JSON: {exc}")

    comp_dict = req.model_dump()

    # Upload Card Image if provided
    if card_image and card_image.filename:
        card_bytes = await card_image.read()
        if card_bytes and len(card_bytes) > 0:
            uploaded_card_url = upload_image(
                card_bytes,
                folder="eventora/competitions/cards",
                filename=card_image.filename,
            )
            if uploaded_card_url:
                comp_dict["card_image_url"] = uploaded_card_url
                comp_dict["image_url"] = uploaded_card_url

    # Upload Banner Image if provided
    if banner_image and banner_image.filename:
        banner_bytes = await banner_image.read()
        if banner_bytes and len(banner_bytes) > 0:
            uploaded_banner_url = upload_image(
                banner_bytes,
                folder="eventora/competitions/banners",
                filename=banner_image.filename,
            )
            if uploaded_banner_url:
                comp_dict["banner_url"] = uploaded_banner_url
                comp_dict["banner_image_url"] = uploaded_banner_url

    # Sync card/banner URLs
    if comp_dict.get("card_image_url") and not comp_dict.get("image_url"):
        comp_dict["image_url"] = comp_dict["card_image_url"]
    elif comp_dict.get("image_url") and not comp_dict.get("card_image_url"):
        comp_dict["card_image_url"] = comp_dict["image_url"]

    if comp_dict.get("banner_image_url") and not comp_dict.get("banner_url"):
        comp_dict["banner_url"] = comp_dict["banner_image_url"]
    elif comp_dict.get("banner_url") and not comp_dict.get("banner_image_url"):
        comp_dict["banner_image_url"] = comp_dict["banner_url"]

    # Validation for ranking modes
    if comp_dict.get("audience_voting_enabled") and comp_dict.get("judge_scoring_enabled"):
        w_a = comp_dict.get("audience_vote_weight", 0.0)
        w_j = comp_dict.get("judge_score_weight", 0.0)
        if abs((w_a + w_j) - 1.0) > 0.001 and abs((w_a + w_j) - 100.0) > 0.001:
            raise HTTPException(status_code=400, detail="Combined weights must total 1.0 or 100%")
    elif not comp_dict.get("audience_voting_enabled") and not comp_dict.get("judge_scoring_enabled"):
        raise HTTPException(status_code=400, detail="At least one ranking mode must be enabled.")

    comp_dict["voting_status"] = "OPEN" if comp_dict.get("audience_voting_enabled") else "CLOSED"
    comp_dict["judging_status"] = "OPEN" if comp_dict.get("judge_scoring_enabled") else "CLOSED"
    comp_dict["result_status"] = "PENDING"
    comp_dict["registration_status"] = comp_dict.get("registration_status") or "OPEN"
    comp_dict["is_registration_open"] = True if comp_dict.get("is_registration_open") is not False else False
    comp_dict["registered_count"] = int(comp_dict.get("registered_count") or 0)

    comp_dict["created_at"] = datetime.now(timezone.utc)
    comp_dict["updated_at"] = datetime.now(timezone.utc)

    # Normalize submission fields according to enabled options (file, video link, audio mp3)
    comp_dict = normalize_submission_settings(comp_dict, is_update=False)

    res = await competition_collection.insert_one(comp_dict)
    comp_dict["_id"] = str(res.inserted_id)
    return comp_dict


@router.post("/upload-card")
async def upload_competition_card(
    file: UploadFile = File(...),
    admin: dict = Depends(get_current_admin),
):
    """Uploads a competition card / thumbnail image to Cloudinary and returns secure URL."""
    contents = await file.read()
    if not contents or len(contents) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    url = upload_image(contents, folder="eventora/competitions/cards", filename=file.filename)
    if not url:
        raise HTTPException(status_code=500, detail="Failed to upload card image to storage.")
    return {
        "url": url,
        "card_image_url": url,
        "image_url": url,
        "filename": file.filename,
    }


@router.post("/upload-banner")
async def upload_competition_banner(
    file: UploadFile = File(...),
    admin: dict = Depends(get_current_admin),
):
    """Uploads a competition banner image to Cloudinary and returns secure URL."""
    contents = await file.read()
    if not contents or len(contents) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    url = upload_image(contents, folder="eventora/competitions/banners", filename=file.filename)
    if not url:
        raise HTTPException(status_code=500, detail="Failed to upload banner image to storage.")
    return {
        "url": url,
        "banner_url": url,
        "banner_image_url": url,
        "filename": file.filename,
    }


@router.post("/upload-image")
async def upload_competition_image(
    file: UploadFile = File(...),
    image_type: Optional[str] = Query("card", description="Image target type: 'card' or 'banner'"),
    admin: dict = Depends(get_current_admin),
):
    """General image uploader for competitions (supports card or banner)."""
    contents = await file.read()
    if not contents or len(contents) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    folder = "eventora/competitions/banners" if image_type == "banner" else "eventora/competitions/cards"
    url = upload_image(contents, folder=folder, filename=file.filename)
    if not url:
        raise HTTPException(status_code=500, detail="Failed to upload image to storage.")
    return {
        "url": url,
        "image_type": image_type,
        "card_image_url": url if image_type != "banner" else None,
        "banner_url": url if image_type == "banner" else None,
        "filename": file.filename,
    }


@router.get("/")
async def list_competitions(admin: dict = Depends(get_current_admin)):
    """List all competitions for Admin."""
    comps = await competition_collection.find().sort("created_at", -1).to_list(length=100)
    for c in comps:
        c["_id"] = str(c["_id"])
        enrich_competition_submission_config(c)
    return comps

@router.get("/{comp_id}")
async def get_competition(comp_id: str, admin: dict = Depends(get_current_admin)):
    """Fetch single competition details for Admin."""
    try:
        c_id = ObjectId(comp_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid competition ID")
    comp = await competition_collection.find_one({"_id": c_id})
    if not comp:
        raise HTTPException(status_code=404, detail="Competition not found")
    comp["_id"] = str(comp["_id"])
    comp = enrich_competition_submission_config(comp)
    return comp

@router.patch("/{comp_id}")
@router.put("/{comp_id}")
async def update_competition(comp_id: str, req: CompetitionUpdateReq, admin: dict = Depends(get_current_admin)):
    """Updates competition fields."""
    try:
        c_id = ObjectId(comp_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid competition ID")

    update_data = req.model_dump(exclude_unset=True)
    if not update_data:
        return {"message": "No fields to update"}

    # Sync card and banner image aliases
    if "card_image_url" in update_data and "image_url" not in update_data:
        update_data["image_url"] = update_data["card_image_url"]
    elif "image_url" in update_data and "card_image_url" not in update_data:
        update_data["card_image_url"] = update_data["image_url"]

    if "banner_image_url" in update_data and "banner_url" not in update_data:
        update_data["banner_url"] = update_data["banner_image_url"]
    elif "banner_url" in update_data and "banner_image_url" not in update_data:
        update_data["banner_image_url"] = update_data["banner_url"]

    # Handle submission field toggles / formats update
    submission_keys = [
        "allow_file_upload",
        "allow_video_link",
        "allow_audio_upload",
        "submission_formats",
        "submission_format_config",
        "dynamic_submission_fields",
    ]
    if any(k in update_data for k in submission_keys):
        existing_comp = await competition_collection.find_one({"_id": c_id})
        if not existing_comp:
            raise HTTPException(status_code=404, detail="Competition not found")

        merged_for_norm = dict(existing_comp)
        merged_for_norm.update(update_data)
        normalized = normalize_submission_settings(merged_for_norm, is_update=False)

        update_data["allow_file_upload"] = normalized["allow_file_upload"]
        update_data["allow_video_link"] = normalized["allow_video_link"]
        update_data["allow_audio_upload"] = normalized["allow_audio_upload"]
        update_data["submission_formats"] = normalized["submission_formats"]
        update_data["submission_format_config"] = normalized["submission_format_config"]
        update_data["dynamic_submission_fields"] = normalized["dynamic_submission_fields"]

    # Validation: min_age cannot exceed max_age if both are provided
    if update_data.get("min_age") is not None and update_data.get("max_age") is not None:
        if update_data["min_age"] > update_data["max_age"]:
            raise HTTPException(status_code=400, detail="Minimum age cannot be greater than maximum age.")
        
    if "audience_voting_enabled" in update_data:
        update_data["voting_status"] = "OPEN" if update_data["audience_voting_enabled"] else "CLOSED"
    if "judge_scoring_enabled" in update_data:
        update_data["judging_status"] = "OPEN" if update_data["judge_scoring_enabled"] else "CLOSED"
    if "is_registration_open" in update_data:
        if update_data["is_registration_open"] is False:
            update_data["registration_status"] = "CLOSED"
        elif "registration_status" not in update_data:
            update_data["registration_status"] = "OPEN"
    elif "registration_status" in update_data and update_data["registration_status"]:
        update_data["is_registration_open"] = (update_data["registration_status"].strip().upper() == "OPEN")
        
    update_data["updated_at"] = datetime.now(timezone.utc)
    
    res = await competition_collection.update_one(
        {"_id": c_id},
        {"$set": update_data}
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Competition not found")
        
    updated_comp = await competition_collection.find_one({"_id": c_id})
    if updated_comp:
        updated_comp["_id"] = str(updated_comp["_id"])
        updated_comp = enrich_competition_submission_config(updated_comp)
        
    return {
        "message": "Competition updated successfully",
        "competition": updated_comp,
        **(updated_comp or {})
    }


@router.post("/{comp_id}/upload-card")
async def upload_card_to_competition(
    comp_id: str,
    file: UploadFile = File(...),
    admin: dict = Depends(get_current_admin),
):
    """Uploads card/thumbnail image and attaches directly to the specified competition."""
    try:
        c_id = ObjectId(comp_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid competition ID")
    contents = await file.read()
    if not contents or len(contents) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    url = upload_image(contents, folder="eventora/competitions/cards", filename=file.filename)
    if not url:
        raise HTTPException(status_code=500, detail="Failed to upload card image to storage.")
    res = await competition_collection.update_one(
        {"_id": c_id},
        {"$set": {"card_image_url": url, "image_url": url, "updated_at": datetime.now(timezone.utc)}}
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Competition not found")
    return {"message": "Card image uploaded and updated successfully", "card_image_url": url, "image_url": url}


@router.post("/{comp_id}/upload-banner")
async def upload_banner_to_competition(
    comp_id: str,
    file: UploadFile = File(...),
    admin: dict = Depends(get_current_admin),
):
    """Uploads banner image and attaches directly to the specified competition."""
    try:
        c_id = ObjectId(comp_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid competition ID")
    contents = await file.read()
    if not contents or len(contents) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    url = upload_image(contents, folder="eventora/competitions/banners", filename=file.filename)
    if not url:
        raise HTTPException(status_code=500, detail="Failed to upload banner image to storage.")
    res = await competition_collection.update_one(
        {"_id": c_id},
        {"$set": {"banner_url": url, "banner_image_url": url, "updated_at": datetime.now(timezone.utc)}}
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Competition not found")
    return {"message": "Banner image uploaded and updated successfully", "banner_url": url, "banner_image_url": url}

@router.post("/{comp_id}/publish")
async def publish_competition(comp_id: str, admin: dict = Depends(get_current_admin)):
    res = await competition_collection.update_one(
        {"_id": ObjectId(comp_id)}, {"$set": {"is_published": True, "updated_at": datetime.now(timezone.utc)}}
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Competition not found")
    return {"message": "Competition published"}

@router.post("/{comp_id}/unpublish")
async def unpublish_competition(comp_id: str, admin: dict = Depends(get_current_admin)):
    res = await competition_collection.update_one(
        {"_id": ObjectId(comp_id)}, {"$set": {"is_published": False, "updated_at": datetime.now(timezone.utc)}}
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Competition not found")
    return {"message": "Competition unpublished"}

@router.delete("/{comp_id}")
async def delete_competition(comp_id: str, admin: dict = Depends(get_current_admin)):
    """
    16. COMPLETE COMPETITION DELETION
    Secure deletion workflow:
    - Cascades to registrations, submissions, scores, votes, leaderboards.
    - (Future: Call Cloudinary to delete assets mapped in submissions).
    """
    try:
        c_id = ObjectId(comp_id)
    except:
        raise HTTPException(status_code=400, detail="Invalid ID")
        
    comp = await competition_collection.find_one({"_id": c_id})
    if not comp:
        raise HTTPException(status_code=404, detail="Competition not found")
        
    # Cascade Deletes
    await competition_submission_collection.delete_many({"competition_id": comp_id})
    await competition_registration_collection.delete_many({"competition_id": comp_id})
    await judge_score_collection.delete_many({"competition_id": comp_id})
    await audience_vote_collection.delete_many({"competition_id": comp_id})
    await leaderboard_result_collection.delete_many({"competition_id": comp_id})
    
    await competition_collection.delete_one({"_id": c_id})
    
    return {"message": "Competition and all associated records deleted securely."}

@router.post("/{comp_id}/recalculate-rankings")
async def recalculate_rankings(comp_id: str, admin: dict = Depends(get_current_admin)):
    """Aggregates scores and updates the leaderboard."""
    comp = await competition_collection.find_one({"_id": ObjectId(comp_id)})
    if not comp:
        raise HTTPException(status_code=404, detail="Competition not found")
        
    # Group by submission_id
    pipeline = [
        {"$match": {"competition_id": comp_id}},
        {"$group": {
            "_id": "$submission_id",
            "total_judge_score": {"$sum": "$total_score"},
            "judge_count": {"$sum": 1}
        }}
    ]
    
    score_agg = await judge_score_collection.aggregate(pipeline).to_list(length=1000)
    
    # Clean previous leaderboard
    await leaderboard_result_collection.delete_many({"competition_id": comp_id})
    
    docs = []
    for s in score_agg:
        # Here we could fetch audience votes and apply audience_vote_weight
        # For simplicity in this abstraction, we just use judge scores
        
        docs.append({
            "competition_id": comp_id,
            "submission_id": s["_id"],
            "total_score": s["total_judge_score"],
            "rank": 0 # Will be assigned
        })
        
    if not docs:
        return {"message": "No scores to calculate."}
        
    # Sort and rank
    docs.sort(key=lambda x: x["total_score"], reverse=True)
    current_rank = 1
    for i, doc in enumerate(docs):
        if i > 0 and docs[i]["total_score"] == docs[i-1]["total_score"]:
            doc["rank"] = docs[i-1]["rank"]
        else:
            doc["rank"] = current_rank
        current_rank += 1
            
    await leaderboard_result_collection.insert_many(docs)
    return {"message": "Rankings recalculated successfully", "total_ranked": len(docs)}

@router.post("/{comp_id}/judging/lock")
async def lock_scores(comp_id: str, admin: dict = Depends(get_current_admin)):
    """Locks all scores for a competition so judges cannot casually edit them."""
    res = await judge_score_collection.update_many(
        {"competition_id": comp_id, "is_locked": {"$ne": True}},
        {"$set": {
            "is_locked": True, 
            "locked_by": admin.get("email"),
            "locked_at": datetime.now(timezone.utc)
        }}
    )
    return {"message": "Scores locked successfully.", "locked_count": res.modified_count}

@router.post("/{comp_id}/results/approve")
async def approve_results(comp_id: str, admin: dict = Depends(get_current_admin)):
    """
    Recalculates the leaderboard from locked scores, generates leaderboard entries,
    and approves the official result snapshot.
    """
    comp = await competition_collection.find_one({"_id": ObjectId(comp_id)})
    if not comp:
        raise HTTPException(status_code=404, detail="Competition not found")
        
    pipeline = [
        {"$match": {"competition_id": comp_id, "is_locked": True}},
        {"$group": {
            "_id": "$submission_id",
            "total_judge_score": {"$sum": "$total_score"},
            "judge_count": {"$sum": 1}
        }}
    ]
    
    score_agg = await judge_score_collection.aggregate(pipeline).to_list(length=1000)
    await leaderboard_result_collection.delete_many({"competition_id": comp_id})
    
    docs = []
    for s in score_agg:
        sub = await competition_submission_collection.find_one({"_id": ObjectId(s["_id"]) if ObjectId.is_valid(s["_id"]) else s["_id"]})
        user_email = sub.get("user_email") if sub else ""
        user_info = await user_collection.find_one({"email": user_email}) if user_email else None
        p_name = (user_info.get("full_name") or user_info.get("name") or sub.get("name") or user_email.split("@")[0].title()) if user_email else "Contestant"
        
        docs.append({
            "competition_id": comp_id,
            "submission_id": s["_id"],
            "participant_id": user_email,
            "email": user_email,
            "name": p_name,
            "total_score": s["total_judge_score"],
            "rank": 0,
            "status": "APPROVED"
        })
        
    if docs:
        docs.sort(key=lambda x: x["total_score"], reverse=True)
        current_rank = 1
        for i, doc in enumerate(docs):
            if i > 0 and docs[i]["total_score"] == docs[i-1]["total_score"]:
                doc["rank"] = docs[i-1]["rank"]
            else:
                doc["rank"] = current_rank
            current_rank += 1
        await leaderboard_result_collection.insert_many(docs)

    # Approve snapshot if exists
    from app.core.database import db
    result_snapshots = db["result_snapshots"]
    snapshot = await result_snapshots.find_one({"competition_id": comp_id, "is_published": False})
    if snapshot:
        await result_snapshots.update_one(
            {"_id": snapshot["_id"]},
            {"$set": {"is_approved": True, "approved_by": admin.get("email"), "approved_at": datetime.now(timezone.utc)}}
        )
    
    await competition_collection.update_one(
        {"_id": ObjectId(comp_id)},
        {"$set": {"result_status": "APPROVED", "results_approved_at": datetime.now(timezone.utc)}}
    )
    return {"message": "Results approved and Leaderboard generated successfully.", "total_ranked": len(docs)}

@router.post("/{comp_id}/results/publish")
async def publish_results(comp_id: str, admin: dict = Depends(get_current_admin)):
    """Publishes the approved leaderboard to the public and dispatches notifications."""
    from app.core.database import db
    result_snapshots = db["result_snapshots"]
    
    snapshot = await result_snapshots.find_one({"competition_id": comp_id, "is_approved": True})
    if snapshot:
        await result_snapshots.update_one(
            {"_id": snapshot["_id"]},
            {"$set": {"is_published": True, "published_by": admin.get("email"), "published_at": datetime.now(timezone.utc)}}
        )

    res = await competition_collection.update_one(
        {"_id": ObjectId(comp_id)},
        {"$set": {
            "result_status": "PUBLISHED",
            "results_published": True,
            "results_published_at": datetime.now(timezone.utc)
        }}
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Competition not found")

    # Authoritative trigger: COMPETITION_RESULT & LEADERBOARD_UPDATE
    try:
        from app.services.notification_service import NotificationService
        from app.core.notification_types import NotificationType, NotificationPriority, SoundCategory

        comp = await competition_collection.find_one({"_id": ObjectId(comp_id)})
        comp_title = comp.get("title", "Competition") if comp else "Competition"

        regs = await competition_registration_collection.find({"competition_id": comp_id}).to_list(2000)
        subs = await competition_submission_collection.find({"competition_id": comp_id}).to_list(2000)
        recipients = list(set([r["user_email"] for r in regs if r.get("user_email")] + [s["user_email"] for s in subs if s.get("user_email")]))

        if recipients:
            await NotificationService.create_bulk_notifications(
                recipient_emails=recipients,
                notification_type=NotificationType.COMPETITION_RESULT,
                title=f"Results Published: {comp_title}",
                message=f"The official results and leaderboard rankings for '{comp_title}' are now published!",
                resource_type="competition",
                resource_id=comp_id,
                action_url=f"/competitions/{comp_id}/results",
                priority=NotificationPriority.HIGH,
                sound_category=SoundCategory.SUCCESS,
                dedup_prefix=f"comp_result_{comp_id}",
            )
    except Exception:
        pass

    return {"message": "Results published successfully."}

# ══════════════════════════════════════════════
# LIVE SESSION PIN MANAGEMENT (Admin)
# ══════════════════════════════════════════════

@router.post("/{comp_id}/session/open")
async def open_judging_session(comp_id: str, pin: str = None, admin: dict = Depends(get_current_admin)):
    """Opens a live judging session. Optionally sets a PIN judges must enter to score."""
    from app.core.database import judging_session_collection
    
    existing = await judging_session_collection.find_one({"competition_id": comp_id, "status": "OPEN"})
    if existing:
        raise HTTPException(status_code=409, detail="A session is already open for this competition.")
    
    import hashlib
    pin_hash = None
    if pin:
        pin_hash = pin  # In production, hash this
    
    doc = {
        "competition_id": comp_id,
        "status": "OPEN",
        "pin": pin_hash,
        "verified_judges": [],
        "opened_by": admin.get("email"),
        "opened_at": datetime.now(timezone.utc)
    }
    await judging_session_collection.insert_one(doc)
    return {"message": "Live judging session opened.", "pin_required": bool(pin)}

@router.post("/{comp_id}/session/close")
async def close_judging_session(comp_id: str, admin: dict = Depends(get_current_admin)):
    """Closes the live judging session."""
    from app.core.database import judging_session_collection
    
    res = await judging_session_collection.update_one(
        {"competition_id": comp_id, "status": "OPEN"},
        {"$set": {"status": "CLOSED", "closed_at": datetime.now(timezone.utc)}}
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="No open session found.")
    return {"message": "Live judging session closed."}

# ══════════════════════════════════════════════
# ADMIN: JUDGING PROGRESS DASHBOARD
# ══════════════════════════════════════════════

@router.get("/{comp_id}/judging/progress")
async def judging_progress(comp_id: str, admin: dict = Depends(get_current_admin)):
    """Returns detailed judging progress for admin dashboard."""
    from app.core.database import (
        judge_assignment_collection, judge_score_collection, 
        competition_submission_collection, coi_skip_collection
    )
    
    comp_matches = [str(comp_id)]
    if ObjectId.is_valid(str(comp_id)):
        comp_matches.append(ObjectId(str(comp_id)))

    total_judges = await judge_assignment_collection.count_documents({"competition_id": {"$in": comp_matches}})
    total_subs = await competition_submission_collection.count_documents({
        "competition_id": {"$in": comp_matches},
        "status": {"$in": ["SUBMITTED", "APPROVED"]}
    })
    total_scores = await judge_score_collection.count_documents({"competition_id": {"$in": comp_matches}})
    locked_scores = await judge_score_collection.count_documents({"competition_id": {"$in": comp_matches}, "is_locked": True})
    total_skips = await coi_skip_collection.count_documents({"competition_id": {"$in": comp_matches}})
    
    expected_total = total_judges * total_subs
    completed = total_scores + total_skips
    
    # Per-judge breakdown
    judges = await judge_assignment_collection.find({"competition_id": {"$in": comp_matches}}).to_list(100)
    judge_details = []
    for j in judges:
        email = j["judge_email"]
        scored = await judge_score_collection.count_documents({"competition_id": {"$in": comp_matches}, "judge_email": email})
        skipped = await coi_skip_collection.count_documents({"competition_id": {"$in": comp_matches}, "judge_email": email})
        judge_details.append({
            "judge_email": email,
            "scored": scored,
            "skipped": skipped,
            "pending": max(0, total_subs - scored - skipped),
            "completion_pct": round((scored + skipped) / total_subs * 100, 1) if total_subs > 0 else 0
        })
    
    return {
        "total_judges": total_judges,
        "total_contestants": total_subs,
        "total_scores_submitted": total_scores,
        "total_scores_locked": locked_scores,
        "total_coi_skips": total_skips,
        "expected_total_scores": expected_total,
        "overall_completion_pct": round(completed / expected_total * 100, 1) if expected_total > 0 else 0,
        "judges": judge_details,
        "judge_details": judge_details
    }

# ══════════════════════════════════════════════
# ADMIN: VIEW CONTESTANT FEEDBACKS
# ══════════════════════════════════════════════

@router.get("/{comp_id}/feedbacks")
async def list_feedbacks(comp_id: str, admin: dict = Depends(get_current_admin)):
    """Lists all judge feedbacks for a competition."""
    from app.core.database import judge_feedback_collection
    feedbacks = await judge_feedback_collection.find({"competition_id": comp_id}).to_list(length=1000)
    for f in feedbacks:
        f["_id"] = str(f["_id"])
    return feedbacks

# ══════════════════════════════════════════════
# EXPORT PARTICIPANTS (PDF)
# ══════════════════════════════════════════════

@router.get("/{comp_id}/participants/export-pdf")
async def export_participants_pdf(comp_id: str, admin: dict = Depends(get_current_admin)):
    """Downloads a PDF report of all registered participants, only if registration is closed."""
    from app.core.database import competition_collection, competition_submission_collection, user_collection
    from app.services.pdf_generator import PDFGenerator
    from fastapi.responses import Response
    from bson import ObjectId
    
    comp = await competition_collection.find_one({"_id": ObjectId(comp_id)})
    if not comp:
        raise HTTPException(status_code=404, detail="Competition not found.")
        
    # Check if registration is closed
    reg_deadline = comp.get("registration_deadline")
    if not reg_deadline:
        raise HTTPException(status_code=400, detail="Registration deadline is not set for this competition.")
        
    # Ensure timezone awareness for comparison
    if reg_deadline.tzinfo is None:
        reg_deadline = reg_deadline.replace(tzinfo=timezone.utc)
        
    if datetime.now(timezone.utc) <= reg_deadline:
        raise HTTPException(
            status_code=400, 
            detail="Registration is still open. You can download the PDF once it closes."
        )
        
    # Fetch all successful submissions
    submissions = await competition_submission_collection.find({
        "competition_id": comp_id,
        "status": {"$in": ["APPROVED", "SUBMITTED"]}
    }).to_list(length=5000)
    
    if not submissions:
        raise HTTPException(status_code=404, detail="No participants found for this competition.")
        
    # Fetch user details
    participants_data = []
    for sub in submissions:
        user = await user_collection.find_one({"email": sub.get("user_email")}) if sub.get("user_email") else None
        if user:
            participants_data.append({
                "name": user.get("name") or user.get("full_name") or "Participant",
                "email": user.get("email", ""),
                "phone": user.get("phone_number") or user.get("phone") or "N/A",
                "status": sub.get("status", "")
            })
        else:
            participants_data.append({
                "name": sub.get("participant_name") or sub.get("name") or sub.get("user_email") or "Participant",
                "email": sub.get("user_email", ""),
                "phone": sub.get("phone_number") or sub.get("phone") or "N/A",
                "status": sub.get("status", "")
            })
            
    # Generate PDF
    pdf_bytes = PDFGenerator.generate_participants_pdf(comp.get("title", "Competition"), participants_data)
    
    # Return as downloadable file
    headers = {
        'Content-Disposition': f'attachment; filename="participants_{comp_id}.pdf"'
    }
    return Response(content=pdf_bytes, media_type="application/pdf", headers=headers)

# ══════════════════════════════════════════════
# LIFECYCLE CONTROLS
# ══════════════════════════════════════════════

@router.post("/{comp_id}/registration/close")
async def close_competition_registration(
    comp_id: str,
    reason: Optional[str] = Query(None, description="Optional administrative closure reason"),
    admin: dict = Depends(get_current_admin),
):
    """
    Administrative Control: Immediately closes registration for the competition.
    No participant will be able to register through any channel once closed.
    """
    c_id = safe_object_id(comp_id)
    if not c_id:
        raise HTTPException(status_code=400, detail="Invalid competition ID")
    comp = await competition_collection.find_one({"_id": c_id})
    if not comp:
        raise HTTPException(status_code=404, detail="Competition not found")

    now = datetime.now(timezone.utc)
    closure_reason = reason or "Registration closed by administrator."
    await competition_collection.update_one(
        {"_id": c_id},
        {"$set": {
            "registration_status": "CLOSED",
            "is_registration_open": False,
            "registration_closed_at": now,
            "registration_closed_by": admin.get("email"),
            "registration_closure_reason": closure_reason,
            "updated_at": now,
        }}
    )
    return {
        "message": "Competition registration has been closed successfully.",
        "competition_id": str(c_id),
        "is_registration_open": False,
        "registration_status": "CLOSED",
        "closure_reason": closure_reason,
        "closed_at": now.isoformat(),
    }


@router.post("/{comp_id}/registration/open")
async def open_competition_registration(
    comp_id: str,
    new_deadline: Optional[datetime] = Query(None, description="Optional new registration deadline"),
    admin: dict = Depends(get_current_admin),
):
    """
    Administrative Control: Re-opens registration for the competition.
    Optionally extends or resets the registration deadline.
    """
    c_id = safe_object_id(comp_id)
    if not c_id:
        raise HTTPException(status_code=400, detail="Invalid competition ID")
    comp = await competition_collection.find_one({"_id": c_id})
    if not comp:
        raise HTTPException(status_code=404, detail="Competition not found")

    now = datetime.now(timezone.utc)
    update_set: Dict[str, Any] = {
        "registration_status": "OPEN",
        "is_registration_open": True,
        "registration_opened_at": now,
        "registration_opened_by": admin.get("email"),
        "updated_at": now,
    }
    if new_deadline:
        update_set["registration_deadline"] = new_deadline

    await competition_collection.update_one({"_id": c_id}, {"$set": update_set})

    updated_comp = await competition_collection.find_one({"_id": c_id})
    status_info = evaluate_competition_registration_status(updated_comp)

    return {
        "message": "Competition registration has been opened successfully.",
        "competition_id": str(c_id),
        **status_info,
    }


@router.get("/{comp_id}/registration/status")
async def get_competition_registration_admin_status(
    comp_id: str,
    admin: dict = Depends(get_current_admin),
):
    """
    Administrative Inspection: Returns comprehensive real-time status of registration,
    including open/close state, deadline, participant counts, and capacity limits.
    """
    c_id = safe_object_id(comp_id)
    if not c_id:
        raise HTTPException(status_code=400, detail="Invalid competition ID")
    comp = await competition_collection.find_one({"_id": c_id})
    if not comp:
        raise HTTPException(status_code=404, detail="Competition not found")

    status_info = evaluate_competition_registration_status(comp)
    total_confirmed = await competition_registration_collection.count_documents({
        "competition_id": str(c_id),
        "status": "CONFIRMED",
    })
    total_pending = await competition_registration_collection.count_documents({
        "competition_id": str(c_id),
        "status": "PENDING",
    })
    return {
        "competition_id": str(c_id),
        "title": comp.get("title"),
        **status_info,
        "metrics": {
            "confirmed_registrations": total_confirmed,
            "pending_registrations": total_pending,
            "total_registered": total_confirmed + total_pending,
        },
    }


@router.post("/{comp_id}/voting/close")
async def close_audience_voting(comp_id: str, admin: dict = Depends(get_current_admin)):
    comp = await competition_collection.find_one({"_id": ObjectId(comp_id)})
    if not comp: raise HTTPException(status_code=404, detail="Competition not found")
    if not comp.get("audience_voting_enabled"):
        raise HTTPException(status_code=400, detail="Audience voting is not enabled for this competition.")
        
    await competition_collection.update_one({"_id": ObjectId(comp_id)}, {"$set": {"voting_status": "CLOSED"}})
    return {"message": "Audience voting closed successfully."}

@router.post("/{comp_id}/judging/close")
async def close_judge_scoring(comp_id: str, admin: dict = Depends(get_current_admin)):
    comp = await competition_collection.find_one({"_id": ObjectId(comp_id)})
    if not comp: raise HTTPException(status_code=404, detail="Competition not found")
    if not comp.get("judge_scoring_enabled"):
        raise HTTPException(status_code=400, detail="Judge scoring is not enabled for this competition.")
        
    await competition_collection.update_one({"_id": ObjectId(comp_id)}, {"$set": {"judging_status": "CLOSED"}})
    return {"message": "Judge scoring closed successfully."}

@router.post("/{comp_id}/finalization/start")
async def start_finalization(comp_id: str, admin: dict = Depends(get_current_admin)):
    """Validates if phases are closed, generates the immutable final ranking snapshot."""
    from app.services.ranking_service import RankingService
    comp = await competition_collection.find_one({"_id": ObjectId(comp_id)})
    if not comp: raise HTTPException(status_code=404, detail="Competition not found")
    
    if comp.get("audience_voting_enabled") and comp.get("voting_status", "OPEN") != "CLOSED":
        raise HTTPException(status_code=400, detail="Audience voting must be closed first.")
    if comp.get("judge_scoring_enabled") and comp.get("judging_status", "OPEN") != "CLOSED":
        raise HTTPException(status_code=400, detail="Judge scoring must be closed first.")
        
    # Get all submissions
    submissions = await competition_submission_collection.find({"competition_id": comp_id, "status": {"$in": ["APPROVED", "SUBMITTED"]}}).to_list(length=5000)
    
    # Get max possible judge score
    max_judge_score = sum(crit.get("max_score", 0) for crit in comp.get("judging_criteria", []))
    
    participants_data = []
    max_audience_votes = 0
    for sub in submissions:
        user_email = sub["user_email"]
        sub_id = str(sub.get("_id", ""))
        vote_match = [{"participant_id": user_email}]
        if sub_id:
            vote_match.append({"participant_id": sub_id})
            vote_match.append({"submission_id": sub_id})
        # Sum voting power
        pipeline = [
            {"$match": {"competition_id": {"$in": [comp_id, str(comp_id)]}, "$or": vote_match}},
            {"$group": {"_id": None, "total": {"$sum": "$voting_power"}}}
        ]
        agg_res = await audience_vote_collection.aggregate(pipeline).to_list(1)
        votes = agg_res[0]["total"] if agg_res else 0
        
        if votes > max_audience_votes: max_audience_votes = votes
        
        # Calculate avg judge score for this user
        scores = await judge_score_collection.find({"competition_id": comp_id, "participant_id": user_email}).to_list(length=100)
        total_judge = 0
        if scores:
            for s in scores:
                total_judge += sum(s.get("scores", {}).values())
            total_judge = total_judge / len(scores)
            
        participants_data.append({
            "participant_id": user_email,
            "audience_votes": votes,
            "judge_score": total_judge
        })
        
    final_leaderboard = RankingService.calculate_leaderboard(participants_data, max_audience_votes, max_judge_score, comp)
    
    # Store Snapshot
    from app.core.database import db
    result_snapshots_collection = db["result_snapshots"]
    
    snapshot_doc = {
        "competition_id": comp_id,
        "calculated_at": datetime.now(timezone.utc),
        "ranking_mode": final_leaderboard[0]["ranking_mode"] if final_leaderboard else "UNKNOWN",
        "leaderboard": final_leaderboard,
        "is_approved": False,
        "is_published": False,
        "approved_by": None,
        "published_by": None
    }
    
    # Delete existing unapproved snapshot if exists
    await result_snapshots_collection.delete_many({"competition_id": comp_id, "is_approved": False})
    res = await result_snapshots_collection.insert_one(snapshot_doc)
    
    await competition_collection.update_one({"_id": ObjectId(comp_id)}, {"$set": {"result_status": "PENDING"}})
    
    return {"message": "Finalization complete. Snapshot generated for review.", "snapshot_id": str(res.inserted_id)}


@router.get("/{comp_id}/overview")
async def competition_dashboard_overview(comp_id: str, admin: dict = Depends(get_current_admin)):
    """Admin Dashboard overview for competition."""
    comp = await competition_collection.find_one({"_id": ObjectId(comp_id)})
    if not comp: raise HTTPException(status_code=404, detail="Competition not found")
    
    total_participants = await competition_submission_collection.count_documents({"competition_id": comp_id, "status": {"$in": ["APPROVED", "SUBMITTED"]}})
    total_votes = await audience_vote_collection.count_documents({"competition_id": comp_id})
    total_scores = await judge_score_collection.count_documents({"competition_id": comp_id})
    
    reg_lifecycle = evaluate_competition_registration_status(comp)
    total_confirmed_regs = await competition_registration_collection.count_documents({"competition_id": comp_id, "status": "CONFIRMED"})

    return {
        "competition_id": comp_id,
        "title": comp.get("title"),
        "ranking_mode": {
            "audience_voting_enabled": comp.get("audience_voting_enabled"),
            "judge_scoring_enabled": comp.get("judge_scoring_enabled"),
        },
        "lifecycle": {
            "registration_status": reg_lifecycle["registration_status"],
            "is_registration_open": reg_lifecycle["is_registration_open"],
            "can_register": reg_lifecycle["can_register"],
            "registration_deadline": reg_lifecycle["registration_deadline"],
            "voting_status": comp.get("voting_status"),
            "judging_status": comp.get("judging_status"),
            "result_status": comp.get("result_status"),
        },
        "metrics": {
            "total_registered": total_confirmed_regs,
            "total_participants": total_participants,
            "total_audience_votes": total_votes,
            "total_judge_scores": total_scores,
        },
    }


@router.get("/{comp_id}/participants")
async def list_competition_participants(
    comp_id: str,
    status: Optional[str] = None,
    payment_status: Optional[str] = None,
    payment_method: Optional[str] = None,
    search: Optional[str] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    admin: dict = Depends(get_current_admin)
):
    """
    Super Admin inspects all registered participants for a competition,
    including registration details, payment verification (Cash vs Online), and file/link submissions.
    """
    try:
        c_oid = ObjectId(comp_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid competition ID format")

    comp = await competition_collection.find_one({"_id": c_oid})
    if not comp:
        raise HTTPException(status_code=404, detail="Competition not found")

    query: Dict[str, Any] = {"competition_id": str(comp_id)}
    if status and status != "ALL":
        query["status"] = status
    if payment_status and payment_status != "ALL":
        query["payment_status"] = payment_status

    if payment_method and payment_method != "ALL":
        pm = payment_method.upper()
        if pm == "CASH":
            query["$or"] = [{"payment_method": "CASH"}, {"cash_payment": True}]
        elif pm == "ONLINE":
            query["cash_payment"] = {"$ne": True}
            query["payment_method"] = {"$nin": ["CASH", "OFFLINE"]}
        elif pm == "FREE":
            query["$or"] = [{"fee": 0.0}, {"fee": 0}, {"payment_status": "NOT_REQUIRED"}]

    if search:
        s_reg = {"$regex": re.escape(search), "$options": "i"}
        search_or = [
            {"user_email": s_reg},
            {"registration_reference": s_reg},
            {"user_name": s_reg},
        ]
        if "$or" in query:
            query["$and"] = [{"$or": query.pop("$or")}, {"$or": search_or}]
        else:
            query["$or"] = search_or

    total = await competition_registration_collection.count_documents(query)
    raw_regs = await competition_registration_collection.find(query).sort("registered_at", -1).skip(skip).limit(limit).to_list(length=limit)

    emails = list({r.get("user_email") for r in raw_regs if r.get("user_email")})

    users_map = {}
    if emails:
        usrs = await user_collection.find({"email": {"$in": emails}}).to_list(length=len(emails))
        users_map = {u.get("email"): u for u in usrs}

    submissions_map = {}
    if emails:
        subs = await competition_submission_collection.find({
            "competition_id": str(comp_id),
            "user_email": {"$in": emails}
        }).to_list(length=len(emails) * 2)
        for s in subs:
            s["_id"] = str(s["_id"])
            submissions_map[s.get("user_email")] = {
                "submission_id": str(s.get("_id")),
                "status": s.get("status", "SUBMITTED"),
                "submitted_at": s.get("submitted_at"),
                "files": s.get("files", {})
            }

    all_regs = await competition_registration_collection.find({"competition_id": str(comp_id)}).to_list(length=5000)
    confirmed_count = sum(1 for r in all_regs if r.get("status") == "CONFIRMED" or r.get("payment_status") in ("PAID", "NOT_REQUIRED"))
    pending_count = sum(1 for r in all_regs if r.get("status") == "PENDING" and r.get("payment_status") != "PAID")
    total_submissions = await competition_submission_collection.count_documents({"competition_id": str(comp_id)})

    online_total = 0
    online_paid = 0
    online_pending = 0
    online_fees_collected = 0.0

    cash_total = 0
    cash_collected = 0
    cash_pending = 0
    cash_fees_collected = 0.0
    cash_fees_pending = 0.0

    free_total = 0

    for r in all_regs:
        f = float(r.get("fee", 0.0))
        is_c = bool(r.get("cash_payment") or str(r.get("payment_method", "")).upper() in ["CASH", "OFFLINE"])
        p_stat = str(r.get("payment_status", "")).upper()
        c_stat = str(r.get("cash_status", "")).upper()

        if f == 0.0 or p_stat == "NOT_REQUIRED":
            free_total += 1
        elif is_c:
            cash_total += 1
            if p_stat == "PAID" or c_stat in ["PAID", "COLLECTED"]:
                cash_collected += 1
                cash_fees_collected += f
            else:
                cash_pending += 1
                cash_fees_pending += f
        else:
            online_total += 1
            if p_stat == "PAID":
                online_paid += 1
                online_fees_collected += f
            else:
                online_pending += 1

    total_fees = online_fees_collected + cash_fees_collected

    participants_res = []
    for r in raw_regs:
        r_id = str(r["_id"])
        email = r.get("user_email", "")
        usr = users_map.get(email, {})
        sub = submissions_map.get(email)

        fee_val = float(r.get("fee", 0.0))
        is_cash = bool(r.get("cash_payment") or str(r.get("payment_method", "")).upper() in ["CASH", "OFFLINE"])
        raw_p_status = str(r.get("payment_status", "PAID")).upper()
        raw_c_status = str(r.get("cash_status", "")).upper()

        if fee_val == 0.0 or raw_p_status == "NOT_REQUIRED":
            method = "FREE"
            p_status = "NOT_REQUIRED"
            c_status = ""
        elif is_cash:
            method = "CASH"
            # If cash has been collected/paid
            if raw_c_status in ["PAID", "COLLECTED"] or raw_p_status == "PAID":
                p_status = "PAID"
                c_status = "COLLECTED"
            else:
                p_status = "PENDING"
                c_status = "PENDING"
        else:
            method = "ONLINE"
            p_status = raw_p_status
            c_status = ""

        participants_res.append({
            "_id": r_id,
            "registration_id": r_id,
            "registration_reference": r.get("registration_reference", f"CRG-{r_id[:8]}"),
            "competition_id": str(comp_id),
            "user_email": email,
            "user_name": usr.get("name") or r.get("user_name") or "Participant",
            "user_phone": usr.get("phone_number") or usr.get("phone") or r.get("user_phone") or "",
            "status": r.get("status", "CONFIRMED"),
            "payment_status": p_status,
            "payment_method": method,
            "cash_payment": is_cash,
            "cash_status": c_status,
            "cash_collected_at": r.get("cash_collected_at"),
            "cash_collected_by": r.get("cash_collected_by"),
            "fee": fee_val,
            "registered_at": r.get("registered_at"),
            "rules_accepted": bool(r.get("rules_accepted", True)),
            "submission": sub
        })

    return {
        "competition_id": str(comp_id),
        "competition_title": comp.get("title", "Competition"),
        "total": total,
        "total_registered": len(all_regs),
        "confirmed_count": confirmed_count,
        "pending_payment_count": pending_count,
        "submissions_count": total_submissions,
        "total_fees_collected": total_fees,
        "online_total": online_total,
        "online_paid": online_paid,
        "online_pending": online_pending,
        "online_fees_collected": online_fees_collected,
        "cash_total": cash_total,
        "cash_collected": cash_collected,
        "cash_pending": cash_pending,
        "cash_fees_collected": cash_fees_collected,
        "cash_fees_pending": cash_fees_pending,
        "free_total": free_total,
        "skip": skip,
        "limit": limit,
        "participants": participants_res
    }


@router.patch("/{comp_id}/participants/{reg_id}/confirm-cash")
async def confirm_participant_cash(
    comp_id: str,
    reg_id: str,
    admin: dict = Depends(get_current_admin)
):
    """
    Super Admin confirms receipt of cash payment at venue for a participant.
    Marks cash_status as 'COLLECTED', payment_status as 'PAID', and status as 'CONFIRMED'.
    """
    try:
        r_oid = ObjectId(reg_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid registration ID")

    reg = await competition_registration_collection.find_one({"_id": r_oid, "competition_id": str(comp_id)})
    if not reg:
        raise HTTPException(status_code=404, detail="Participant registration not found")

    now_iso = datetime.now(timezone.utc).isoformat()
    await competition_registration_collection.update_one(
        {"_id": r_oid},
        {
            "$set": {
                "cash_payment": True,
                "payment_method": "CASH",
                "cash_status": "COLLECTED",
                "payment_status": "PAID",
                "status": "CONFIRMED",
                "cash_collected_at": now_iso,
                "cash_collected_by": admin.get("email") or admin.get("name") or "admin",
                "updated_at": now_iso
            }
        }
    )
    return {
        "success": True,
        "message": "Cash payment verified and collected successfully at venue.",
        "registration_id": reg_id,
        "payment_status": "PAID",
        "cash_status": "COLLECTED",
        "cash_collected_at": now_iso
    }


@router.patch("/{comp_id}/participants/{reg_id}/revert-cash")
async def revert_participant_cash(
    comp_id: str,
    reg_id: str,
    admin: dict = Depends(get_current_admin)
):
    """Reverts cash payment status back to PENDING."""
    try:
        r_oid = ObjectId(reg_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid registration ID")

    reg = await competition_registration_collection.find_one({"_id": r_oid, "competition_id": str(comp_id)})
    if not reg:
        raise HTTPException(status_code=404, detail="Participant registration not found")

    now_iso = datetime.now(timezone.utc).isoformat()
    await competition_registration_collection.update_one(
        {"_id": r_oid},
        {
            "$set": {
                "cash_status": "PENDING",
                "payment_status": "PENDING",
                "updated_at": now_iso
            },
            "$unset": {
                "cash_collected_at": "",
                "cash_collected_by": ""
            }
        }
    )
    return {
        "success": True,
        "message": "Cash payment status reverted to PENDING.",
        "registration_id": reg_id,
        "payment_status": "PENDING",
        "cash_status": "PENDING"
    }

