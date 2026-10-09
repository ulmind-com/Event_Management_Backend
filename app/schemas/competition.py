from pydantic import BaseModel, Field, HttpUrl
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum

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
    audience_vote_weight: float = 0.0
    
    rules: Optional[CompetitionRules] = None
    dynamic_submission_fields: List[DynamicFieldConfig] = []
    judging_criteria: List[JudgingCriterion] = []

class CompetitionUpdateReq(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    image_url: Optional[str] = None
    is_published: Optional[bool] = None
    # Add other fields as needed for partial updates

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
