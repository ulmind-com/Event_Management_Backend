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
    leaderboard_result_collection,
    user_collection
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
    """Returns approved submissions for this competition with scoring status."""
    await verify_judge_assignment(judge["email"], comp_id)
    subs = await competition_submission_collection.find({
        "competition_id": comp_id,
        "status": {"$in": [SubmissionStatus.SUBMITTED.value, SubmissionStatus.APPROVED.value, "Scored"]}
    }).to_list(length=1000)

    # Fetch all scores submitted by this judge for this competition
    existing_scores = await judge_score_collection.find({
        "competition_id": str(comp_id),
        "judge_email": judge["email"]
    }).to_list(length=1000)

    score_by_sub = {}
    score_by_user = {}
    for sc in existing_scores:
        if sc.get("submission_id"):
            score_by_sub[str(sc["submission_id"])] = sc
        u_email = (sc.get("user_email") or sc.get("participant_id") or "").lower().strip()
        if u_email:
            score_by_user[u_email] = sc

    # Fetch COI skips
    coi_skips = await coi_skip_collection.find({
        "competition_id": str(comp_id),
        "judge_email": judge["email"]
    }).to_list(length=1000)
    coi_set = {str(c["submission_id"]) for c in coi_skips if c.get("submission_id")}

    # Fetch drafts
    drafts = await draft_score_collection.find({
        "competition_id": str(comp_id),
        "judge_email": judge["email"]
    }).to_list(length=1000)
    draft_set = {str(d["submission_id"]) for d in drafts if d.get("submission_id")}

    # Populate missing contestant names in batch from user_collection
    missing_emails = [
        s.get("user_email") for s in subs
        if not s.get("user_name") and s.get("user_email")
    ]
    name_map = {}
    if missing_emails:
        try:
            users = await user_collection.find(
                {"email": {"$in": missing_emails}},
                {"email": 1, "name": 1}
            ).to_list(length=len(missing_emails))
            for u in users:
                name_map[u.get("email")] = u.get("name")
        except Exception:
            pass

    for s in subs:
        sid = str(s["_id"])
        s["_id"] = sid
        u_email = (s.get("user_email") or "").lower().strip()
        if not s.get("user_name") and u_email in name_map:
            s["user_name"] = name_map[u_email]

        sc = score_by_sub.get(sid) or (score_by_user.get(u_email) if u_email else None)
        if sc:
            s["is_scored"] = True
            s["has_scored"] = True
            s["is_locked"] = True
            s["status"] = "Scored"
            s["total_score"] = sc.get("total_score")
            s["my_score"] = sc.get("total_score")
            s["my_scores"] = sc.get("scores")
            s["my_comments"] = sc.get("comments")
            s["scored_at"] = sc.get("scored_at").isoformat() if hasattr(sc.get("scored_at"), "isoformat") else sc.get("scored_at")
        else:
            s["is_scored"] = False
            s["has_scored"] = False
            s["is_locked"] = False
            if sid in coi_set:
                s["status"] = "Skipped"
                s["is_skipped"] = True
            elif sid in draft_set:
                s["has_draft"] = True

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
    """Auto-saves partial scores as a draft. Strictly blocked if already officially scored."""
    await verify_judge_assignment(judge["email"], comp_id)

    # Check if participant has already been officially scored
    submission = await competition_submission_collection.find_one({
        "_id": ObjectId(submission_id) if ObjectId.is_valid(submission_id) else submission_id
    })
    participant_email = (submission.get("user_email") or "").strip() if submission else ""

    score_query = {
        "competition_id": str(comp_id),
        "judge_email": judge["email"],
        "$or": [{"submission_id": str(submission_id)}]
    }
    if ObjectId.is_valid(submission_id):
        score_query["$or"].append({"submission_id": ObjectId(submission_id)})
    if participant_email:
        score_query["$or"].extend([
            {"participant_id": participant_email},
            {"user_email": participant_email}
        ])

    already_scored = await judge_score_collection.find_one(score_query)
    if already_scored:
        raise HTTPException(
            status_code=400,
            detail="Official score has already been submitted for this participant in this competition. Drafts cannot be saved."
        )

    doc = {
        "competition_id": str(comp_id),
        "submission_id": str(submission_id),
        "judge_email": judge["email"],
        "scores": req.scores,
        "comments": req.comments,
        "updated_at": datetime.now(timezone.utc)
    }
    await draft_score_collection.update_one(
        {"competition_id": str(comp_id), "submission_id": str(submission_id), "judge_email": judge["email"]},
        {"$set": doc},
        upsert=True
    )
    return {"message": "Draft saved."}

@router.get("/{comp_id}/contestants/{submission_id}/draft")
async def get_draft_score(comp_id: str, submission_id: str, judge: dict = Depends(get_current_judge)):
    """Retrieves a previously saved draft or official score for this submission."""
    await verify_judge_assignment(judge["email"], comp_id)

    # First check if an official score was already submitted
    submission = await competition_submission_collection.find_one({
        "_id": ObjectId(submission_id) if ObjectId.is_valid(submission_id) else submission_id
    })
    participant_email = (submission.get("user_email") or "").strip() if submission else ""

    score_query = {
        "competition_id": str(comp_id),
        "judge_email": judge["email"],
        "$or": [{"submission_id": str(submission_id)}]
    }
    if ObjectId.is_valid(submission_id):
        score_query["$or"].append({"submission_id": ObjectId(submission_id)})
    if participant_email:
        score_query["$or"].extend([
            {"participant_id": participant_email},
            {"user_email": participant_email}
        ])

    already_scored = await judge_score_collection.find_one(score_query)
    if already_scored:
        return {
            "message": "Official score has already been submitted for this participant.",
            "scores": already_scored.get("scores", {}),
            "total_score": already_scored.get("total_score", 0.0),
            "comments": already_scored.get("comments"),
            "is_locked": True,
            "is_scored": True,
            "already_scored": True,
            "scored_at": already_scored.get("scored_at").isoformat() if hasattr(already_scored.get("scored_at"), "isoformat") else already_scored.get("scored_at")
        }

    draft = await draft_score_collection.find_one({
        "competition_id": str(comp_id), "submission_id": str(submission_id), "judge_email": judge["email"]
    })
    if not draft:
        return {"message": "No draft found.", "scores": {}, "comments": None, "is_locked": False, "is_scored": False}
    draft["_id"] = str(draft["_id"])
    draft["is_locked"] = False
    draft["is_scored"] = False
    return draft

# ══════════════════════════════════════════════
# 4. COI / SKIP CONTESTANT
# ══════════════════════════════════════════════

@router.post("/{comp_id}/contestants/{submission_id}/skip")
async def skip_contestant(comp_id: str, submission_id: str, req: COISkipReq, judge: dict = Depends(get_current_judge)):
    """Judge declares Conflict of Interest and skips scoring this contestant."""
    await verify_judge_assignment(judge["email"], comp_id)

    submission = await competition_submission_collection.find_one({
        "_id": ObjectId(submission_id) if ObjectId.is_valid(submission_id) else submission_id
    })
    participant_email = (submission.get("user_email") or "").strip() if submission else ""

    score_query = {
        "competition_id": str(comp_id),
        "judge_email": judge["email"],
        "$or": [{"submission_id": str(submission_id)}]
    }
    if ObjectId.is_valid(submission_id):
        score_query["$or"].append({"submission_id": ObjectId(submission_id)})
    if participant_email:
        score_query["$or"].extend([
            {"participant_id": participant_email},
            {"user_email": participant_email}
        ])

    already_scored = await judge_score_collection.find_one(score_query)
    if already_scored:
        raise HTTPException(
            status_code=400,
            detail="You have already submitted an official score for this participant. Cannot declare COI or skip."
        )

    doc = {
        "competition_id": str(comp_id),
        "submission_id": str(submission_id),
        "judge_email": judge["email"],
        "reason": req.reason,
        "skipped_at": datetime.now(timezone.utc)
    }
    await coi_skip_collection.update_one(
        {"competition_id": str(comp_id), "submission_id": str(submission_id), "judge_email": judge["email"]},
        {"$set": doc},
        upsert=True
    )
    return {"message": "Contestant skipped due to Conflict of Interest."}

# ══════════════════════════════════════════════
# 5. FINAL SCORE SUBMISSION (with WebSocket broadcast)
# ══════════════════════════════════════════════

@router.post("/submissions/{submission_id}/scores")
async def submit_score(submission_id: str, req: ScoreReq, judge: dict = Depends(get_current_judge)):
    """
    Submits final official scores.
    STRICT ENFORCEMENT: A participant can only be scored once per judge in a competition.
    Subsequent scoring attempts for the same participant are strictly rejected.
    """
    try:
        sub_id = ObjectId(submission_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid submission ID")

    submission = await competition_submission_collection.find_one({"_id": sub_id})
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    comp_id = str(submission["competition_id"])
    await verify_judge_assignment(judge["email"], comp_id)

    # Check if session-based judging is active
    session = await judging_session_collection.find_one({"competition_id": comp_id, "status": "OPEN"})
    if session and session.get("pin"):
        if judge["email"] not in session.get("verified_judges", []):
            raise HTTPException(status_code=403, detail="You must verify the session PIN before scoring.")

    if submission.get("status") not in [SubmissionStatus.SUBMITTED.value, SubmissionStatus.APPROVED.value, "Scored"]:
        raise HTTPException(status_code=400, detail="Submission is not in a scorable state.")

    comp = await competition_collection.find_one({"_id": ObjectId(comp_id)})
    if not comp:
        raise HTTPException(status_code=404, detail="Competition not found")
        
    if not comp.get("judge_scoring_enabled", False):
        raise HTTPException(status_code=400, detail="Judge scoring is disabled for this competition.")
        
    if comp.get("judging_status", "OPEN") == "CLOSED":
        raise HTTPException(status_code=400, detail="Judging phase is closed for this competition.")

    # Check COI skip
    coi = await coi_skip_collection.find_one({
        "competition_id": comp_id, "submission_id": str(submission_id), "judge_email": judge["email"]
    })
    if coi:
        raise HTTPException(status_code=400, detail="You have declared COI for this contestant. Cannot score.")

    # ══════════════════════════════════════════════
    # STRICT SINGLE-SCORE ENFORCEMENT
    # Once a participant has been scored by this judge, they CANNOT be scored again!
    # ══════════════════════════════════════════════
    participant_email = (submission.get("user_email") or "").strip()
    score_query = {
        "competition_id": comp_id,
        "judge_email": judge["email"],
        "$or": [
            {"submission_id": str(submission_id)},
            {"submission_id": sub_id}
        ]
    }
    if participant_email:
        score_query["$or"].extend([
            {"participant_id": participant_email},
            {"user_email": participant_email}
        ])

    existing_score = await judge_score_collection.find_one(score_query)
    if existing_score:
        raise HTTPException(
            status_code=400,
            detail="You have already submitted an official score for this participant in this competition. Each participant can only be scored once."
        )

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
        "submission_id": str(submission_id),
        "competition_id": comp_id,
        "participant_id": participant_email,
        "user_email": participant_email,
        "judge_email": judge["email"],
        "scores": req.scores,
        "total_score": total_score,
        "comments": req.comments,
        "scored_at": datetime.now(timezone.utc),
        "is_locked": True,
        "is_final": True,
        "status": "SUBMITTED"
    }

    await judge_score_collection.update_one(
        {"submission_id": str(submission_id), "judge_email": judge["email"]},
        {"$set": doc},
        upsert=True
    )

    # Mark submission with scored_by_judges
    await competition_submission_collection.update_one(
        {"_id": sub_id},
        {"$addToSet": {"scored_by_judges": judge["email"]}}
    )

    # Delete draft after final submission
    await draft_score_collection.delete_one({
        "competition_id": comp_id, "submission_id": str(submission_id), "judge_email": judge["email"]
    })

    # ── WebSocket: Broadcast real-time update to Admin + Users ──
    await ws_manager.broadcast_to_competition(comp_id, {
        "event": "score_submitted",
        "competition_id": comp_id,
        "submission_id": str(submission_id),
        "participant_id": participant_email,
        "judge_email": judge["email"],
        "total_score": total_score,
        "timestamp": datetime.now(timezone.utc).isoformat()
    })

    return {
        "message": "Official score submitted successfully",
        "total_score": total_score,
        "is_locked": True,
        "participant_id": participant_email
    }

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
