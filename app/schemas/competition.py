from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime
from enum import Enum
import re

def parse_age_limit(val: Any) -> Tuple[Optional[int], Optional[int]]:
    """
    Parses age limit string representations like '12-15', '12 - 15', '12 to 15',
    '12+', 'under 15', 'up to 15 years' into a (min_age, max_age) tuple.
    """
    if not val:
        return None, None
    if isinstance(val, (list, tuple)) and len(val) >= 2:
        try:
            return int(val[0]), int(val[1])
        except (ValueError, TypeError):
            pass
    if not isinstance(val, str):
        return None, None
    s = val.strip().lower()

    # 1. Range pattern: "12-15", "12 - 15", "12 to 15", "between 12 and 15"
    m = re.search(r'(\d{1,2})\s*(?:-|to|\.\.|and)\s*(\d{1,2})', s)
    if m:
        try:
            v1, v2 = int(m.group(1)), int(m.group(2))
            return min(v1, v2), max(v1, v2)
        except (ValueError, TypeError):
            pass

    # 2. Plus / Above pattern: "12+", "12 +", "above 12", "12 and above", ">= 12", "minimum 12", "min 12"
    m = re.search(r'(\d{1,2})\s*\+|(?:\b(?:above|over|from|min|minimum|>=|>)\b\s*(\d{1,2}))|(\d{1,2})\s*(?:and above|and older)', s)
    if m:
        val_str = next(g for g in m.groups() if g is not None)
        try:
            return int(val_str), None
        except (ValueError, TypeError):
            pass

    # 3. Under / Below / Up to pattern: "under 15", "below 15", "up to 15", "<= 15", "max 15", "maximum 15"
    m = re.search(r'(?:\b(?:under|below|up to|upto|max|maximum|<=|<)\b\s*(\d{1,2}))', s)
    if m:
        try:
            return None, int(m.group(1))
        except (ValueError, TypeError):
            pass

    return None, None

def check_no_fake_cloudinary(v: Optional[str]) -> Optional[str]:
    if v and isinstance(v, str):
        v_low = v.lower()
        if "res.cloudinary.com/demo" in v_low or "cloudinary.com/demo" in v_low:
            raise ValueError("Fake demo Cloudinary URLs are strictly prohibited.")
    return v

class FieldType(str, Enum):
    TEXT = "TEXT"
    NUMBER = "NUMBER"
    DATE = "DATE"
    IMAGE = "IMAGE"
    AUDIO = "AUDIO"
    VIDEO = "VIDEO"
    DOCUMENT = "DOCUMENT"
    URL = "URL"
    PORTFOLIO_LINK = "PORTFOLIO_LINK"

class DynamicFieldConfig(BaseModel):
    name: str
    label: str
    type: FieldType
    description: Optional[str] = None
    required: bool = True
    allowed_formats: Optional[List[str]] = None
    max_size_mb: Optional[float] = None
    display_order: int = 0

class JudgingCriterion(BaseModel):
    name: str
    description: Optional[str] = None
    max_score: float
    weight: float = 1.0
    display_order: int = 0

class SubmissionFormatConfig(BaseModel):
    allow_file_upload: bool = False
    allow_video_link: bool = False
    allow_audio_upload: bool = False
    allowed_submission_types: List[str] = []
    require_all_fields: bool = False
    description: Optional[str] = None
    enabled_fields: Optional[List[str]] = None

class CompetitionRules(BaseModel):
    terms_and_conditions: str = ""
    eligibility_requirements: str = ""
    submission_guidelines: str = ""
    disqualification_rules: str = ""
    prize_information: str = ""

class CompetitionCategory(BaseModel):
    name: str = Field(..., min_length=1, description="Category name, e.g. 'Junior', 'Senior', 'Classical Dance'")
    min_age: Optional[int] = Field(None, ge=0, description="Minimum age allowed for this category")
    max_age: Optional[int] = Field(None, ge=0, description="Maximum age allowed for this category")
    age_limit: Optional[str] = Field(None, description="Formatted age limit string, e.g. '12-15' or '12-15 years'")
    description: Optional[str] = Field(None, description="Optional description of this category")

    @model_validator(mode="before")
    @classmethod
    def populate_age_fields(cls, data: Any):
        if isinstance(data, dict):
            min_a = data.get("min_age")
            max_a = data.get("max_age")
            age_lim = data.get("age_limit")
            if (min_a is None or max_a is None) and age_lim:
                p_min, p_max = parse_age_limit(age_lim)
                if min_a is None and p_min is not None:
                    data["min_age"] = p_min
                    min_a = p_min
                if max_a is None and p_max is not None:
                    data["max_age"] = p_max
                    max_a = p_max
            if not age_lim:
                if min_a is not None and max_a is not None:
                    data["age_limit"] = f"{min_a}-{max_a} years"
                elif min_a is not None:
                    data["age_limit"] = f"{min_a}+ years"
                elif max_a is not None:
                    data["age_limit"] = f"Up to {max_a} years"
                else:
                    data["age_limit"] = "Open for all ages"
        return data


class CompetitionRegisterReq(BaseModel):
    rules_accepted: Optional[bool] = None
    payment_method: Optional[str] = "ONLINE"
    category: Optional[str] = None
    date_of_birth: Optional[str] = None
    dob: Optional[str] = None
    age: Optional[int] = None

class CompetitionCreateReq(BaseModel):
    title: str = Field(..., min_length=3)
    description: str
    image_url: Optional[str] = None
    card_image_url: Optional[str] = None
    banner_url: Optional[str] = None
    banner_image_url: Optional[str] = None
    type: str = "General"
    category: Optional[str] = "General"
    categories: List[CompetitionCategory] = []
    
    start_date: datetime
    end_date: datetime
    registration_start_date: datetime
    registration_deadline: Optional[datetime] = None
    submission_deadline: datetime
    
    min_age: Optional[int] = None
    max_age: Optional[int] = None
    allowed_age_groups: List[str] = []
    
    participation_fee: float = 0.0
    currency: str = "USD"
    max_participants: Optional[int] = None
    
    is_published: bool = False
    is_registration_open: Optional[bool] = True
    registration_status: Optional[str] = "OPEN"
    audience_voting_enabled: bool = False
    judge_scoring_enabled: bool = False
    audience_vote_weight: float = 0.0
    judge_score_weight: float = 0.0
    
    rules: Optional[CompetitionRules] = None
    venue: Optional[str] = None
    is_offline: Optional[bool] = False
    event_mode: Optional[str] = "ONLINE"
    allow_file_upload: Optional[bool] = None
    allow_video_link: Optional[bool] = None
    allow_audio_upload: Optional[bool] = None
    submission_formats: Optional[List[str]] = None
    submission_format_config: Optional[SubmissionFormatConfig] = None
    dynamic_submission_fields: List[DynamicFieldConfig] = []
    judging_criteria: List[JudgingCriterion] = []
    rounds: Optional[List[Dict[str, Any]]] = None
    current_round: Optional[int] = 1
    total_rounds: Optional[int] = None

    @field_validator("image_url", "card_image_url", "banner_url", "banner_image_url", mode="before")
    @classmethod
    def validate_image_urls(cls, v):
        return check_no_fake_cloudinary(v)

class CompetitionUpdateReq(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    image_url: Optional[str] = None
    card_image_url: Optional[str] = None
    banner_url: Optional[str] = None
    banner_image_url: Optional[str] = None
    type: Optional[str] = None
    category: Optional[str] = None
    categories: Optional[List[CompetitionCategory]] = None
    
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    registration_start_date: Optional[datetime] = None
    registration_deadline: Optional[datetime] = None
    submission_deadline: Optional[datetime] = None
    
    min_age: Optional[int] = None
    max_age: Optional[int] = None
    allowed_age_groups: Optional[List[str]] = None
    
    participation_fee: Optional[float] = None
    currency: Optional[str] = None
    max_participants: Optional[int] = None
    
    is_published: Optional[bool] = None
    is_registration_open: Optional[bool] = None
    registration_status: Optional[str] = None
    audience_voting_enabled: Optional[bool] = None
    judge_scoring_enabled: Optional[bool] = None
    audience_vote_weight: Optional[float] = None
    judge_score_weight: Optional[float] = None
    
    rules: Optional[CompetitionRules] = None
    venue: Optional[str] = None
    is_offline: Optional[bool] = False
    event_mode: Optional[str] = "ONLINE"
    allow_file_upload: Optional[bool] = None
    allow_video_link: Optional[bool] = None
    allow_audio_upload: Optional[bool] = None
    submission_formats: Optional[List[str]] = None
    submission_format_config: Optional[SubmissionFormatConfig] = None
    dynamic_submission_fields: Optional[List[DynamicFieldConfig]] = None
    judging_criteria: Optional[List[JudgingCriterion]] = None
    rounds: Optional[List[Dict[str, Any]]] = None
    current_round: Optional[int] = None
    total_rounds: Optional[int] = None

    @field_validator("image_url", "card_image_url", "banner_url", "banner_image_url", mode="before")
    @classmethod
    def validate_image_urls(cls, v):
        return check_no_fake_cloudinary(v)



class VotingStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"

class JudgingStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"

class ResultStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    PUBLISHED = "PUBLISHED"

class RegistrationStatus(str, Enum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"

class SubmissionStatus(str, Enum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    UNDER_REVIEW = "UNDER_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"

class ScoreReq(BaseModel):
    scores: Dict[str, float]  # Mapping of criterion name to score
    participant_id: Optional[str] = Field(None, description="Participant ID, email, registration ID or submission ID")
    comments: Optional[str] = None
    feedback: Optional[str] = None
    round_number: Optional[int] = Field(None, description="Round number for which score is given. Defaults to active competition round.")
    
class VoteReq(BaseModel):
    participant_id: str
    source: str = "WEBSITE"  # WEBSITE or MOBILE_APP
    is_paid_pack: bool = False
    payment_reference: Optional[str] = None
    round_number: Optional[int] = Field(None, description="Round number for which vote is given. Defaults to active competition round.")


# ── Advanced Judging Schemas ──

class DraftScoreReq(BaseModel):
    scores: Dict[str, float]
    comments: Optional[str] = None
    round_number: Optional[int] = Field(None, description="Round number for draft score.")

class COISkipReq(BaseModel):
    reason: str = "Conflict of Interest"

class JudgeFeedbackReq(BaseModel):
    text_feedback: Optional[str] = None
    audio_feedback_url: Optional[str] = None

class SessionPINReq(BaseModel):
    pin: str

class SessionCreateReq(BaseModel):
    pin: Optional[str] = None


# ── Live Stage Performance Schemas ──

class StagePerformerSetReq(BaseModel):
    identifier: Optional[str] = None
    registration_id: Optional[str] = None
    registration_reference: Optional[str] = None
    user_email: Optional[str] = None
    participant_id: Optional[str] = None
    notes: Optional[str] = None

class StageOrderUpdateReq(BaseModel):
    orders: List[Dict[str, Any]]  # [{"registration_id": str, "stage_order": int}]

class StageScoreReq(BaseModel):
    scores: Dict[str, float]
    comments: Optional[str] = None
