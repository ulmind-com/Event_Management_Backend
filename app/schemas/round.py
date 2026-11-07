from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from enum import Enum
from datetime import datetime


class RoundStatus(str, Enum):
    UPCOMING = "UPCOMING"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"


class CompetitionRoundConfig(BaseModel):
    round_number: int = Field(..., ge=1, description="Round number: 1, 2, 3...")
    name: str = Field(..., min_length=1, description="Round title, e.g. 'Round 1 - Preliminary', 'Round 2 - Semi-Final', 'Grand Finale'")
    description: Optional[str] = None
    status: str = Field(RoundStatus.UPCOMING.value, description="UPCOMING, ACTIVE, COMPLETED")
    advancing_count: Optional[int] = Field(None, ge=1, description="Number of contestants who qualify to advance to the next round, e.g. 10")
    is_final_round: bool = Field(False, description="Whether this is the final championship round")
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None


class RoundCreateReq(BaseModel):
    round_number: int = Field(..., ge=1)
    name: str = Field(..., min_length=1)
    description: Optional[str] = None
    advancing_count: Optional[int] = Field(None, ge=1)
    is_final_round: bool = False
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None


class RoundUpdateReq(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    advancing_count: Optional[int] = None
    is_final_round: Optional[bool] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None


class RoundsConfigureReq(BaseModel):
    rounds: List[CompetitionRoundConfig]


class AdvanceContestantsReq(BaseModel):
    advancing_count: Optional[int] = Field(None, ge=1, description="Top N contestants from this round's leaderboard to advance, e.g. 10")
    contestant_ids: Optional[List[str]] = Field(None, description="Explicit list of registration IDs, user emails, or participant IDs to advance")
    next_round_number: Optional[int] = Field(None, ge=1, description="Round number to advance into (defaults to round_number + 1)")
    notes: Optional[str] = None


class RoundContestantItem(BaseModel):
    registration_id: str
    registration_reference: str
    user_email: str
    user_name: str
    full_name: Optional[str] = None
    user_phone: Optional[str] = None
    profile_photo: Optional[str] = None
    category: Optional[str] = "General"
    current_round: int = 1
    highest_round: int = 1
    qualified_rounds: List[int] = [1]
    round_status: str = "QUALIFIED"  # QUALIFIED, ELIMINATED, COMPLETED
    is_present: bool = False
    attendance_status: str = "ABSENT"
    is_verified: bool = False
    stage_order: int = 0
    stage_status: str = "NOT_STARTED"
    round_score: Optional[float] = None
    round_rank: Optional[int] = None


class RoundLeaderboardResponse(BaseModel):
    competition_id: str
    competition_title: str
    round_number: int
    round_name: str
    round_status: str
    is_final_round: bool = False
    advancing_count: Optional[int] = None
    current_active_round: int = 1
    total_rounds: int = 1
    total_participants_in_round: int = 0
    rounds: List[Dict[str, Any]] = []
    category: Optional[str] = None
    categories: List[str] = []
    leaderboard: List[Dict[str, Any]] = []
    category_leaderboards: Dict[str, List[Dict[str, Any]]] = {}


class RoundAdvancementSummary(BaseModel):
    competition_id: str
    completed_round: int
    new_active_round: int
    total_in_completed_round: int
    advancing_count: int
    eliminated_count: int
    advanced_contestants: List[Dict[str, Any]]
    message: str
