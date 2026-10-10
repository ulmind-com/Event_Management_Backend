from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from bson import ObjectId
from app.core.admin_auth import get_current_admin
from app.core.database import (
    competition_collection, 
    competition_registration_collection,
    competition_submission_collection,
    judge_score_collection,
    audience_vote_collection,
    leaderboard_result_collection
)
from app.schemas.competition import CompetitionCreateReq, CompetitionUpdateReq
from datetime import datetime, timezone

router = APIRouter()

@router.post("/")
async def create_competition(req: CompetitionCreateReq, admin: dict = Depends(get_current_admin)):
    """Creates a new advanced competition with rules, criteria, and dynamic fields."""
    comp_dict = req.model_dump()
    
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
    
    comp_dict["created_at"] = datetime.now(timezone.utc)
    comp_dict["updated_at"] = datetime.now(timezone.utc)
    
    res = await competition_collection.insert_one(comp_dict)
    comp_dict["_id"] = str(res.inserted_id)
    return comp_dict

@router.get("/")
async def list_competitions(admin: dict = Depends(get_current_admin)):
    """List all competitions for Admin."""
    comps = await competition_collection.find().sort("created_at", -1).to_list(length=100)
    for c in comps:
        c["_id"] = str(c["_id"])
    return comps

@router.patch("/{comp_id}")
async def update_competition(comp_id: str, req: CompetitionUpdateReq, admin: dict = Depends(get_current_admin)):
    """Updates competition fields."""
    update_data = {k: v for k, v in req.model_dump().items() if v is not None}
    if not update_data:
        return {"message": "No fields to update"}
        
    update_data["updated_at"] = datetime.now(timezone.utc)
    
    res = await competition_collection.update_one(
        {"_id": ObjectId(comp_id)},
        {"$set": update_data}
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Competition not found")
        
    return {"message": "Competition updated successfully"}

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
    Recalculates the leaderboard ONLY from LOCKED scores.
    This acts as the official provisional ranking.
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
        docs.append({
            "competition_id": comp_id,
            "submission_id": s["_id"],
            "total_score": s["total_judge_score"],
            "rank": 0,
            "status": "APPROVED"
        })
        
    if not docs:
        return {"message": "No locked scores available to calculate."}
        
    docs.sort(key=lambda x: x["total_score"], reverse=True)
    current_rank = 1
    for i, doc in enumerate(docs):
        if i > 0 and docs[i]["total_score"] == docs[i-1]["total_score"]:
            doc["rank"] = docs[i-1]["rank"]
        else:
            doc["rank"] = current_rank
        current_rank += 1
            
    await leaderboard_result_collection.insert_many(docs)
    return {"message": "Results approved and Leaderboard generated successfully.", "total_ranked": len(docs)}

@router.post("/{comp_id}/results/publish")
async def publish_results(comp_id: str, admin: dict = Depends(get_current_admin)):
    """Publishes the approved leaderboard to the public."""
    # In a real app, this might just flip a boolean on the Competition.
    res = await competition_collection.update_one(
        {"_id": ObjectId(comp_id)},
        {"$set": {"results_published": True, "results_published_at": datetime.now(timezone.utc)}}
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Competition not found")
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
    
    total_judges = await judge_assignment_collection.count_documents({"competition_id": comp_id})
    total_subs = await competition_submission_collection.count_documents({
        "competition_id": comp_id,
        "status": {"$in": ["SUBMITTED", "APPROVED"]}
    })
    total_scores = await judge_score_collection.count_documents({"competition_id": comp_id})
    locked_scores = await judge_score_collection.count_documents({"competition_id": comp_id, "is_locked": True})
    total_skips = await coi_skip_collection.count_documents({"competition_id": comp_id})
    
    expected_total = total_judges * total_subs
    completed = total_scores + total_skips
    
    # Per-judge breakdown
    judges = await judge_assignment_collection.find({"competition_id": comp_id}).to_list(100)
    judge_details = []
    for j in judges:
        email = j["judge_email"]
        scored = await judge_score_collection.count_documents({"competition_id": comp_id, "judge_email": email})
        skipped = await coi_skip_collection.count_documents({"competition_id": comp_id, "judge_email": email})
        judge_details.append({
            "judge_email": email,
            "scored": scored,
            "skipped": skipped,
            "pending": total_subs - scored - skipped,
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
        "judges": judge_details
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
        user = await user_collection.find_one({"email": sub["user_email"]})
        if user:
            participants_data.append({
                "name": user.get("name", "Unknown"),
                "email": user.get("email", ""),
                "phone": user.get("phone_number", ""),
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
        votes = await audience_vote_collection.count_documents({"competition_id": comp_id, "participant_id": user_email})
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

@router.post("/{comp_id}/results/approve")
async def approve_results(comp_id: str, admin: dict = Depends(get_current_admin)):
    from app.core.database import db
    result_snapshots = db["result_snapshots"]
    
    snapshot = await result_snapshots.find_one({"competition_id": comp_id, "is_published": False})
    if not snapshot:
        raise HTTPException(status_code=404, detail="No un-published snapshot found. Start finalization first.")
        
    await result_snapshots.update_one({"_id": snapshot["_id"]}, {"$set": {"is_approved": True, "approved_by": admin.get("email"), "approved_at": datetime.now(timezone.utc)}})
    await competition_collection.update_one({"_id": ObjectId(comp_id)}, {"$set": {"result_status": "APPROVED"}})
    return {"message": "Result snapshot approved successfully."}

@router.post("/{comp_id}/results/publish")
async def publish_results(comp_id: str, admin: dict = Depends(get_current_admin)):
    from app.core.database import db
    result_snapshots = db["result_snapshots"]
    
    snapshot = await result_snapshots.find_one({"competition_id": comp_id, "is_approved": True})
    if not snapshot:
        raise HTTPException(status_code=400, detail="Result must be approved before publication.")
        
    await result_snapshots.update_one({"_id": snapshot["_id"]}, {"$set": {"is_published": True, "published_by": admin.get("email"), "published_at": datetime.now(timezone.utc)}})
    await competition_collection.update_one({"_id": ObjectId(comp_id)}, {"$set": {"result_status": "PUBLISHED"}})
    return {"message": "Result published officially!"}

@router.get("/{comp_id}/overview")
async def competition_dashboard_overview(comp_id: str, admin: dict = Depends(get_current_admin)):
    """Admin Dashboard overview for competition."""
    comp = await competition_collection.find_one({"_id": ObjectId(comp_id)})
    if not comp: raise HTTPException(status_code=404, detail="Competition not found")
    
    total_participants = await competition_submission_collection.count_documents({"competition_id": comp_id, "status": {"$in": ["APPROVED", "SUBMITTED"]}})
    total_votes = await audience_vote_collection.count_documents({"competition_id": comp_id})
    total_scores = await judge_score_collection.count_documents({"competition_id": comp_id})
    
    return {
        "competition_id": comp_id,
        "title": comp.get("title"),
        "ranking_mode": {
            "audience_voting_enabled": comp.get("audience_voting_enabled"),
            "judge_scoring_enabled": comp.get("judge_scoring_enabled"),
        },
        "lifecycle": {
            "voting_status": comp.get("voting_status"),
            "judging_status": comp.get("judging_status"),
            "result_status": comp.get("result_status")
        },
        "metrics": {
            "total_participants": total_participants,
            "total_audience_votes": total_votes,
            "total_judge_scores": total_scores
        }
    }
