import re
from fastapi import APIRouter, Depends, HTTPException
from bson import ObjectId
from app.api.deps import get_current_user
from app.core.database import (
    competition_collection,
    competition_submission_collection,
    competition_registration_collection,
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
    clean_email = (judge_email or "").strip()
    clean_cid = str(competition_id).strip()
    comp_matches = [clean_cid]
    if ObjectId.is_valid(clean_cid):
        comp_matches.append(ObjectId(clean_cid))

    assignment = await judge_assignment_collection.find_one({
        "judge_email": {"$regex": f"^{re.escape(clean_email)}$", "$options": "i"},
        "competition_id": {"$in": comp_matches}
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
    clean_email = (judge.get("email") or "").strip()
    assignments = await judge_assignment_collection.find({
        "judge_email": {"$regex": f"^{re.escape(clean_email)}$", "$options": "i"}
    }).to_list(length=100)
    
    valid_ids = []
    str_ids = []
    for a in assignments:
        cid = str(a.get("competition_id", "")).strip()
        if cid:
            str_ids.append(cid)
            if ObjectId.is_valid(cid):
                valid_ids.append(ObjectId(cid))
                
    comps = await competition_collection.find({
        "$or": [
            {"_id": {"$in": valid_ids}},
            {"_id": {"$in": str_ids}},
            {"id": {"$in": str_ids}}
        ]
    }).to_list(length=100)
    for c in comps:
        c["_id"] = str(c["_id"])
    return comps

def extract_submission_url(sub: dict) -> str:
    """Extracts a valid previewable or downloadable URL from submission data."""
    # 1. Direct fields
    for k in ["submission_url", "media_url", "file_url", "url", "link"]:
        val = sub.get(k)
        if isinstance(val, str) and val.strip():
            return val.strip()

    # 2. In files dict, list, or str
    files = sub.get("files")
    if isinstance(files, str) and files.strip():
        return files.strip()
    elif isinstance(files, dict):
        for key in ["submission_file", "file", "url", "submission_url", "video", "audio", "document"]:
            val = files.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        for val in files.values():
            if isinstance(val, str) and val.strip():
                return val.strip()
    elif isinstance(files, (list, tuple)) and files:
        first = files[0]
        if isinstance(first, str) and first.strip():
            return first.strip()
        elif isinstance(first, dict):
            for val in first.values():
                if isinstance(val, str) and val.strip():
                    return val.strip()
    return ""


@router.get("/{comp_id}/contestants")
async def list_contestants(comp_id: str, judge: dict = Depends(get_current_judge)):
    """
    Returns approved submissions for this competition with scoring status and enriched contestant profiles:
    - User Table Join: full_name, profile_photo / avatar
    - Registration Table Join: registration_reference (CRG-xxxxx), participant name, phone
    - Formatted submission_url & media_url for instant preview
    """
    await verify_judge_assignment(judge["email"], comp_id)

    sub_query = {
        "$or": [{"competition_id": str(comp_id)}],
        "status": {"$in": [SubmissionStatus.SUBMITTED.value, SubmissionStatus.APPROVED.value, "Scored"]}
    }
    if ObjectId.is_valid(comp_id):
        sub_query["$or"].append({"competition_id": ObjectId(comp_id)})

    subs = await competition_submission_collection.find(sub_query).to_list(length=1000)

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

    # Collect all emails for batch joins
    all_emails = list({
        (s.get("user_email") or "").strip().lower()
        for s in subs if s.get("user_email")
    })

    # Batch Join 1: user_collection (name, full_name, profile_photo, phone)
    user_map = {}
    if all_emails:
        try:
            users = await user_collection.find(
                {"email": {"$in": all_emails}},
                {"email": 1, "name": 1, "full_name": 1, "profile_photo": 1, "photo_url": 1, "avatar": 1, "phone": 1, "phone_number": 1}
            ).to_list(length=len(all_emails) * 2)
            for u in users:
                em = (u.get("email") or "").strip().lower()
                if em:
                    user_map[em] = u
        except Exception:
            pass

    # Batch Join 2: competition_registration_collection (registration_reference CRG-xxxxx, participant name, phone)
    reg_map = {}
    if all_emails:
        try:
            reg_query = {
                "user_email": {"$in": all_emails},
                "$or": [{"competition_id": str(comp_id)}]
            }
            if ObjectId.is_valid(comp_id):
                reg_query["$or"].append({"competition_id": ObjectId(comp_id)})
            regs = await competition_registration_collection.find(reg_query).to_list(length=len(all_emails) * 2)
            for r in regs:
                em = (r.get("user_email") or "").strip().lower()
                if em:
                    reg_map[em] = r
        except Exception:
            pass

    for s in subs:
        sid = str(s["_id"])
        s["_id"] = sid
        u_email = (s.get("user_email") or "").strip().lower()
        u_info = user_map.get(u_email, {})
        r_info = reg_map.get(u_email, {})

        # 1. Resolve User Name / Full Name
        real_name = (
            s.get("user_name")
            or r_info.get("user_name")
            or u_info.get("full_name")
            or u_info.get("name")
            or r_info.get("participant_name")
            or (u_email.split("@")[0] if u_email else "Contestant")
        )
        s["user_name"] = real_name
        s["full_name"] = real_name
        s["name"] = real_name

        # 2. Resolve Profile Photo / Avatar
        photo = (
            u_info.get("profile_photo")
            or u_info.get("photo_url")
            or u_info.get("avatar")
            or r_info.get("profile_photo")
            or s.get("profile_photo")
            or ""
        )
        s["profile_photo"] = photo
        s["avatar"] = photo
        s["user_photo"] = photo

        # 3. Resolve Registration Reference (CRG-xxxxx) & Phone
        reg_ref = (
            r_info.get("registration_reference")
            or r_info.get("registration_number")
            or s.get("registration_reference")
            or s.get("registration_number")
            or ""
        )
        s["registration_number"] = reg_ref
        s["registration_reference"] = reg_ref
        s["reg_no"] = reg_ref

        phone = (
            r_info.get("user_phone")
            or u_info.get("phone_number")
            or u_info.get("phone")
            or s.get("user_phone")
            or ""
        )
        s["user_phone"] = phone
        s["phone"] = phone

        # 4. Resolve submission_url and media_url
        sub_url = extract_submission_url(s)
        s["submission_url"] = sub_url
        s["media_url"] = sub_url

        # Format title and description
        s["title"] = s.get("title") or r_info.get("entry_title") or s.get("submission_title") or "Contestant Entry"
        s["description"] = s.get("description") or ""

        # Status & Scores
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

    sub_query = {
        "$or": [{"competition_id": str(comp_id)}],
        "status": {"$in": [SubmissionStatus.SUBMITTED.value, SubmissionStatus.APPROVED.value, "Scored"]}
    }
    if ObjectId.is_valid(comp_id):
        sub_query["$or"].append({"competition_id": ObjectId(comp_id)})

    total_subs = await competition_submission_collection.count_documents(sub_query)
    scored = await judge_score_collection.count_documents({
        "competition_id": str(comp_id), "judge_email": judge["email"]
    })
    skipped = await coi_skip_collection.count_documents({
        "competition_id": str(comp_id), "judge_email": judge["email"]
    })
    drafts = await draft_score_collection.count_documents({
        "competition_id": str(comp_id), "judge_email": judge["email"]
    })

    return {
        "total_contestants": total_subs,
        "scored": scored,
        "skipped_coi": skipped,
        "drafts_in_progress": drafts,
        "pending": max(0, total_subs - scored - skipped),
        "completion_pct": round((scored + skipped) / total_subs * 100, 1) if total_subs > 0 else 0
    }
