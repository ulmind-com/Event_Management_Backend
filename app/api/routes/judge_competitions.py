from fastapi import APIRouter, Depends, HTTPException
from bson import ObjectId
from app.api.deps import get_current_user
from app.core.database import (
    competition_collection,
    competition_submission_collection,
    judge_score_collection,
    judge_assignment_collection,
    judging_session_collection,
    draft_score_collection,
    judge_feedback_collection,
    coi_skip_collection,
    leaderboard_result_collection
)
from app.schemas.competition import (
    ScoreReq, SubmissionStatus, DraftScoreReq, COISkipReq,
    JudgeFeedbackReq, SessionPINReq
)
from app.core.websocket import ws_manager
from datetime import datetime, timezone

router = APIRouter()

# ══════════════════════════════════════════════
# AUTH HELPERS
# ══════════════════════════════════════════════

async def get_current_judge(user: dict = Depends(get_current_user)):
    """Verifies that the current user has the JUDGE role."""
    if user.get("role") != "JUDGE":
        raise HTTPException(status_code=403, detail="Not authorized as judge")
    if user.get("status") != "ACTIVE":
        raise HTTPException(status_code=403, detail="Judge account is not active")
    return user

async def verify_judge_assignment(judge_email: str, competition_id: str):
    """Verifies that the judge is strictly assigned to this competition."""
    assignment = await judge_assignment_collection.find_one({
        "judge_email": judge_email,
        "competition_id": competition_id
    })
    if not assignment:
        raise HTTPException(status_code=403, detail="You are not authorized to view or score this competition.")

async def verify_session_pin(judge_email: str, comp_id: str):
    """If a live session with PIN exists, verify judge has validated it."""
    session = await judging_session_collection.find_one({
        "competition_id": comp_id, "status": "OPEN"
    })
    if not session:
        raise HTTPException(status_code=403, detail="Live judging session is not open for this competition.")
    if session.get("pin"):
        if judge_email not in session.get("verified_judges", []):
            raise HTTPException(status_code=403, detail="You must verify the session PIN before scoring.")

# ══════════════════════════════════════════════
# 1. BASIC JUDGE APIs
# ══════════════════════════════════════════════

@router.get("/")
async def list_assigned_competitions(judge: dict = Depends(get_current_judge)):
    """Returns only competitions the judge is assigned to."""
    assignments = await judge_assignment_collection.find({"judge_email": judge["email"]}).to_list(length=100)
    comp_ids = [ObjectId(a["competition_id"]) for a in assignments]
    comps = await competition_collection.find({"_id": {"$in": comp_ids}}).to_list(length=100)
    for c in comps:
        c["_id"] = str(c["_id"])
    return comps

@router.get("/{comp_id}/contestants")
async def list_contestants(comp_id: str, judge: dict = Depends(get_current_judge)):
    """Returns approved submissions for this competition."""
    await verify_judge_assignment(judge["email"], comp_id)
    subs = await competition_submission_collection.find({
        "competition_id": comp_id,
        "status": {"$in": [SubmissionStatus.SUBMITTED.value, SubmissionStatus.APPROVED.value]}
    }).to_list(length=1000)
    for s in subs:
        s["_id"] = str(s["_id"])
    return subs

# ══════════════════════════════════════════════
# 2. SESSION PIN VERIFICATION
# ══════════════════════════════════════════════

@router.post("/{comp_id}/verify-pin")
async def verify_pin(comp_id: str, req: SessionPINReq, judge: dict = Depends(get_current_judge)):
    """Judge submits the PIN to unlock scoring for this live session."""
    await verify_judge_assignment(judge["email"], comp_id)
    session = await judging_session_collection.find_one({
        "competition_id": comp_id, "status": "OPEN"
    })
    if not session:
        raise HTTPException(status_code=404, detail="No active judging session for this competition.")
    if not session.get("pin"):
        return {"message": "This session does not require a PIN."}
    if session["pin"] != req.pin:
        raise HTTPException(status_code=403, detail="Incorrect PIN.")

    await judging_session_collection.update_one(
        {"_id": session["_id"]},
        {"$addToSet": {"verified_judges": judge["email"]}}
    )
    return {"message": "PIN verified. You can now score contestants."}

# ══════════════════════════════════════════════
# 3. AUTO-SAVE DRAFT SCORES
# ══════════════════════════════════════════════

@router.post("/{comp_id}/contestants/{submission_id}/draft")
async def save_draft_score(comp_id: str, submission_id: str, req: DraftScoreReq, judge: dict = Depends(get_current_judge)):
    """Auto-saves partial scores as a draft. Does not count as official."""
    await verify_judge_assignment(judge["email"], comp_id)
    doc = {
        "competition_id": comp_id,
        "submission_id": submission_id,
        "judge_email": judge["email"],
        "scores": req.scores,
        "comments": req.comments,
        "updated_at": datetime.now(timezone.utc)
    }
    await draft_score_collection.update_one(
        {"competition_id": comp_id, "submission_id": submission_id, "judge_email": judge["email"]},
        {"$set": doc},
        upsert=True
    )
    return {"message": "Draft saved."}

@router.get("/{comp_id}/contestants/{submission_id}/draft")
async def get_draft_score(comp_id: str, submission_id: str, judge: dict = Depends(get_current_judge)):
    """Retrieves a previously saved draft for this submission."""
    await verify_judge_assignment(judge["email"], comp_id)
    draft = await draft_score_collection.find_one({
        "competition_id": comp_id, "submission_id": submission_id, "judge_email": judge["email"]
    })
    if not draft:
        return {"message": "No draft found.", "scores": {}, "comments": None}
    draft["_id"] = str(draft["_id"])
    return draft

# ══════════════════════════════════════════════
# 4. COI / SKIP CONTESTANT
# ══════════════════════════════════════════════

@router.post("/{comp_id}/contestants/{submission_id}/skip")
async def skip_contestant(comp_id: str, submission_id: str, req: COISkipReq, judge: dict = Depends(get_current_judge)):
    """Judge declares Conflict of Interest and skips scoring this contestant."""
    await verify_judge_assignment(judge["email"], comp_id)
    doc = {
        "competition_id": comp_id,
        "submission_id": submission_id,
        "judge_email": judge["email"],
        "reason": req.reason,
        "skipped_at": datetime.now(timezone.utc)
    }
    await coi_skip_collection.update_one(
        {"competition_id": comp_id, "submission_id": submission_id, "judge_email": judge["email"]},
        {"$set": doc},
        upsert=True
    )
    return {"message": "Contestant skipped due to Conflict of Interest."}

# ══════════════════════════════════════════════
# 5. FINAL SCORE SUBMISSION (with WebSocket broadcast)
# ══════════════════════════════════════════════

@router.post("/submissions/{submission_id}/scores")
async def submit_score(submission_id: str, req: ScoreReq, judge: dict = Depends(get_current_judge)):
    """Submits final scores. Broadcasts real-time update via WebSocket."""
    try:
        sub_id = ObjectId(submission_id)
    except:
        raise HTTPException(status_code=400, detail="Invalid submission ID")

    submission = await competition_submission_collection.find_one({"_id": sub_id})
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    comp_id = submission["competition_id"]
    await verify_judge_assignment(judge["email"], comp_id)

    # Check if session-based judging is active
    session = await judging_session_collection.find_one({"competition_id": comp_id, "status": "OPEN"})
    if session and session.get("pin"):
        if judge["email"] not in session.get("verified_judges", []):
            raise HTTPException(status_code=403, detail="You must verify the session PIN before scoring.")

    if submission.get("status") not in [SubmissionStatus.SUBMITTED.value, SubmissionStatus.APPROVED.value]:
        raise HTTPException(status_code=400, detail="Submission is not in a scorable state.")

    comp = await competition_collection.find_one({"_id": ObjectId(comp_id)})

    # Check COI skip
    coi = await coi_skip_collection.find_one({
        "competition_id": comp_id, "submission_id": submission_id, "judge_email": judge["email"]
    })
    if coi:
        raise HTTPException(status_code=400, detail="You have declared COI for this contestant. Cannot score.")

    # Check Score Lock
    existing_score = await judge_score_collection.find_one({
        "submission_id": submission_id, "judge_email": judge["email"]
    })
    if existing_score and existing_score.get("is_locked"):
        raise HTTPException(status_code=403, detail="Scores are locked and cannot be modified.")

    # Validate scores against criteria
    criteria = comp.get("judging_criteria", [])
    valid_names = {c["name"]: c["max_score"] for c in criteria}

    total_score = 0.0
    for crit_name, score_val in req.scores.items():
        if crit_name not in valid_names:
            raise HTTPException(status_code=400, detail=f"Unknown criterion: {crit_name}")
        if score_val < 0 or score_val > valid_names[crit_name]:
            raise HTTPException(status_code=400, detail=f"Score {score_val} for {crit_name} out of bounds (0-{valid_names[crit_name]}).")
        weight = next((c.get("weight", 1.0) for c in criteria if c["name"] == crit_name), 1.0)
        total_score += (score_val * weight)

    doc = {
        "submission_id": submission_id,
        "competition_id": comp_id,
        "judge_email": judge["email"],
        "scores": req.scores,
        "total_score": total_score,
        "comments": req.comments,
        "scored_at": datetime.now(timezone.utc),
        "is_locked": False
    }

    await judge_score_collection.update_one(
        {"submission_id": submission_id, "judge_email": judge["email"]},
        {"$set": doc},
        upsert=True
    )

    # Delete draft after final submission
    await draft_score_collection.delete_one({
        "competition_id": comp_id, "submission_id": submission_id, "judge_email": judge["email"]
    })

    # ── WebSocket: Broadcast real-time update to Admin + Users ──
    await ws_manager.broadcast_to_competition(comp_id, {
        "event": "score_submitted",
        "competition_id": comp_id,
        "submission_id": submission_id,
        "judge_email": judge["email"],
        "total_score": total_score,
        "timestamp": datetime.now(timezone.utc).isoformat()
    })

    return {"message": "Score submitted successfully", "total_score": total_score}

# ══════════════════════════════════════════════
# 6. JUDGE FEEDBACK FOR CONTESTANTS
# ══════════════════════════════════════════════

@router.post("/{comp_id}/contestants/{submission_id}/feedback")
async def submit_feedback(comp_id: str, submission_id: str, req: JudgeFeedbackReq, judge: dict = Depends(get_current_judge)):
    """Judge submits text or audio feedback for a contestant."""
    await verify_judge_assignment(judge["email"], comp_id)
    doc = {
        "competition_id": comp_id,
        "submission_id": submission_id,
        "judge_email": judge["email"],
        "judge_name": judge.get("full_name", judge["email"]),
        "text_feedback": req.text_feedback,
        "audio_feedback_url": req.audio_feedback_url,
        "submitted_at": datetime.now(timezone.utc)
    }
    await judge_feedback_collection.update_one(
        {"competition_id": comp_id, "submission_id": submission_id, "judge_email": judge["email"]},
        {"$set": doc},
        upsert=True
    )
    return {"message": "Feedback submitted successfully."}

# ══════════════════════════════════════════════
# 7. JUDGE PROGRESS VIEW
# ══════════════════════════════════════════════

@router.get("/{comp_id}/my-progress")
async def my_progress(comp_id: str, judge: dict = Depends(get_current_judge)):
    """Shows this judge how many contestants they have scored, skipped, or have pending."""
    await verify_judge_assignment(judge["email"], comp_id)

    total_subs = await competition_submission_collection.count_documents({
        "competition_id": comp_id,
        "status": {"$in": [SubmissionStatus.SUBMITTED.value, SubmissionStatus.APPROVED.value]}
    })
    scored = await judge_score_collection.count_documents({
        "competition_id": comp_id, "judge_email": judge["email"]
    })
    skipped = await coi_skip_collection.count_documents({
        "competition_id": comp_id, "judge_email": judge["email"]
    })
    drafts = await draft_score_collection.count_documents({
        "competition_id": comp_id, "judge_email": judge["email"]
    })

    return {
        "total_contestants": total_subs,
        "scored": scored,
        "skipped_coi": skipped,
        "drafts_in_progress": drafts,
        "pending": total_subs - scored - skipped,
        "completion_pct": round((scored + skipped) / total_subs * 100, 1) if total_subs > 0 else 0
    }
