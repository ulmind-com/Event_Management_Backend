from fastapi import APIRouter, HTTPException, Depends
from bson import ObjectId
from app.core.database import judge_feedback_collection, competition_collection, leaderboard_result_collection
from bson import ObjectId

router = APIRouter()

@router.get("/competitions/{comp_id}/results")
async def public_results(comp_id: str):
    """Public API: Returns official leaderboard ONLY if results are published."""
    comp = await competition_collection.find_one({"_id": ObjectId(comp_id)})
    if not comp:
        raise HTTPException(status_code=404, detail="Competition not found")
    if comp.get("result_status") != "PUBLISHED":
        raise HTTPException(status_code=403, detail="Results have not been published yet.")
        
    from app.core.database import db
    result_snapshots_collection = db["result_snapshots"]
    snapshot = await result_snapshots_collection.find_one({"competition_id": comp_id, "is_published": True})
    
    if not snapshot:
        raise HTTPException(status_code=404, detail="Official result snapshot not found.")
        
    snapshot["_id"] = str(snapshot["_id"])
    return snapshot

@router.get("/competitions/{comp_id}/contestants/{submission_id}/feedbacks")
async def public_feedbacks(comp_id: str, submission_id: str):
    """Public API: Returns judge feedbacks for a contestant ONLY after results are published."""
    comp = await competition_collection.find_one({"_id": ObjectId(comp_id)})
    if not comp:
        raise HTTPException(status_code=404, detail="Competition not found")
    if not comp.get("results_published"):
        raise HTTPException(status_code=403, detail="Feedbacks available after results are published.")
    
    feedbacks = await judge_feedback_collection.find({
        "competition_id": comp_id, "submission_id": submission_id
    }).to_list(length=100)
    for f in feedbacks:
        f["_id"] = str(f["_id"])
    return feedbacks

from app.api.deps import get_current_user
from app.core.database import (
    audience_vote_collection,
    competition_submission_collection,
    competition_registration_collection
)
from app.schemas.competition import VoteReq
from datetime import datetime, timezone

@router.get("/users/me/home/live-voting")
async def get_live_voting(user: dict = Depends(get_current_user)):
    """Returns competitions where audience voting is enabled and currently OPEN."""
    comps = await competition_collection.find({
        "audience_voting_enabled": True,
        "voting_status": "OPEN",
        "is_published": True
    }).to_list(length=50)
    
    res = []
    for c in comps:
        res.append({
            "competition_id": str(c["_id"]),
            "title": c.get("title"),
            "image_url": c.get("image_url"),
            "voting_status": c.get("voting_status"),
            "audience_voting_enabled": True
        })
    return res

@router.post("/competitions/{comp_id}/vote")
async def submit_audience_vote(comp_id: str, req: VoteReq, user: dict = Depends(get_current_user)):
    """Allows an authenticated user to submit a vote if voting is OPEN."""
    comp = None
    if ObjectId.is_valid(comp_id):
        comp = await competition_collection.find_one({"_id": ObjectId(comp_id)})
    if not comp:
        comp = await competition_collection.find_one({"_id": comp_id})
    if not comp:
        raise HTTPException(status_code=404, detail="Competition not found")
    
    if not comp.get("audience_voting_enabled"):
        raise HTTPException(status_code=400, detail="Audience voting is not enabled for this competition.")
        
    if comp.get("voting_status", "OPEN") != "OPEN":
        raise HTTPException(status_code=400, detail="Voting is currently closed.")

    comp_id_str = str(comp["_id"])
    comp_id_match = [comp_id_str, comp_id]
    if ObjectId.is_valid(comp_id_str):
        comp_id_match.append(ObjectId(comp_id_str))

    voter_email = (user.get("email") or "").strip().lower()
    if not voter_email:
        raise HTTPException(status_code=401, detail="Voter email not found in session.")
    voter_user_id = str(user.get("_id") or "").strip()

    req_participant_id = (req.participant_id or "").strip()
    if not req_participant_id:
        raise HTTPException(status_code=400, detail="Participant ID is required.")

    # Look up target contestant / submission
    sub_conditions = [
        {"user_email": req_participant_id},
        {"_id": req_participant_id}
    ]
    if ObjectId.is_valid(req_participant_id):
        sub_conditions.append({"_id": ObjectId(req_participant_id)})

    submission = await competition_submission_collection.find_one({
        "competition_id": {"$in": comp_id_match}, 
        "$or": sub_conditions,
        "status": {"$in": ["APPROVED", "SUBMITTED", "PENDING", "COMPLETED"]}
    })

    registration = None
    if not submission:
        reg_conditions = [
            {"user_email": req_participant_id},
            {"email": req_participant_id},
            {"_id": req_participant_id}
        ]
        if ObjectId.is_valid(req_participant_id):
            reg_conditions.append({"_id": ObjectId(req_participant_id)})

        registration = await competition_registration_collection.find_one({
            "competition_id": {"$in": comp_id_match},
            "$or": reg_conditions
        })

    if not submission and not registration:
        raise HTTPException(status_code=400, detail="Participant is not an eligible contestant.")

    # Target contestant info
    target_email = ""
    target_user_id = ""
    if submission:
        target_email = (submission.get("user_email") or "").strip().lower()
        target_user_id = str(submission.get("user_id") or "").strip()
    elif registration:
        target_email = (registration.get("user_email") or registration.get("email") or "").strip().lower()
        target_user_id = str(registration.get("user_id") or "").strip()

    # ── Rule 1: Self-Voting Prevention ──
    # Contestants are strictly forbidden from voting for their own submission
    is_self_vote = False
    if target_email and target_email == voter_email:
        is_self_vote = True
    elif req_participant_id.lower() == voter_email:
        is_self_vote = True
    elif target_user_id and voter_user_id and target_user_id == voter_user_id:
        is_self_vote = True

    if is_self_vote:
        raise HTTPException(
            status_code=400,
            detail="Contestants cannot vote for their own submission."
        )

    # ── Rule 2: Competition Participant Prevention ──
    # A registered participant in this competition cannot cast audience votes
    voter_has_sub = await competition_submission_collection.find_one({
        "competition_id": {"$in": comp_id_match},
        "user_email": voter_email
    })

    voter_has_reg = None
    if not voter_has_sub:
        voter_has_reg = await competition_registration_collection.find_one({
            "competition_id": {"$in": comp_id_match},
            "$or": [
                {"user_email": voter_email},
                {"email": voter_email}
            ]
        })

    if not voter_has_sub and not voter_has_reg:
        from app.core.database import registration_collection
        voter_has_legacy = await registration_collection.find_one({
            "activity_id": {"$in": comp_id_match},
            "email": voter_email,
            "activity_type": "competition"
        })
        if voter_has_legacy:
            voter_has_reg = voter_has_legacy

    if voter_has_sub or voter_has_reg:
        raise HTTPException(
            status_code=400,
            detail="Participants of this competition are not eligible to vote. Audience voting is restricted to audience members and fans."
        )

    # ── Rule 3: Prevent duplicate voting (1 vote per user per contestant) ──
    canonical_pid = target_email or req_participant_id
    sub_id_str = str(submission["_id"]) if submission else None

    dup_filters = [
        {"participant_id": req_participant_id},
        {"participant_id": canonical_pid}
    ]
    if sub_id_str:
        dup_filters.append({"participant_id": sub_id_str})
        dup_filters.append({"submission_id": sub_id_str})

    existing_vote = await audience_vote_collection.find_one({
        "competition_id": {"$in": comp_id_match},
        "$or": dup_filters,
        "voter_email": user["email"]
    })
    if existing_vote:
        raise HTTPException(status_code=400, detail="You have already voted for this contestant.")
        
    from app.core.database import website_settings_collection
    v_settings = await website_settings_collection.find_one({"_id": "voting_settings"}) or {}
    
    if getattr(req, "is_paid_pack", False):
        if not req.payment_reference:
            raise HTTPException(status_code=400, detail="Payment reference is required for paid vote packs.")
        voting_power = v_settings.get("voting_power_paid", 10)
    elif getattr(req, "source", "WEBSITE") == "MOBILE_APP":
        voting_power = v_settings.get("voting_power_mobile", 5)
    else:
        voting_power = v_settings.get("voting_power_website", 1)

    vote_doc = {
        "competition_id": comp_id_str,
        "participant_id": canonical_pid,
        "submission_id": sub_id_str,
        "voter_email": user["email"],
        "source": getattr(req, "source", "WEBSITE"),
        "is_paid_pack": getattr(req, "is_paid_pack", False),
        "voting_power": voting_power,
        "created_at": datetime.now(timezone.utc)
    }
    await audience_vote_collection.insert_one(vote_doc)
    
    from app.core.websocket import ws_manager
    await ws_manager.broadcast_to_competition(comp_id_str, {"type": "LEADERBOARD_UPDATED"})
    
    return {"message": "Vote submitted successfully"}

@router.get("/competitions/{comp_id}/leaderboard")
async def provisional_leaderboard(comp_id: str):
    """Returns the real-time provisional leaderboard calculated by the ranking service."""
    from app.services.ranking_service import RankingService
    from app.core.database import (
        judge_score_collection,
        competition_registration_collection,
        user_collection,
        leaderboard_result_collection,
        db
    )

    comp = None
    if ObjectId.is_valid(comp_id):
        comp = await competition_collection.find_one({"_id": ObjectId(comp_id)})
    if not comp:
        comp = await competition_collection.find_one({"_id": comp_id})
    if not comp:
        raise HTTPException(status_code=404, detail="Competition not found")

    # If officially published snapshot exists, return it
    if comp.get("result_status") == "PUBLISHED" or comp.get("results_published"):
        snapshot = await db["result_snapshots"].find_one({"competition_id": comp_id, "is_published": True})
        if snapshot and snapshot.get("leaderboard"):
            return {
                "ranking_mode": snapshot.get("ranking_mode", "FINAL"),
                "is_published": True,
                "leaderboard": snapshot["leaderboard"]
            }

    # Gather all participant candidates from submissions, registrations, and existing leaderboard docs
    submissions = await competition_submission_collection.find({
        "competition_id": comp_id,
        "status": {"$in": ["APPROVED", "SUBMITTED", "PENDING", "COMPLETED"]}
    }).to_list(length=5000)

    registrations = await competition_registration_collection.find({
        "competition_id": comp_id
    }).to_list(length=5000)

    # Build unique mapping of participants
    participants_map = {}
    for sub in submissions:
        email = sub.get("user_email")
        if email:
            participants_map[email] = {
                "user_email": email,
                "submission_id": str(sub["_id"]),
                "sub_name": sub.get("name"),
            }

    for reg in registrations:
        email = reg.get("user_email") or reg.get("email")
        if email and email not in participants_map:
            participants_map[email] = {
                "user_email": email,
                "submission_id": str(reg.get("submission_id") or ""),
                "sub_name": reg.get("name") or reg.get("participant_name"),
            }

    # Also check if leaderboard_result_collection already has ranked entries
    lb_docs = await leaderboard_result_collection.find({"competition_id": comp_id}).to_list(length=1000)
    for lb in lb_docs:
        email = lb.get("email") or lb.get("participant_id") or lb.get("user_email")
        if email and email not in participants_map:
            participants_map[email] = {
                "user_email": email,
                "submission_id": str(lb.get("submission_id") or ""),
                "sub_name": lb.get("name"),
            }

    max_judge_score = sum(crit.get("max_score", 0) for crit in comp.get("judging_criteria", []))
    participants_data = []
    max_audience_votes = 0

    for email, p_info in participants_map.items():
        sub_id = p_info.get("submission_id") or ""

        # Aggregate audience votes by participant_id or sub_id
        vote_match = [{"participant_id": email}]
        if sub_id:
            vote_match.append({"participant_id": sub_id})
            vote_match.append({"submission_id": sub_id})
        pipeline = [
            {"$match": {"competition_id": {"$in": [comp_id, str(comp_id)]}, "$or": vote_match}},
            {"$group": {"_id": None, "total": {"$sum": "$voting_power"}}}
        ]
        agg_res = await audience_vote_collection.aggregate(pipeline).to_list(1)
        votes = int(agg_res[0]["total"]) if agg_res else 0
        if votes > max_audience_votes:
            max_audience_votes = votes

        # Calculate average judge score (matches submission_id, participant_id, or user_email)
        score_match = [{"participant_id": email}, {"user_email": email}]
        if sub_id:
            score_match.append({"submission_id": sub_id})
        scores = await judge_score_collection.find({
            "competition_id": comp_id,
            "$or": score_match
        }).to_list(length=100)

        total_judge = 0.0
        if scores:
            score_sums = []
            for s in scores:
                if s.get("total_score") is not None and s.get("total_score") > 0:
                    score_sums.append(float(s["total_score"]))
                elif s.get("scores") and isinstance(s["scores"], dict):
                    score_sums.append(float(sum(s["scores"].values())))
            if score_sums:
                total_judge = sum(score_sums) / len(score_sums)

        # Fallback to score saved in leaderboard_result_collection if judge_scores empty
        if total_judge == 0.0:
            lb_match = [{"participant_id": email}, {"email": email}]
            if sub_id:
                lb_match.append({"submission_id": sub_id})
            existing_lb = await leaderboard_result_collection.find_one({
                "competition_id": comp_id,
                "$or": lb_match
            })
            if existing_lb:
                total_judge = float(existing_lb.get("total_score") or existing_lb.get("score") or 0.0)

        # Look up user name and avatar
        user_doc = await user_collection.find_one({"email": email}) or {}
        display_name = (
            user_doc.get("full_name")
            or user_doc.get("name")
            or p_info.get("sub_name")
            or email.split("@")[0].replace(".", " ").title()
        )
        avatar = user_doc.get("profile_photo") or ""

        participants_data.append({
            "participant_id": email,
            "email": email,
            "name": display_name,
            "profile_photo": avatar,
            "audience_votes": votes,
            "judge_score": total_judge
        })

    final_leaderboard = RankingService.calculate_leaderboard(participants_data, max_audience_votes, max_judge_score, comp)
    return {
        "ranking_mode": final_leaderboard[0]["ranking_mode"] if final_leaderboard else "UNKNOWN",
        "leaderboard": final_leaderboard
    }
