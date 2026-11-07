"""
competition_round_service.py
============================
Authoritative Multi-Round Competition Management Service:
- Manages multi-round lifecycles (Round 1 / Prelims -> Round 2 / Semi-Finals -> Round 3 / Grand Finale).
- Independent round-specific leaderboards: calculates who is 1st, 2nd, 3rd in each specific round.
- Round advancement & qualification: advances top N (e.g. 10 out of 100) or selected contestants to the next round.
- Online & Offline integration:
  * Online: round-by-round evaluation and separate rankings.
  * Offline: live stage performance queue strictly filters to contestants qualified for the active round.
- Real-time WebSocket broadcasts on round activation and contestant advancement.
"""

from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from bson import ObjectId
import logging

from app.core.database import (
    competition_collection,
    competition_registration_collection,
    competition_submission_collection,
    judge_score_collection,
    audience_vote_collection,
    leaderboard_result_collection,
    user_collection,
    registration_collection,
    db
)
from app.services.ranking_service import RankingService
from app.core.websocket import ws_manager
from app.services.audit import log_admin_action

logger = logging.getLogger(__name__)


class CompetitionRoundService:

    @classmethod
    def _resolve_comp_query(cls, comp_id: str) -> Dict[str, Any]:
        c_str = str(comp_id).strip()
        if ObjectId.is_valid(c_str):
            return {"$or": [{"_id": ObjectId(c_str)}, {"_id": c_str}, {"id": c_str}]}
        return {"$or": [{"_id": c_str}, {"id": c_str}]}

    @classmethod
    def _get_comp_matches(cls, comp_id: str, comp_doc: Optional[dict] = None) -> List[Any]:
        c_str = str(comp_id).strip()
        matches: List[Any] = [c_str]
        if ObjectId.is_valid(c_str):
            matches.append(ObjectId(c_str))
        if comp_doc:
            if comp_doc.get("_id"):
                matches.append(comp_doc["_id"])
                matches.append(str(comp_doc["_id"]))
            if comp_doc.get("id"):
                matches.append(comp_doc["id"])
                matches.append(str(comp_doc["id"]))
        return list({str(x): x for x in matches}.values())

    @classmethod
    async def get_competition_or_404(cls, comp_id: str) -> Dict[str, Any]:
        comp = await competition_collection.find_one(cls._resolve_comp_query(comp_id))
        if not comp:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Competition not found")
        return comp

    @classmethod
    def normalize_competition_rounds(cls, comp: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Ensures a competition always has a valid list of round objects.
        If no rounds were explicitly configured, generates default Round 1.
        """
        raw_rounds = comp.get("rounds") or []
        curr_round = int(comp.get("current_round") or 1)

        normalized = []
        for idx, r in enumerate(raw_rounds):
            r_num = int(r.get("round_number") or (idx + 1))
            name = str(r.get("name") or f"Round {r_num}").strip()
            desc = r.get("description")
            adv_count = int(r.get("advancing_count")) if r.get("advancing_count") is not None else None
            is_final = bool(r.get("is_final_round") or (idx == len(raw_rounds) - 1 and len(raw_rounds) > 1))
            status = str(r.get("status") or ("ACTIVE" if r_num == curr_round else ("COMPLETED" if r_num < curr_round else "UPCOMING"))).upper()

            normalized.append({
                "round_number": r_num,
                "name": name,
                "description": desc,
                "status": status,
                "advancing_count": adv_count,
                "is_final_round": is_final,
                "start_date": r.get("start_date"),
                "end_date": r.get("end_date")
            })

        if not normalized:
            # Default single-round championship
            normalized = [{
                "round_number": 1,
                "name": "Round 1 - Preliminary",
                "description": "Initial open round for all registered contestants",
                "status": "ACTIVE",
                "advancing_count": None,
                "is_final_round": True,
                "start_date": comp.get("start_date"),
                "end_date": comp.get("end_date")
            }]

        return normalized

    @classmethod
    async def get_rounds(cls, comp_id: str) -> Dict[str, Any]:
        """
        Returns full multi-round structure and current active round status for a competition.
        """
        comp = await cls.get_competition_or_404(comp_id)
        rounds = cls.normalize_competition_rounds(comp)
        curr_round = int(comp.get("current_round") or 1)

        # Count participants per round
        c_matches = cls._get_comp_matches(comp_id, comp)
        for r in rounds:
            r_num = r["round_number"]
            if r_num == 1:
                p_count = await competition_registration_collection.count_documents({
                    "competition_id": {"$in": c_matches}
                })
            else:
                p_count = await competition_registration_collection.count_documents({
                    "competition_id": {"$in": c_matches},
                    "$or": [
                        {"qualified_rounds": r_num},
                        {"current_round": r_num},
                        {"highest_round": {"$gte": r_num}}
                    ]
                })
            r["qualified_contestant_count"] = p_count
            r["qualified_contestants_count"] = p_count

        return {
            "competition_id": str(comp["_id"]),
            "competition_title": comp.get("title", "Competition"),
            "current_round": curr_round,
            "total_rounds": len(rounds),
            "rounds": rounds
        }

    @classmethod
    async def configure_rounds(
        cls,
        comp_id: str,
        rounds_data: List[Dict[str, Any]],
        admin_email: str = "admin@upgradetalentsindia.in"
    ) -> Dict[str, Any]:
        """
        Configures the multi-round sequence for a competition (e.g. Round 1, Round 2, Round 3).
        """
        comp = await cls.get_competition_or_404(comp_id)
        c_str = str(comp["_id"])
        c_matches = cls._get_comp_matches(c_str, comp)
        rounds = cls.normalize_competition_rounds(comp)

        if not rounds_data:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="Must provide at least one round in rounds configuration.")

        cleaned_rounds = []
        for idx, r in enumerate(rounds_data):
            r_num = int(r.get("round_number") or (idx + 1))
            name = str(r.get("name") or f"Round {r_num}").strip()
            desc = r.get("description")
            adv_count = int(r.get("advancing_count")) if r.get("advancing_count") is not None else None
            is_final = bool(r.get("is_final_round") or (idx == len(rounds_data) - 1 and len(rounds_data) > 1))
            status = str(r.get("status") or ("ACTIVE" if idx == 0 else "UPCOMING")).upper()

            cleaned_rounds.append({
                "round_number": r_num,
                "name": name,
                "description": desc,
                "status": status,
                "advancing_count": adv_count,
                "is_final_round": is_final,
                "start_date": r.get("start_date"),
                "end_date": r.get("end_date")
            })

        # Ensure round 1 is active by default if none active
        has_active = any(r["status"] == "ACTIVE" for r in cleaned_rounds)
        if not has_active:
            cleaned_rounds[0]["status"] = "ACTIVE"
        active_r_num = next((r["round_number"] for r in cleaned_rounds if r["status"] == "ACTIVE"), 1)

        now = datetime.now(timezone.utc)
        await competition_collection.update_one(
            cls._resolve_comp_query(c_str),
            {"$set": {
                "rounds": cleaned_rounds,
                "current_round": active_r_num,
                "total_rounds": len(cleaned_rounds),
                "updated_at": now
            }}
        )

        # Initialize existing registered contestants with qualified_rounds = [1]
        await competition_registration_collection.update_many(
            {"competition_id": {"$in": c_matches}, "qualified_rounds": {"$exists": False}},
            {"$set": {
                "qualified_rounds": [1],
                "current_round": 1,
                "highest_round": 1,
                "round_status": "QUALIFIED"
            }}
        )

        await log_admin_action(
            admin_email,
            "configure_rounds",
            "competition",
            c_str,
            metadata={"total_rounds": len(cleaned_rounds), "current_round": active_r_num}
        )

        # Broadcast
        await ws_manager.broadcast_to_competition(c_str, {
            "event": "rounds_configured",
            "competition_id": c_str,
            "total_rounds": len(cleaned_rounds),
            "current_round": active_r_num,
            "rounds": cleaned_rounds
        })

        return {
            "message": f"Successfully configured {len(cleaned_rounds)} rounds for competition.",
            "competition_id": c_str,
            "current_round": active_r_num,
            "total_rounds": len(cleaned_rounds),
            "rounds": cleaned_rounds
        }

    @classmethod
    async def set_active_round(
        cls,
        comp_id: str,
        round_number: int,
        admin_email: str = "admin@upgradetalentsindia.in"
    ) -> Dict[str, Any]:
        """
        Switches the active competition round to round_number.
        """
        comp = await cls.get_competition_or_404(comp_id)
        c_str = str(comp["_id"])
        rounds = cls.normalize_competition_rounds(comp)

        target_r = next((r for r in rounds if r["round_number"] == round_number), None)
        if not target_r:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail=f"Round {round_number} not found in this competition.")

        for r in rounds:
            r_num = r["round_number"]
            if r_num < round_number:
                r["status"] = "COMPLETED"
            elif r_num == round_number:
                r["status"] = "ACTIVE"
            else:
                r["status"] = "UPCOMING"

        now = datetime.now(timezone.utc)
        await competition_collection.update_one(
            cls._resolve_comp_query(c_str),
            {"$set": {
                "rounds": rounds,
                "current_round": round_number,
                "updated_at": now
            }}
        )

        # Reset stage state for the new round
        stage_update = {
            "stage_status": "IDLE",
            "current_performer": None,
            "previous_performer": None,
            "current_round": round_number,
            "updated_at": now.isoformat(),
            "updated_by": admin_email
        }
        await competition_collection.update_one(
            cls._resolve_comp_query(c_str),
            {"$set": {"live_stage": stage_update}}
        )

        await log_admin_action(
            admin_email,
            "set_active_round",
            "competition",
            c_str,
            metadata={"round_number": round_number, "round_name": target_r["name"]}
        )

        await ws_manager.broadcast_to_competition(c_str, {
            "event": "active_round_changed",
            "competition_id": c_str,
            "current_round": round_number,
            "round_name": target_r["name"],
            "rounds": rounds
        })

        return {
            "message": f"Round {round_number} ({target_r['name']}) is now ACTIVE.",
            "competition_id": c_str,
            "current_round": round_number,
            "round_name": target_r["name"],
            "rounds": rounds
        }

    @classmethod
    async def get_round_leaderboard(
        cls,
        comp_id: str,
        round_number: Optional[int] = None,
        category: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Calculates the separate, independent leaderboard for a specific round:
        - Shows who came 1st, 2nd, 3rd in this round!
        - Only aggregates scores & votes belonging to this specific round.
        - Indicates who is in the advancing / qualifying zone.
        """
        comp = await cls.get_competition_or_404(comp_id)
        c_str = str(comp["_id"])
        c_matches = cls._get_comp_matches(c_str, comp)
        rounds = cls.normalize_competition_rounds(comp)

        rounds = cls.normalize_competition_rounds(comp)
        target_r_num = int(round_number) if round_number is not None else int(comp.get("current_round") or 1)

        round_cfg = next((r for r in rounds if r["round_number"] == target_r_num), None)
        if not round_cfg:
            round_cfg = {
                "round_number": target_r_num,
                "name": f"Round {target_r_num}",
                "status": "ACTIVE" if target_r_num == comp.get("current_round", 1) else "UPCOMING",
                "advancing_count": None,
                "is_final_round": (target_r_num == len(rounds))
            }

        advancing_count = round_cfg.get("advancing_count")

        # 1. Fetch participants qualified for this round
        reg_query: Dict[str, Any] = {"competition_id": {"$in": c_matches}}
        if target_r_num > 1:
            reg_query["$or"] = [
                {"qualified_rounds": target_r_num},
                {"current_round": target_r_num},
                {"highest_round": {"$gte": target_r_num}}
            ]

        regs = await competition_registration_collection.find(reg_query).to_list(length=3000)
        if not regs and target_r_num == 1:
            regs = await registration_collection.find({
                "$or": [{"activity_id": {"$in": c_matches}}, {"competition_id": {"$in": c_matches}}]
            }).to_list(length=3000)

        emails = list({(r.get("user_email") or r.get("email") or "").strip().lower() for r in regs if (r.get("user_email") or r.get("email"))})

        # Fetch submissions
        sub_query: Dict[str, Any] = {"competition_id": {"$in": c_matches}}
        if emails:
            sub_query["user_email"] = {"$in": emails}
        subs = await competition_submission_collection.find(sub_query).to_list(length=len(emails) * 2 + 10)
        sub_map = {}
        for s in subs:
            em = (s.get("user_email") or "").strip().lower()
            if em and em not in sub_map:
                sub_map[em] = s

        # Fetch users
        users_map = {}
        if emails:
            usrs = await user_collection.find({"email": {"$in": emails}}).to_list(length=len(emails) * 2)
            users_map = {(u.get("email") or "").strip().lower(): u for u in usrs}

        # 2. Fetch judge scores strictly for this round
        score_filter: Dict[str, Any] = {"competition_id": {"$in": c_matches}}
        if target_r_num == 1:
            score_filter["$or"] = [{"round_number": 1}, {"round_number": {"$exists": False}}, {"round_number": None}]
        else:
            score_filter["round_number"] = target_r_num

        round_scores = await judge_score_collection.find(score_filter).to_list(length=5000)
        scores_by_email: Dict[str, List[float]] = {}
        for sc in round_scores:
            em = (sc.get("participant_id") or sc.get("user_email") or "").strip().lower()
            val = float(sc.get("total_score") or 0.0)
            if em:
                scores_by_email.setdefault(em, []).append(val)

        # 3. Fetch audience votes strictly for this round
        vote_filter: Dict[str, Any] = {"competition_id": {"$in": c_matches}}
        if target_r_num == 1:
            vote_filter["$or"] = [{"round_number": 1}, {"round_number": {"$exists": False}}, {"round_number": None}]
        else:
            vote_filter["round_number"] = target_r_num

        round_votes = await audience_vote_collection.find(vote_filter).to_list(length=5000)
        votes_by_email: Dict[str, int] = {}
        for v in round_votes:
            em = (v.get("participant_id") or v.get("voter_email") or "").strip().lower()
            p_val = int(v.get("voting_power") or 1)
            if em:
                votes_by_email[em] = votes_by_email.get(em, 0) + p_val

        # 4. Build participant items for ranking calculation
        max_possible_judge = sum(crit.get("max_score", 0.0) for crit in comp.get("judging_criteria", []))
        max_votes = max(votes_by_email.values()) if votes_by_email else 0

        participants_data = []
        for r in regs:
            em = (r.get("user_email") or r.get("email") or "").strip().lower()
            usr = users_map.get(em, {})
            sub = sub_map.get(em, {})

            name = (
                r.get("user_name")
                or usr.get("full_name")
                or usr.get("name")
                or r.get("participant_name")
                or (em.split("@")[0].title() if em else "Contestant")
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

            p_scores = scores_by_email.get(em, [])
            avg_judge_score = round(sum(p_scores) / len(p_scores), 2) if p_scores else 0.0
            p_votes = votes_by_email.get(em, 0)
            p_cat = r.get("category") or (sub.get("category") if sub else None) or comp.get("category") or "General"

            is_p = bool(r.get("is_present", False) or r.get("attendance_status") == "PRESENT")

            participants_data.append({
                "participant_id": em,
                "email": em,
                "user_email": em,
                "name": name,
                "profile_photo": photo,
                "avatar": photo,
                "category": p_cat,
                "judge_score": avg_judge_score,
                "judge_score_count": len(p_scores),
                "audience_votes": p_votes,
                "is_verified": is_v,
                "verified_badge": is_v,
                "verified_tick": is_v,
                "is_present": is_p,
                "attendance_status": r.get("attendance_status", "PRESENT" if is_p else "ABSENT"),
                "registration_id": str(r["_id"]),
                "registration_reference": r.get("registration_reference") or f"CRG-{str(r['_id'])[:8]}",
                "round_status": r.get("round_status", "QUALIFIED")
            })

        # Calculate round leaderboard
        leaderboard = RankingService.calculate_leaderboard(
            participants=participants_data,
            max_audience_votes=max_votes,
            max_judge_score=max_possible_judge,
            comp_settings=comp
        )

        # Mark advancing status (e.g. top 10)
        for idx, item in enumerate(leaderboard):
            if advancing_count and advancing_count > 0:
                item["is_advancing"] = bool(idx < advancing_count)
            else:
                item["is_advancing"] = False

        category_leaderboards = RankingService.calculate_category_leaderboards(leaderboard)

        all_cats = [c.get("name") for c in comp.get("categories", []) if isinstance(c, dict) and c.get("name")]
        for k in category_leaderboards.keys():
            if k not in all_cats:
                all_cats.append(k)

        target_lb = leaderboard
        if category and category.strip():
            c_clean = category.strip().lower()
            target_lb = category_leaderboards.get(category.strip(), [
                x for x in leaderboard if (x.get("category") or "").strip().lower() == c_clean
            ])

        return {
            "competition_id": c_str,
            "competition_title": comp.get("title", "Competition"),
            "round_number": target_r_num,
            "round_name": round_cfg.get("name", f"Round {target_r_num}"),
            "round_status": round_cfg.get("status", "ACTIVE"),
            "is_final_round": bool(round_cfg.get("is_final_round")),
            "advancing_count": advancing_count,
            "current_active_round": int(comp.get("current_round") or 1),
            "total_rounds": len(rounds),
            "total_participants": len(leaderboard),
            "total_participants_in_round": len(leaderboard),
            "rounds": rounds,
            "category": category,
            "categories": all_cats,
            "leaderboard": target_lb,
            "category_leaderboards": category_leaderboards
        }

    @classmethod
    async def advance_contestants(
        cls,
        comp_id: str,
        round_number: int,
        advancing_count: Optional[int] = None,
        contestant_ids: Optional[List[str]] = None,
        next_round_number: Optional[int] = None,
        admin_email: str = "admin@upgradetalentsindia.in",
        notes: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Advances top N (e.g. top 10 of 100) or explicit contestants from round_number into next_round_number!
        - Marks selected contestants as QUALIFIED for next round.
        - Marks non-advancing contestants as ELIMINATED.
        - Switches active round in competition to next_round_number.
        - Broadcasts real-time WebSocket update so admin, judges, stage, and users reflect new lineup.
        """
        comp = await cls.get_competition_or_404(comp_id)
        c_str = str(comp["_id"])
        c_matches = cls._get_comp_matches(c_str, comp)
        rounds = cls.normalize_competition_rounds(comp)

        rounds = cls.normalize_competition_rounds(comp)
        curr_round_cfg = next((r for r in rounds if r["round_number"] == round_number), None)
        if not curr_round_cfg:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail=f"Round {round_number} not found.")

        target_next = next_round_number or (round_number + 1)
        next_round_cfg = next((r for r in rounds if r["round_number"] == target_next), None)
        if not next_round_cfg:
            # Auto-create next round if not pre-configured
            next_round_cfg = {
                "round_number": target_next,
                "name": f"Round {target_next}" if target_next < len(rounds) + 1 else "Grand Finale",
                "description": f"Round {target_next} qualifiers",
                "status": "ACTIVE",
                "advancing_count": None,
                "is_final_round": True
            }
            rounds.append(next_round_cfg)

        # 1. Fetch current round leaderboard to determine top qualifiers
        round_lb_data = await cls.get_round_leaderboard(c_str, round_number=round_number)
        full_leaderboard = round_lb_data.get("leaderboard", [])

        if not full_leaderboard:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail=f"No participants found in Round {round_number} to advance.")

        # Determine cutoff count
        cutoff_count = advancing_count or curr_round_cfg.get("advancing_count") or 10

        selected_contestants: List[Dict[str, Any]] = []
        eliminated_contestants: List[Dict[str, Any]] = []

        if contestant_ids and len(contestant_ids) > 0:
            clean_ids = {str(x).strip().lower() for x in contestant_ids}
            for item in full_leaderboard:
                em = (item.get("user_email") or item.get("email") or item.get("participant_id") or "").strip().lower()
                rid = str(item.get("registration_id") or "").strip().lower()
                ref = str(item.get("registration_reference") or "").strip().lower()
                if em in clean_ids or rid in clean_ids or ref in clean_ids:
                    selected_contestants.append(item)
                else:
                    eliminated_contestants.append(item)
        else:
            selected_contestants = full_leaderboard[:cutoff_count]
            eliminated_contestants = full_leaderboard[cutoff_count:]

        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()

        # 2. Update Advancing Contestants
        advancing_emails = list({(s.get("user_email") or s.get("email") or s.get("participant_id") or "").strip().lower() for s in selected_contestants})
        advancing_rids = [s.get("registration_id") for s in selected_contestants if s.get("registration_id")]

        adv_or: List[Dict[str, Any]] = []
        if advancing_emails:
            adv_or.append({"user_email": {"$in": advancing_emails}})
            adv_or.append({"email": {"$in": advancing_emails}})
        if advancing_rids:
            adv_or.append({"_id": {"$in": [ObjectId(x) for x in advancing_rids if ObjectId.is_valid(x)]}})
            adv_or.append({"registration_reference": {"$in": advancing_rids}})

        if adv_or:
            await competition_registration_collection.update_many(
                {"competition_id": {"$in": c_matches}, "$or": adv_or},
                {
                    "$addToSet": {"qualified_rounds": target_next},
                    "$set": {
                        "current_round": target_next,
                        "highest_round": target_next,
                        "round_status": "QUALIFIED",
                        "stage_status": "NOT_STARTED",
                        "updated_at": now_iso
                    },
                    "$push": {
                        "round_history": {
                            "round_number": round_number,
                            "action": "ADVANCED_TO_NEXT_ROUND",
                            "next_round": target_next,
                            "status": "QUALIFIED",
                            "advanced_at": now_iso,
                            "advanced_by": admin_email,
                            "notes": notes
                        }
                    }
                }
            )

        # 3. Update Eliminated Contestants
        elim_emails = list({(e.get("user_email") or e.get("email") or e.get("participant_id") or "").strip().lower() for e in eliminated_contestants})
        elim_rids = [e.get("registration_id") for e in eliminated_contestants if e.get("registration_id")]

        elim_or: List[Dict[str, Any]] = []
        if elim_emails:
            elim_or.append({"user_email": {"$in": elim_emails}})
            elim_or.append({"email": {"$in": elim_emails}})
        if elim_rids:
            elim_or.append({"_id": {"$in": [ObjectId(x) for x in elim_rids if ObjectId.is_valid(x)]}})
            elim_or.append({"registration_reference": {"$in": elim_rids}})

        if elim_or:
            await competition_registration_collection.update_many(
                {"competition_id": {"$in": c_matches}, "$or": elim_or},
                {
                    "$set": {
                        "round_status": "ELIMINATED",
                        "updated_at": now_iso
                    },
                    "$push": {
                        "round_history": {
                            "round_number": round_number,
                            "action": "ELIMINATED",
                            "status": "ELIMINATED",
                            "eliminated_at": now_iso
                        }
                    }
                }
            )

        # 4. Update Competition round statuses
        for r in rounds:
            r_num = r["round_number"]
            if r_num <= round_number:
                r["status"] = "COMPLETED"
            elif r_num == target_next:
                r["status"] = "ACTIVE"
            else:
                r["status"] = "UPCOMING"

        # Reset stage for the new round
        stage_update = {
            "stage_status": "IDLE",
            "current_performer": None,
            "previous_performer": None,
            "current_round": target_next,
            "updated_at": now_iso,
            "updated_by": admin_email
        }

        await competition_collection.update_one(
            cls._resolve_comp_query(c_str),
            {"$set": {
                "rounds": rounds,
                "current_round": target_next,
                "total_rounds": len(rounds),
                "live_stage": stage_update,
                "updated_at": now
            }}
        )

        await log_admin_action(
            admin_email,
            "advance_contestants_round",
            "competition",
            c_str,
            metadata={
                "from_round": round_number,
                "to_round": target_next,
                "advanced_count": len(selected_contestants),
                "eliminated_count": len(eliminated_contestants)
            }
        )

        # Real-time WebSocket Broadcast
        await ws_manager.broadcast_to_competition(c_str, {
            "event": "round_advanced",
            "competition_id": c_str,
            "completed_round": round_number,
            "new_active_round": target_next,
            "total_in_completed_round": len(full_leaderboard),
            "advancing_count": len(selected_contestants),
            "eliminated_count": len(eliminated_contestants),
            "advanced_contestants": [
                {
                    "participant_id": s.get("participant_id"),
                    "name": s.get("name"),
                    "score": s.get("final_score"),
                    "rank": s.get("rank")
                }
                for s in selected_contestants
            ]
        })

        return {
            "competition_id": c_str,
            "completed_round": round_number,
            "from_round": round_number,
            "new_active_round": target_next,
            "next_round": target_next,
            "total_in_completed_round": len(full_leaderboard),
            "advancing_count": len(selected_contestants),
            "advanced_count": len(selected_contestants),
            "eliminated_count": len(eliminated_contestants),
            "advanced_contestant_ids": [s.get("participant_id") for s in selected_contestants],
            "advanced_contestants": selected_contestants,
            "message": f"Successfully advanced {len(selected_contestants)} contestants from Round {round_number} into Round {target_next}."
        }

    @classmethod
    async def get_round_contestants(
        cls,
        comp_id: str,
        round_number: Optional[int] = None,
        category: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Returns all contestants qualified to participate in a specific round.
        """
        comp = await cls.get_competition_or_404(comp_id)
        c_str = str(comp["_id"])
        c_matches = cls._get_comp_matches(c_str, comp)
        rounds = cls.normalize_competition_rounds(comp)

        target_r = int(round_number) if round_number is not None else int(comp.get("current_round") or 1)

        reg_query: Dict[str, Any] = {"competition_id": {"$in": c_matches}}
        if target_r > 1:
            reg_query["$or"] = [
                {"qualified_rounds": target_r},
                {"current_round": target_r},
                {"highest_round": {"$gte": target_r}}
            ]

        if category and category.strip() and category.upper() != "ALL":
            reg_query["category"] = category.strip()

        regs = await competition_registration_collection.find(reg_query).sort([("stage_order", 1), ("registered_at", 1)]).to_list(length=3000)
        if not regs and target_r == 1:
            regs = await registration_collection.find({
                "$or": [{"activity_id": {"$in": c_matches}}, {"competition_id": {"$in": c_matches}}]
            }).to_list(length=3000)

        emails = list({(r.get("user_email") or r.get("email") or "").strip().lower() for r in regs if (r.get("user_email") or r.get("email"))})
        users_map = {}
        if emails:
            usrs = await user_collection.find({"email": {"$in": emails}}).to_list(length=len(emails) * 2)
            users_map = {(u.get("email") or "").strip().lower(): u for u in usrs}

        # Fetch judge scores for this round
        score_filter: Dict[str, Any] = {"competition_id": {"$in": c_matches}}
        if target_r == 1:
            score_filter["$or"] = [{"round_number": 1}, {"round_number": {"$exists": False}}, {"round_number": None}]
        else:
            score_filter["round_number"] = target_r
        scores = await judge_score_collection.find(score_filter).to_list(length=5000)
        score_map = {}
        for sc in scores:
            em = (sc.get("participant_id") or sc.get("user_email") or "").strip().lower()
            if em:
                score_map.setdefault(em, []).append(float(sc.get("total_score") or 0.0))

        res = []
        for idx, r in enumerate(regs):
            em = (r.get("user_email") or r.get("email") or "").strip().lower()
            usr = users_map.get(em, {})

            name = (
                r.get("user_name")
                or usr.get("full_name")
                or usr.get("name")
                or r.get("participant_name")
                or (em.split("@")[0].title() if em else f"Contestant #{idx+1}")
            )
            photo = usr.get("profile_photo") or usr.get("avatar") or r.get("profile_photo") or ""
            phone = usr.get("phone_number") or usr.get("phone") or r.get("user_phone") or ""

            is_v = bool(
                usr.get("is_verified")
                or usr.get("is_id_verified")
                or usr.get("is_govt_id_verified")
                or usr.get("verified_badge")
                or usr.get("verified_tick")
                or usr.get("kyc_status") == "VERIFIED"
            )

            p_sc = score_map.get(em, [])
            avg_sc = round(sum(p_sc) / len(p_sc), 2) if p_sc else None
            is_p = bool(r.get("is_present", False) or r.get("attendance_status") == "PRESENT")

            res.append({
                "registration_id": str(r["_id"]),
                "registration_reference": r.get("registration_reference") or f"CRG-{str(r['_id'])[:8]}",
                "user_email": em,
                "user_name": name,
                "full_name": name,
                "user_phone": phone,
                "profile_photo": photo,
                "avatar": photo,
                "category": r.get("category") or comp.get("category") or "General",
                "current_round": target_r,
                "round_status": r.get("round_status", "QUALIFIED"),
                "is_present": is_p,
                "attendance_status": r.get("attendance_status", "PRESENT" if is_p else "ABSENT"),
                "is_verified": is_v,
                "verified_badge": is_v,
                "verified_tick": is_v,
                "stage_order": r.get("stage_order", idx + 1),
                "stage_status": r.get("stage_status", "NOT_STARTED"),
                "round_score": avg_sc,
                "score": avg_sc,
                "total_score": avg_sc,
                "judge_score_count": len(p_sc)
            })

        return {
            "competition_id": c_str,
            "competition_title": comp.get("title", "Competition"),
            "round_number": target_r,
            "round_name": next((r.get("name") for r in rounds if r.get("round_number") == target_r), f"Round {target_r}"),
            "total_contestants": len(res),
            "contestants": res
        }
