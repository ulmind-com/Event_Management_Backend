from pydantic import BaseModel, Field, HttpUrl, field_validator
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum

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

class CompetitionRules(BaseModel):
    terms_and_conditions: str = ""
    eligibility_requirements: str = ""
    submission_guidelines: str = ""
    disqualification_rules: str = ""
    prize_information: str = ""

class CompetitionCreateReq(BaseModel):
    title: str = Field(..., min_length=3)
    description: str
    image_url: Optional[str] = None
    type: str = "General"
    category: str
    
    start_date: datetime
    end_date: datetime
    registration_start_date: datetime
    registration_deadline: datetime
    submission_deadline: datetime
    
    min_age: Optional[int] = None
    max_age: Optional[int] = None
    allowed_age_groups: List[str] = []
    
    participation_fee: float = 0.0
    currency: str = "USD"
    max_participants: Optional[int] = None
    
    is_published: bool = False
    audience_voting_enabled: bool = False
    judge_scoring_enabled: bool = False
    audience_vote_weight: float = 0.0
    judge_score_weight: float = 0.0
    
    rules: Optional[CompetitionRules] = None
    dynamic_submission_fields: List[DynamicFieldConfig] = []
    judging_criteria: List[JudgingCriterion] = []

    @field_validator("image_url", mode="before")
    @classmethod
    def validate_image_url(cls, v):
        return check_no_fake_cloudinary(v)

class CompetitionUpdateReq(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    image_url: Optional[str] = None
    type: Optional[str] = None
    category: Optional[str] = None
    
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
    audience_voting_enabled: Optional[bool] = None
    judge_scoring_enabled: Optional[bool] = None
    audience_vote_weight: Optional[float] = None
    judge_score_weight: Optional[float] = None
    
    rules: Optional[CompetitionRules] = None
    dynamic_submission_fields: Optional[List[DynamicFieldConfig]] = None
    judging_criteria: Optional[List[JudgingCriterion]] = None

    @field_validator("image_url", mode="before")
    @classmethod
    def validate_image_url(cls, v):
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
    comments: Optional[str] = None
    
class VoteReq(BaseModel):
    participant_id: str
    source: str = "WEBSITE"  # WEBSITE or MOBILE_APP
    is_paid_pack: bool = False
    payment_reference: Optional[str] = None


# ── Advanced Judging Schemas ──

class DraftScoreReq(BaseModel):
    scores: Dict[str, float]
    comments: Optional[str] = None

class COISkipReq(BaseModel):
    reason: str = "Conflict of Interest"

class JudgeFeedbackReq(BaseModel):
    text_feedback: Optional[str] = None
    audio_feedback_url: Optional[str] = None

class SessionPINReq(BaseModel):
    pin: str

class SessionCreateReq(BaseModel):
    pin: Optional[str] = None
