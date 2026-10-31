"""
app/services/competition_stage_service.py
=========================================
Authoritative Production-Grade Service for Live Competition Stage Performance Management.

Features:
1. Live Stage Performer Tracking:
   - Admin sets who is currently performing on stage from the registered participants list.
   - Transition to next performer when current finishes.
   - Clear stage / intermission state.
2. Visibility for All:
   - Public / Competitors / Audience can view who is on stage in real-time.
   - Real-time WebSocket broadcasts on stage performer updates.
3. Judge Panel Integration:
   - Dedicated active stage performer card with participant name, registration reference, and photo.
   - Immediate one-click scoring access to criteria and direct score submission for judges.
   - Ensures valid scorable submission exists in `competition_submission_collection`.
"""

import re
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Tuple
from bson import ObjectId
from fastapi import HTTPException

from app.core.database import (
    competition_collection,
    competition_registration_collection,
    competition_submission_collection,
    judge_score_collection,
    draft_score_collection,
    judge_assignment_collection,
    user_collection,
    competition_stage_collection,
)
from app.core.websocket import ws_manager
from app.services.competition_lifecycle import safe_object_id

logger = logging.getLogger(__name__)


class CompetitionStageService:

    @staticmethod
    def _resolve_comp_query(comp_id: str) -> Dict[str, Any]:
        """Builds MongoDB query supporting both ObjectId and string IDs."""
        cid = str(comp_id).strip()
        or_list: List[Dict[str, Any]] = [{"_id": cid}, {"id": cid}]
        if ObjectId.is_valid(cid):
            or_list.insert(0, {"_id": ObjectId(cid)})
        return {"$or": or_list}

    @classmethod
    async def get_competition_or_404(cls, comp_id: str) -> Dict[str, Any]:
        """Retrieves competition document or raises 404."""
        query = cls._resolve_comp_query(comp_id)
        comp = await competition_collection.find_one(query)
        if not comp:
            raise HTTPException(status_code=404, detail="Competition not found.")
        return comp

    @classmethod
    async def get_or_create_submission(
        cls,
        comp_id: str,
        user_email: str,
        user_name: str,
        reg_id: Optional[str] = None
    ) -> str:
        """
        Ensures a valid scorable submission exists in `competition_submission_collection`.
        If the contestant has already submitted online, returns their submission_id.
        If they perform live on stage without a prior file, creates an approved live-stage submission.
        """
        c_str = str(comp_id).strip()
        clean_email = (user_email or "").strip().lower()

        sub_query: Dict[str, Any] = {
            "competition_id": {"$in": [c_str, ObjectId(c_str)] if ObjectId.is_valid(c_str) else [c_str]},
            "user_email": {"$regex": f"^{re.escape(clean_email)}$", "$options": "i"}
        }

        existing = await competition_submission_collection.find_one(sub_query)
        if existing:
            return str(existing["_id"])

        # Auto-create approved live-stage submission
        doc = {
            "competition_id": c_str,
            "user_email": clean_email,
            "user_name": user_name or clean_email.split("@")[0],
            "registration_id": str(reg_id) if reg_id else None,
            "title": f"Live Stage Performance - {user_name or clean_email.split('@')[0]}",
            "status": "APPROVED",
            "files": {
                "type": "LIVE_STAGE",
                "media_type": "LIVE_PERFORMANCE",
                "label": "Live Stage Performance"
            },
            "is_live_stage": True,
            "submitted_at": datetime.now(timezone.utc),
            "created_at": datetime.now(timezone.utc)
        }
        res = await competition_submission_collection.insert_one(doc)
        return str(res.inserted_id)

    @classmethod
    async def get_stage_state(cls, comp_id: str) -> Dict[str, Any]:
        """Fetches the current live stage state for a competition."""
        comp = await cls.get_competition_or_404(comp_id)
        live_stage = comp.get("live_stage") or {}

        curr = live_stage.get("current_performer")
        prev = live_stage.get("previous_performer")

        return {
            "competition_id": str(comp["_id"]),
            "competition_title": comp.get("title", "Competition"),
            "stage_status": live_stage.get("stage_status", "IDLE"),
            "has_active_performer": bool(curr),
            "current_performer": curr,
            "previous_performer": prev,
            "updated_at": live_stage.get("updated_at"),
            "updated_by": live_stage.get("updated_by")
        }

    @classmethod
    async def get_admin_stage_dashboard(cls, comp_id: str) -> Dict[str, Any]:
        """
        Returns full live stage management data for Admin:
        - Current performer on stage
        - Previous performer
        - All confirmed registered participants with their stage status (NOT_STARTED, ON_STAGE, COMPLETED),
          registration reference, user profile info, submission id, and judging scores.
        """
        c_str = str(comp_id).strip()
        comp = await cls.get_competition_or_404(c_str)
        live_stage = comp.get("live_stage") or {}
        curr_performer = live_stage.get("current_performer")
        prev_performer = live_stage.get("previous_performer")

        # 1. Fetch all confirmed registrations for this competition
        reg_query: Dict[str, Any] = {
            "competition_id": {"$in": [c_str, ObjectId(c_str)] if ObjectId.is_valid(c_str) else [c_str]},
            "status": {"$in": ["CONFIRMED", "REGISTERED", "ATTENDED"]}
        }
        regs = await competition_registration_collection.find(reg_query).sort([
            ("stage_order", 1),
            ("registered_at", 1)
        ]).to_list(length=2000)

        # Fallback if no status: CONFIRMED found, include all registrations for testing/flexibility
        if not regs:
            regs = await competition_registration_collection.find({
                "competition_id": {"$in": [c_str, ObjectId(c_str)] if ObjectId.is_valid(c_str) else [c_str]}
            }).sort([("stage_order", 1), ("registered_at", 1)]).to_list(length=2000)

        emails = list({(r.get("user_email") or "").strip().lower() for r in regs if r.get("user_email")})

        # 2. Batch fetch user profile data
        user_map: Dict[str, Any] = {}
        if emails:
            users = await user_collection.find({"email": {"$in": emails}}).to_list(length=len(emails) * 2)
            for u in users:
                em = (u.get("email") or "").strip().lower()
                if em:
                    user_map[em] = u

        # 3. Batch fetch submissions
        sub_map: Dict[str, Any] = {}
        if emails:
            subs = await competition_submission_collection.find({
                "competition_id": {"$in": [c_str, ObjectId(c_str)] if ObjectId.is_valid(c_str) else [c_str]},
                "user_email": {"$in": emails}
            }).to_list(length=len(emails) * 2)
            for s in subs:
                em = (s.get("user_email") or "").strip().lower()
                if em:
                    sub_map[em] = s

        # 4. Batch fetch judge scores
        score_map: Dict[str, List[Any]] = {}
        scores = await judge_score_collection.find({
            "competition_id": {"$in": [c_str, ObjectId(c_str)] if ObjectId.is_valid(c_str) else [c_str]}
        }).to_list(length=5000)
        for sc in scores:
            em = (sc.get("participant_id") or sc.get("user_email") or "").strip().lower()
            sid = str(sc.get("submission_id", ""))
            if em:
                score_map.setdefault(em, []).append(sc)
            if sid:
                score_map.setdefault(sid, []).append(sc)

        participants_list: List[Dict[str, Any]] = []
        completed_count = 0
        current_email = (curr_performer.get("user_email") or "").lower().strip() if curr_performer else ""

        for idx, r in enumerate(regs):
            r_id = str(r["_id"])
            em = (r.get("user_email") or "").strip().lower()
            usr = user_map.get(em, {})
            sub = sub_map.get(em, {})
            user_scores = score_map.get(em, []) or (score_map.get(str(sub.get("_id")), []) if sub else [])

            name = (
                r.get("user_name")
                or usr.get("full_name")
                or usr.get("name")
                or r.get("participant_name")
                or (em.split("@")[0] if em else "Participant")
            )
            photo = (
                usr.get("profile_photo")
                or usr.get("photo_url")
                or usr.get("avatar")
                or r.get("profile_photo")
                or ""
            )
            phone = usr.get("phone_number") or usr.get("phone") or r.get("user_phone") or ""
            reg_ref = r.get("registration_reference") or f"CRG-{r_id[:8]}"

            # Determine stage status
            stage_stat = r.get("stage_status", "NOT_STARTED")
            is_curr = False
            if current_email and em == current_email:
                stage_stat = "ON_STAGE"
                is_curr = True
            elif stage_stat == "ON_STAGE" and not is_curr:
                # If DB had ON_STAGE but live_stage current_performer changed
                stage_stat = "COMPLETED"

            if stage_stat == "COMPLETED":
                completed_count += 1

            total_score_sum = sum(float(sc.get("total_score", 0.0)) for sc in user_scores)
            avg_score = round(total_score_sum / len(user_scores), 2) if user_scores else 0.0

            participants_list.append({
                "registration_id": r_id,
                "registration_reference": reg_ref,
                "user_email": em,
                "user_name": name,
                "full_name": name,
                "user_phone": phone,
                "user_photo": photo,
                "stage_order": r.get("stage_order", idx + 1),
                "stage_status": stage_stat,
                "is_current_performer": is_curr,
                "has_submission": bool(sub),
                "submission_id": str(sub["_id"]) if sub else None,
                "entry_title": r.get("entry_title") or sub.get("title") or f"Entry - {name}",
                "is_scored": len(user_scores) > 0,
                "judge_score_count": len(user_scores),
                "average_score": avg_score,
                "total_score": total_score_sum,
                "registered_at": r.get("registered_at")
            })

        return {
            "competition_id": c_str,
            "competition_title": comp.get("title", "Competition"),
            "stage_status": live_stage.get("stage_status", "IDLE"),
            "has_active_performer": bool(curr_performer),
            "current_performer": curr_performer,
            "previous_performer": prev_performer,
            "total_participants": len(participants_list),
            "completed_performances": completed_count,
            "completed_count": completed_count,
            "remaining_performances": max(0, len(participants_list) - completed_count),
            "remaining_count": max(0, len(participants_list) - completed_count),
            "participants": participants_list
        }


    @classmethod
    async def set_current_performer(
        cls,
        comp_id: str,
        identifier: str,
        admin_email: str = "admin@eventora.com",
        notes: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Sets a specific participant as the active performer on stage.
        Identifier can be:
        1. Registration ObjectId string
        2. registration_reference (CRG-xxxxx)
        3. user_email
        """
        c_str = str(comp_id).strip()
        comp = await cls.get_competition_or_404(c_str)
        clean_id = str(identifier).strip()

        # Build registration lookup query
        reg_or: List[Dict[str, Any]] = [
            {"registration_reference": clean_id},
            {"user_email": clean_id.lower()},
            {"_id": clean_id}
        ]
        if ObjectId.is_valid(clean_id):
            reg_or.insert(0, {"_id": ObjectId(clean_id)})

        reg = await competition_registration_collection.find_one({
            "competition_id": {"$in": [c_str, ObjectId(c_str)] if ObjectId.is_valid(c_str) else [c_str]},
            "$or": reg_or
        })

        if not reg:
            # Fallback: check without competition_id in case format differed
            reg = await competition_registration_collection.find_one({"$or": reg_or})
            if not reg:
                raise HTTPException(status_code=404, detail="Participant registration not found for this competition.")

        reg_id = str(reg["_id"])
        user_email = (reg.get("user_email") or "").strip().lower()

        # Fetch User Profile details
        usr = await user_collection.find_one({"email": user_email}) if user_email else None
        user_name = (
            reg.get("user_name")
            or (usr.get("full_name") if usr else None)
            or (usr.get("name") if usr else None)
            or reg.get("participant_name")
            or (user_email.split("@")[0] if user_email else "Participant")
        )
        user_photo = (
            (usr.get("profile_photo") if usr else None)
            or (usr.get("photo_url") if usr else None)
            or (usr.get("avatar") if usr else None)
            or reg.get("profile_photo")
            or ""
        )
        user_phone = (
            (usr.get("phone_number") if usr else None)
            or (usr.get("phone") if usr else None)
            or reg.get("user_phone")
            or ""
        )
        reg_ref = reg.get("registration_reference") or f"CRG-{reg_id[:8]}"

        # Ensure submission exists so judges can score
        submission_id = await cls.get_or_create_submission(
            comp_id=c_str,
            user_email=user_email,
            user_name=user_name,
            reg_id=reg_id
        )

        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()

        # Handle previous performer transition
        live_stage = comp.get("live_stage") or {}
        prev_performer = live_stage.get("current_performer")
        if prev_performer:
            prev_reg_id = prev_performer.get("registration_id")
            prev_email = prev_performer.get("user_email")
            prev_query: List[Dict[str, Any]] = []
            if prev_reg_id and ObjectId.is_valid(prev_reg_id):
                prev_query.append({"_id": ObjectId(prev_reg_id)})
            elif prev_reg_id:
                prev_query.append({"_id": prev_reg_id})
            if prev_email:
                prev_query.append({"user_email": prev_email})

            if prev_query:
                await competition_registration_collection.update_one(
                    {"$or": prev_query},
                    {"$set": {
                        "stage_status": "COMPLETED",
                        "stage_completed_at": now
                    }}
                )
            prev_performer["stage_status"] = "COMPLETED"
            prev_performer["completed_at"] = now_iso

        # Build new performer details
        new_performer = {
            "registration_id": reg_id,
            "registration_reference": reg_ref,
            "user_email": user_email,
            "user_name": user_name,
            "full_name": user_name,
            "user_phone": user_phone,
            "user_photo": user_photo,
            "avatar": user_photo,
            "submission_id": submission_id,
            "entry_title": reg.get("entry_title") or f"Stage Performance - {user_name}",
            "stage_status": "ON_STAGE",
            "started_at": now_iso,
            "notes": notes
        }

        # Update current registration in DB
        await competition_registration_collection.update_one(
            {"_id": reg["_id"]},
            {"$set": {
                "stage_status": "ON_STAGE",
                "stage_started_at": now
            }}
        )

        # Update competition live_stage
        stage_update = {
            "stage_status": "LIVE",
            "current_performer": new_performer,
            "previous_performer": prev_performer,
            "updated_at": now_iso,
            "updated_by": admin_email
        }
        await competition_collection.update_one(
            cls._resolve_comp_query(c_str),
            {"$set": {"live_stage": stage_update}}
        )

        # Log to stage history
        try:
            await competition_stage_collection.insert_one({
                "competition_id": c_str,
                "action": "SET_CURRENT_PERFORMER",
                "performer": new_performer,
                "previous_performer": prev_performer,
                "admin_email": admin_email,
                "timestamp": now
            })
        except Exception as e:
            logger.warning(f"Failed to log stage session history: {e}")

        # Real-time WebSocket Broadcast
        await ws_manager.broadcast_to_competition(c_str, {
            "event": "stage_performer_changed",
            "competition_id": c_str,
            "stage_status": "LIVE",
            "current_performer": new_performer,
            "previous_performer": prev_performer,
            "timestamp": now_iso
        })

        return {
            "message": f"{user_name} is now performing on stage.",
            "competition_id": c_str,
            "stage_status": "LIVE",
            "current_performer": new_performer,
            "previous_performer": prev_performer
        }

    @classmethod
    async def advance_to_next_performer(
        cls,
        comp_id: str,
        admin_email: str = "admin@eventora.com"
    ) -> Dict[str, Any]:
        """
        Advances the stage to the next registered contestant who has not performed yet.
        """
        c_str = str(comp_id).strip()
        comp = await cls.get_competition_or_404(c_str)
        live_stage = comp.get("live_stage") or {}
        curr_performer = live_stage.get("current_performer")
        current_email = (curr_performer.get("user_email") or "").lower().strip() if curr_performer else ""

        # Find next unperformed contestant
        query: Dict[str, Any] = {
            "competition_id": {"$in": [c_str, ObjectId(c_str)] if ObjectId.is_valid(c_str) else [c_str]},
            "stage_status": {"$nin": ["COMPLETED", "ON_STAGE", "SKIPPED"]}
        }
        if current_email:
            query["user_email"] = {"$ne": current_email}

        next_reg = await competition_registration_collection.find_one(
            query,
            sort=[("stage_order", 1), ("registered_at", 1)]
        )

        if not next_reg:
            # If all are completed or on stage
            if curr_performer:
                # Mark current as completed and set stage to IDLE / COMPLETED
                return await cls.end_current_performance(c_str, admin_email=admin_email)
            return {
                "message": "All registered participants have completed their stage performances.",
                "competition_id": c_str,
                "stage_status": "COMPLETED",
                "current_performer": None
            }

        return await cls.set_current_performer(
            comp_id=c_str,
            identifier=str(next_reg["_id"]),
            admin_email=admin_email
        )

    @classmethod
    async def end_current_performance(
        cls,
        comp_id: str,
        admin_email: str = "admin@eventora.com"
    ) -> Dict[str, Any]:
        """
        Ends the current on-stage performance.
        Marks the performer as COMPLETED and sets stage to IDLE (intermission / waiting for next).
        """
        c_str = str(comp_id).strip()
        comp = await cls.get_competition_or_404(c_str)
        live_stage = comp.get("live_stage") or {}
        curr_performer = live_stage.get("current_performer")

        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()

        if curr_performer:
            prev_reg_id = curr_performer.get("registration_id")
            prev_email = curr_performer.get("user_email")
            prev_query: List[Dict[str, Any]] = []
            if prev_reg_id and ObjectId.is_valid(prev_reg_id):
                prev_query.append({"_id": ObjectId(prev_reg_id)})
            elif prev_reg_id:
                prev_query.append({"_id": prev_reg_id})
            if prev_email:
                prev_query.append({"user_email": prev_email})

            if prev_query:
                await competition_registration_collection.update_one(
                    {"$or": prev_query},
                    {"$set": {
                        "stage_status": "COMPLETED",
                        "stage_completed_at": now
                    }}
                )
            curr_performer["stage_status"] = "COMPLETED"
            curr_performer["completed_at"] = now_iso

        stage_update = {
            "stage_status": "IDLE",
            "current_performer": None,
            "previous_performer": curr_performer,
            "updated_at": now_iso,
            "updated_by": admin_email
        }

        await competition_collection.update_one(
            cls._resolve_comp_query(c_str),
            {"$set": {"live_stage": stage_update}}
        )

        try:
            await competition_stage_collection.insert_one({
                "competition_id": c_str,
                "action": "END_PERFORMANCE",
                "previous_performer": curr_performer,
                "admin_email": admin_email,
                "timestamp": now
            })
        except Exception:
            pass

        # Broadcast stage cleared
        await ws_manager.broadcast_to_competition(c_str, {
            "event": "stage_performer_cleared",
            "competition_id": c_str,
            "stage_status": "IDLE",
            "current_performer": None,
            "previous_performer": curr_performer,
            "timestamp": now_iso
        })

        return {
            "message": "Current stage performance ended. Stage is now clear/idle.",
            "competition_id": c_str,
            "stage_status": "IDLE",
            "current_performer": None,
            "previous_performer": curr_performer
        }

    @classmethod
    async def get_judge_stage_card(
        cls,
        comp_id: str,
        judge_email: str
    ) -> Dict[str, Any]:
        """
        Dedicated Judge API:
        Returns the active on-stage performer card with participant details,
        scoring criteria, submission_id for 1-click scoring modal,
        and current judge's scoring status (already scored vs pending).
        """
        c_str = str(comp_id).strip()
        comp = await cls.get_competition_or_404(c_str)
        clean_judge = (judge_email or "").strip().lower()

        live_stage = comp.get("live_stage") or {}
        curr = live_stage.get("current_performer")
        criteria = comp.get("judging_criteria", [])

        if not curr:
            return {
                "competition_id": c_str,
                "competition_title": comp.get("title", "Competition"),
                "stage_status": live_stage.get("stage_status", "IDLE"),
                "has_active_performer": False,
                "message": "No participant is currently performing on stage.",
                "performer": None,
                "criteria": criteria
            }

        sub_id = curr.get("submission_id")
        user_email = (curr.get("user_email") or "").strip().lower()

        # Check if this judge has already scored this contestant
        score_query: Dict[str, Any] = {
            "competition_id": {"$in": [c_str, ObjectId(c_str)] if ObjectId.is_valid(c_str) else [c_str]},
            "judge_email": {"$regex": f"^{re.escape(clean_judge)}$", "$options": "i"},
            "$or": []
        }
        if sub_id:
            score_query["$or"].append({"submission_id": str(sub_id)})
            if ObjectId.is_valid(sub_id):
                score_query["$or"].append({"submission_id": ObjectId(sub_id)})
        if user_email:
            score_query["$or"].extend([
                {"participant_id": user_email},
                {"user_email": user_email}
            ])

        existing_score = None
        if score_query["$or"]:
            existing_score = await judge_score_collection.find_one(score_query)

        # Check if draft exists
        draft = None
        if not existing_score and sub_id:
            draft = await draft_score_collection.find_one({
                "competition_id": c_str,
                "submission_id": str(sub_id),
                "judge_email": clean_judge
            })

        is_scored = bool(existing_score)
        scored_at = existing_score.get("scored_at") if existing_score else None
        if hasattr(scored_at, "isoformat"):
            scored_at = scored_at.isoformat()

        card_data = {
            "submission_id": sub_id,
            "registration_id": curr.get("registration_id"),
            "registration_reference": curr.get("registration_reference"),
            "participant_name": curr.get("user_name"),
            "user_name": curr.get("user_name"),
            "user_email": user_email,
            "user_photo": curr.get("user_photo"),
            "user_phone": curr.get("user_phone"),
            "entry_title": curr.get("entry_title"),
            "started_at": curr.get("started_at"),
            "stage_status": curr.get("stage_status", "ON_STAGE"),
            "is_scored_by_me": is_scored,
            "has_scored": is_scored,
            "is_locked": is_scored,
            "my_total_score": existing_score.get("total_score") if existing_score else None,
            "my_scores": existing_score.get("scores") if existing_score else (draft.get("scores", {}) if draft else {}),
            "my_comments": existing_score.get("comments") if existing_score else (draft.get("comments") if draft else None),
            "scored_at": scored_at,
            "judging_criteria": criteria,
            "can_score": not is_scored,
            "scoring_endpoint": f"/api/judge/submissions/{sub_id}/scores" if sub_id else None
        }

        my_status = {
            "has_scored": is_scored,
            "is_scored": is_scored,
            "is_locked": is_scored,
            "total_score": existing_score.get("total_score") if existing_score else None,
            "scores": existing_score.get("scores") if existing_score else (draft.get("scores", {}) if draft else {}),
            "comments": existing_score.get("comments") if existing_score else (draft.get("comments") if draft else None),
            "scored_at": scored_at
        }

        return {
            "competition_id": c_str,
            "competition_title": comp.get("title", "Competition"),
            "stage_status": live_stage.get("stage_status", "LIVE"),
            "has_active_performer": True,
            "has_current_performer": True,
            "current_performer": card_data,
            "performer": card_data,
            "criteria": criteria,
            "judging_criteria": criteria,
            "can_score": not is_scored,
            "my_scoring_status": my_status
        }

    @classmethod
    async def get_public_stage_status(cls, comp_id: str) -> Dict[str, Any]:
        """
        Safe public live stage representation for all users, competitors, and audience.
        Sensitive private info (phone numbers, private notes) is omitted.
        """
        c_str = str(comp_id).strip()
        comp = await cls.get_competition_or_404(c_str)
        live_stage = comp.get("live_stage") or {}
        curr = live_stage.get("current_performer")
        prev = live_stage.get("previous_performer")

        public_curr = None
        if curr:
            public_curr = {
                "participant_name": curr.get("user_name"),
                "user_name": curr.get("user_name"),
                "registration_reference": curr.get("registration_reference"),
                "user_photo": curr.get("user_photo"),
                "avatar": curr.get("user_photo"),
                "entry_title": curr.get("entry_title"),
                "started_at": curr.get("started_at"),
                "stage_status": curr.get("stage_status", "ON_STAGE")
            }

        public_prev = None
        if prev:
            public_prev = {
                "participant_name": prev.get("user_name"),
                "user_name": prev.get("user_name"),
                "registration_reference": prev.get("registration_reference"),
                "user_photo": prev.get("user_photo"),
                "entry_title": prev.get("entry_title"),
                "completed_at": prev.get("completed_at")
            }

        return {
            "competition_id": c_str,
            "competition_title": comp.get("title", "Competition"),
            "stage_status": live_stage.get("stage_status", "IDLE"),
            "has_active_performer": bool(public_curr),
            "current_performer": public_curr,
            "previous_performer": public_prev,
            "updated_at": live_stage.get("updated_at")
        }


    @classmethod
    async def update_stage_orders(
        cls,
        comp_id: str,
        orders: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Allows admin to specify performance sequence order for registered participants.
        """
        c_str = str(comp_id).strip()
        await cls.get_competition_or_404(c_str)

        updated_count = 0
        for item in orders:
            reg_id = item.get("registration_id")
            order_num = item.get("stage_order")
            if reg_id and order_num is not None:
                q: Dict[str, Any] = {"_id": ObjectId(reg_id) if ObjectId.is_valid(reg_id) else reg_id}
                res = await competition_registration_collection.update_one(
                    q,
                    {"$set": {"stage_order": int(order_num)}}
                )
                if res.modified_count > 0:
                    updated_count += 1

        return {
            "message": f"Successfully updated performance order for {updated_count} contestants.",
            "competition_id": c_str
        }
