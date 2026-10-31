from typing import List, Dict, Any
import logging

logger = logging.getLogger(__name__)

class RankingService:
    @staticmethod
    def normalize_scores(raw_score: float, max_possible: float) -> float:
        if max_possible <= 0:
            return 0.0
        return min((raw_score / max_possible) * 100, 100.0)

    @staticmethod
    def calculate_final_score(
        participant_id: str,
        audience_votes: int,
        max_audience_votes: int,
        judge_score: float,
        max_judge_score: float,
        audience_enabled: bool,
        judge_enabled: bool,
        audience_weight: float,
        judge_weight: float
    ) -> Dict[str, Any]:
        """
        Authoritative service to calculate a single participant's final ranking score.
        """
        norm_audience = RankingService.normalize_scores(float(audience_votes), float(max_audience_votes)) if max_audience_votes > 0 else 0.0
        
        # If max_judge_score <= 0 or not configured, normalize against 100 or use raw judge_score
        effective_max_judge = max_judge_score if max_judge_score > 0 else (100.0 if judge_score <= 100.0 else judge_score)
        norm_judge = RankingService.normalize_scores(judge_score, effective_max_judge) if effective_max_judge > 0 else judge_score
        
        final_score = 0.0
        ranking_mode = "UNKNOWN"

        if audience_enabled and not judge_enabled:
            ranking_mode = "AUDIENCE_VOTING_ONLY"
            final_score = norm_audience if max_audience_votes > 0 else float(audience_votes)
            
        elif not audience_enabled and judge_enabled:
            ranking_mode = "JUDGES_SCORE_ONLY"
            final_score = norm_judge if max_judge_score > 0 else judge_score
            
        elif audience_enabled and judge_enabled:
            ranking_mode = "COMBINED_AUDIENCE_AND_JUDGES"
            # Ensure weights are applied as fractions
            a_frac = audience_weight if audience_weight <= 1.0 else (audience_weight / 100.0)
            j_frac = judge_weight if judge_weight <= 1.0 else (judge_weight / 100.0)
            if a_frac == 0 and j_frac == 0:
                a_frac, j_frac = 0.5, 0.5
            
            final_score = (norm_audience * a_frac) + (norm_judge * j_frac)
        else:
            # Fallback when explicit flags are not set on comp document
            if judge_score > 0 and audience_votes > 0:
                ranking_mode = "COMBINED_AUDIENCE_AND_JUDGES"
                final_score = (norm_audience * 0.5) + (norm_judge * 0.5)
            elif judge_score > 0:
                ranking_mode = "JUDGES_SCORE_ONLY"
                final_score = norm_judge or judge_score
            elif audience_votes > 0:
                ranking_mode = "AUDIENCE_VOTING_ONLY"
                final_score = norm_audience or float(audience_votes)
            else:
                ranking_mode = "UNKNOWN"
                final_score = 0.0

        # Safety: If final score evaluated to 0.0 but user has actual points, don't drop points to zero
        if final_score <= 0.0:
            if judge_score > 0:
                final_score = judge_score
            elif audience_votes > 0:
                final_score = float(audience_votes)
        
        return {
            "participant_id": participant_id,
            "raw_audience_votes": audience_votes,
            "normalized_audience_score": norm_audience,
            "raw_judge_score": judge_score,
            "normalized_judge_score": norm_judge,
            "final_score": round(final_score, 2),
            "total_score": round(final_score, 2),
            "score": round(final_score, 2),
            "points": round(final_score, 2),
            "audience_weight": audience_weight,
            "judge_weight": judge_weight,
            "ranking_mode": ranking_mode
        }

    @staticmethod
    def calculate_leaderboard(
        participants: List[Dict[str, Any]], 
        max_audience_votes: int, 
        max_judge_score: float,
        comp_settings: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Calculates and sorts a leaderboard for multiple participants.
        `participants` must contain: participant_id, audience_votes, judge_score.
        """
        leaderboard = []
        for p in participants:
            result = RankingService.calculate_final_score(
                participant_id=p["participant_id"],
                audience_votes=p.get("audience_votes", 0),
                max_audience_votes=max_audience_votes,
                judge_score=p.get("judge_score", 0.0),
                max_judge_score=max_judge_score,
                audience_enabled=comp_settings.get("audience_voting_enabled", False),
                judge_enabled=comp_settings.get("judge_scoring_enabled", False),
                audience_weight=comp_settings.get("audience_vote_weight", 0.0),
                judge_weight=comp_settings.get("judge_score_weight", 0.0)
            )
            # Add user details if provided
            if "name" in p: result["name"] = p["name"]
            if "email" in p: result["email"] = p["email"]
            if "profile_photo" in p: result["profile_photo"] = p["profile_photo"]
            if "audience_votes" in p: result["audience_votes"] = p["audience_votes"]
            if "judge_score" in p: result["judge_score"] = p["judge_score"]
            if "is_verified" in p: result["is_verified"] = p["is_verified"]
            if "verified_badge" in p: result["verified_badge"] = p["verified_badge"]
            if "verified_tick" in p: result["verified_tick"] = p["verified_tick"]
            result["total_score"] = result.get("total_score", result["final_score"])
            result["score"] = result.get("score", result["final_score"])
            result["points"] = result.get("points", result["final_score"])
            
            leaderboard.append(result)
            
        # Sort descending by final_score
        leaderboard.sort(key=lambda x: x["final_score"], reverse=True)
        
        # Assign ranks
        for idx, item in enumerate(leaderboard, 1):
            item["rank"] = idx
            
        return leaderboard
