from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum

class BadgeQualificationMode(str, Enum):
    THRESHOLD = 'THRESHOLD'
    TOP_PERCENTAGE = 'TOP_PERCENTAGE'
    TOP_N = 'TOP_N'

class BadgeStatus(str, Enum):
    ACTIVE = 'ACTIVE'
    REVOKED = 'REVOKED'

class BadgePolicyConfig(BaseModel):
    badge_id: str = 'TOP_PARTICIPATION'
    badge_name: str = 'Top Participation'
    description: str = 'Recognizes participants who complete multiple verified Eventora competitions or events.'
    category: str = 'MILESTONE'
    image_url: Optional[str] = '/badges/top-participation.svg'
    enabled: bool = True
    qualification_mode: BadgeQualificationMode = BadgeQualificationMode.THRESHOLD
    minimum_qualifying_participations: int = Field(default=10, ge=1)
    count_distinct_activities: bool = True
    require_completed_activity: bool = True
    policy_version: str = '1.0'

class BadgeAwardResp(BaseModel):
    id: str
    badge_id: str
    badge_name: str
    description: str
    category: str
    image_url: Optional[str] = None
    awarded_at: datetime
    status: str
    qualification_count: int
    qualifying_activities: List[str] = []
    policy_version: str

class BadgeReconcileResp(BaseModel):
    evaluated_users: int
    newly_awarded: int
    already_awarded: int
