from fastapi import APIRouter, HTTPException, Depends, Query
from typing import Optional, List, Dict, Any
from bson import ObjectId
from app.core.database import judge_feedback_collection, competition_collection, leaderboard_result_collection
from app.services.competition_stage_service import CompetitionStageService

router = APIRouter()

@router.get("/competitions/{comp_id}/live-stage")
@router.get("/competitions/{comp_id}/current-performer")
async def get_public_competition_live_stage(comp_id: str):
    """
    Public API: Returns the current live stage performance status for a competition.
    Visible to all audiences, competitors, judges, and viewers in real-time.
    """
    return await CompetitionStageService.get_public_stage_status(comp_id)


@router.get("/competitions/{comp_id}/final-participants")
@router.get("/competitions/{comp_id}/attendance-list")
async def get_public_final_present_participants(comp_id: str, category: Optional[str] = Query(None)):
    """
    Public & User API: Returns the official final lineup of PRESENT participants for an offline competition.
    Shows the confirmed contestants who are attending the venue and performing on stage.
    """
    c_str = str(comp_id).strip()
    comp = None
    if ObjectId.is_valid(c_str):
        comp = await competition_collection.find_one({"_id": ObjectId(c_str)})
    if not comp:
        comp = await competition_collection.find_one({"$or": [{"_id": c_str}, {"id": c_str}]})
    if not comp:
        raise HTTPException(status_code=404, detail="Competition not found")

    comp_matches = [c_str]
    if ObjectId.is_valid(c_str):
        comp_matches.append(ObjectId(c_str))
    if comp.get("_id"):
        comp_matches.append(comp["_id"])
        comp_matches.append(str(comp["_id"]))
    comp_matches = list({str(x): x for x in comp_matches}.values())

    # Fetch registrations
    all_regs = await competition_registration_collection.find({
        "competition_id": {"$in": comp_matches}
    }).sort([("stage_order", 1), ("registered_at", 1)]).to_list(length=2000)

    if not all_regs:
        from app.core.database import registration_collection
        all_regs = await registration_collection.find({
            "$or": [
                {"activity_id": {"$in": comp_matches}},
                {"competition_id": {"$in": comp_matches}}
            ]
        }).sort([("created_at", 1)]).to_list(length=2000)

    # Filter strictly to PRESENT participants
    present_regs = [
        r for r in all_regs
        if r.get("is_present") or r.get("attendance_status") == "PRESENT"
    ]

    # Category filter if provided
    if category and category.strip():
        c_clean = category.strip().lower()
        present_regs = [
            r for r in present_regs
            if (r.get("category") or comp.get("category") or "General").strip().lower() == c_clean
        ]

    from app.core.database import user_collection
    emails = list({(r.get("user_email") or r.get("email") or "").strip().lower() for r in present_regs if (r.get("user_email") or r.get("email"))})
    users_map = {}
    if emails:
        usrs = await user_collection.find({"email": {"$in": emails}}).to_list(length=len(emails) * 2)
        users_map = {(u.get("email") or "").strip().lower(): u for u in usrs}

    live_stage = comp.get("live_stage") or {}
    curr_performer = live_stage.get("current_performer") or {}
    curr_email = (curr_performer.get("user_email") or "").strip().lower()

    final_list = []
    for idx, r in enumerate(present_regs):
        em = (r.get("user_email") or r.get("email") or "").strip().lower()
        usr = users_map.get(em, {})

        name = (
            r.get("user_name")
            or usr.get("full_name")
            or usr.get("name")
            or r.get("participant_name")
            or (em.split("@")[0].title() if em else f"Participant #{idx+1}")
        )
        photo = usr.get("profile_photo") or usr.get("avatar") or r.get("profile_photo") or ""
        is_v = bool(
            usr.get("is_verified")
            or usr.get("is_id_verified")
            or usr.get("is_govt_id_verified")
            or usr.get("verified_badge")
            or usr.get("verified_tick")
            or usr.get("kyc_status") == "VERIFIED"
        )

        stage_stat = r.get("stage_status", "NOT_STARTED")
        is_on_stage = bool(curr_email and em == curr_email)
        if is_on_stage:
            stage_stat = "ON_STAGE"

        final_list.append({
            "registration_id": str(r["_id"]),
            "registration_reference": r.get("registration_reference") or f"CRG-{str(r['_id'])[:8]}",
            "participant_name": name,
            "user_name": name,
            "user_email": em,
            "user_photo": photo,
            "avatar": photo,
            "category": r.get("category") or comp.get("category") or "General",
            "stage_order": r.get("stage_order", idx + 1),
            "stage_status": stage_stat,
            "is_current_performer": is_on_stage,
            "is_present": True,
            "attendance_status": "PRESENT",
            "is_verified": is_v,
            "verified_badge": is_v,
            "verified_tick": is_v
        })

    return {
        "competition_id": c_str,
        "competition_title": comp.get("title", "Competition"),
        "is_offline": bool(comp.get("is_offline", False) or str(comp.get("event_mode", "")).upper() == "OFFLINE"),
        "venue": comp.get("venue"),
        "total_registered": len(all_regs),
        "present_count": len(final_list),
        "category": category,
        "final_participants": final_list,
        "final_present_list": final_list
    }


@router.get("/competitions/{comp_id}/results")
async def public_results(comp_id: str, category: Optional[str] = Query(None)):
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
    cat_lbs = snapshot.get("category_leaderboards") or {}
    all_cats = snapshot.get("categories") or list(cat_lbs.keys()) or [c.get("name") for c in comp.get("categories", []) if isinstance(c, dict) and c.get("name")]
    snapshot["categories"] = all_cats
    snapshot["category_leaderboards"] = cat_lbs
    if category and category.strip():
        c_clean = category.strip()
        if c_clean in cat_lbs:
            snapshot["leaderboard"] = cat_lbs[c_clean]
        else:
            snapshot["leaderboard"] = [
                x for x in snapshot.get("leaderboard", [])
                if (x.get("category") or "").strip().lower() == c_clean.lower()
            ]
        snapshot["category"] = c_clean
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
async def provisional_leaderboard(comp_id: str, category: Optional[str] = Query(None)):
    """Returns the real-time provisional leaderboard calculated by the ranking service, supporting category filtering and grouping."""
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
            cat_lbs = snapshot.get("category_leaderboards") or {}
            all_cats = snapshot.get("categories") or list(cat_lbs.keys()) or [c.get("name") for c in comp.get("categories", []) if isinstance(c, dict) and c.get("name")]
            target_lb = snapshot["leaderboard"]
            if category and category.strip():
                c_clean = category.strip()
                if c_clean in cat_lbs:
                    target_lb = cat_lbs[c_clean]
                else:
                    target_lb = [x for x in target_lb if (x.get("category") or "").strip().lower() == c_clean.lower()]
            return {
                "ranking_mode": snapshot.get("ranking_mode", "FINAL"),
                "is_published": True,
                "category": category,
                "categories": all_cats,
                "leaderboard": target_lb,
                "category_leaderboards": cat_lbs
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
                "category": sub.get("category"),
            }

    for reg in registrations:
        email = reg.get("user_email") or reg.get("email")
        if email:
            if email in participants_map:
                if reg.get("category"):
                    participants_map[email]["category"] = reg.get("category")
            else:
                participants_map[email] = {
                    "user_email": email,
                    "submission_id": str(reg.get("submission_id") or ""),
                    "sub_name": reg.get("name") or reg.get("participant_name"),
                    "category": reg.get("category"),
                }

    # Also check if leaderboard_result_collection already has ranked entries
    lb_docs = await leaderboard_result_collection.find({"competition_id": comp_id}).to_list(length=1000)
    for lb in lb_docs:
        email = lb.get("email") or lb.get("participant_id") or lb.get("user_email")
        if email:
            if email in participants_map:
                if lb.get("category") and not participants_map[email].get("category"):
                    participants_map[email]["category"] = lb.get("category")
            else:
                participants_map[email] = {
                    "user_email": email,
                    "submission_id": str(lb.get("submission_id") or ""),
                    "sub_name": lb.get("name"),
                    "category": lb.get("category"),
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
        is_user_verified = bool(
            user_doc.get("is_verified")
            or user_doc.get("is_id_verified")
            or user_doc.get("is_govt_id_verified")
            or user_doc.get("verified_badge")
            or user_doc.get("verified_tick")
            or user_doc.get("kyc_status") == "VERIFIED"
        )

        p_category = p_info.get("category") or comp.get("category") or "General"
        participants_data.append({
            "participant_id": email,
            "email": email,
            "name": display_name,
            "profile_photo": avatar,
            "category": p_category,
            "audience_votes": votes,
            "judge_score": total_judge,
            "is_verified": is_user_verified,
            "verified_badge": is_user_verified,
            "verified_tick": is_user_verified
        })

    final_leaderboard = RankingService.calculate_leaderboard(participants_data, max_audience_votes, max_judge_score, comp)
    category_leaderboards = RankingService.calculate_category_leaderboards(final_leaderboard)
    all_cats = [c.get("name") for c in comp.get("categories", []) if isinstance(c, dict) and c.get("name")]
    for k in category_leaderboards.keys():
        if k not in all_cats:
            all_cats.append(k)

    target_lb = final_leaderboard
    if category and category.strip():
        c_clean = category.strip()
        target_lb = category_leaderboards.get(c_clean, [
            x for x in final_leaderboard if (x.get("category") or "").strip().lower() == c_clean.lower()
        ])

    return {
        "ranking_mode": final_leaderboard[0]["ranking_mode"] if final_leaderboard else "UNKNOWN",
        "is_published": False,
        "category": category,
        "categories": all_cats,
        "leaderboard": target_lb,
        "category_leaderboards": category_leaderboards
    }
