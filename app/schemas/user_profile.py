from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime

class UserBasicInfo(BaseModel):
    id: str
    name: str
    email: str
    phone: Optional[str] = None
    role: str = 'USER'
    account_status: str = 'ACTIVE'
    created_at: Optional[datetime] = None

class ActivitySummary(BaseModel):
    total_event_registrations: int = 0
    total_distinct_events_participated: int = 0
    total_events_completed: int = 0
    total_upcoming_registrations: int = 0
    total_cancelled_registrations: int = 0
    total_competitions_registered: int = 0
    total_competitions_participated: int = 0
    total_competitions_completed: int = 0
    total_courses_registered: int = 0
    total_courses_completed: int = 0
    total_workshops_registered: int = 0
    total_workshops_completed: int = 0

class AchievementsSummary(BaseModel):
    total_certificates: int = 0
    total_participation_certificates: int = 0
    total_award_certificates: int = 0
    total_course_certificates: int = 0
    total_workshop_certificates: int = 0
    total_valid_certificates: int = 0
    total_revoked_certificates: int = 0
    total_badges: int = 0
    total_active_badges: int = 0
    top_participation_badge_earned: bool = False

class UserProfileSummaryResp(BaseModel):
    user: UserBasicInfo
    activity_summary: ActivitySummary
    achievements: AchievementsSummary

class UserActivityTimelineItem(BaseModel):
    activity_type: str
    entity_id: str
    entity_title: str
    timestamp: datetime
    status: str
    metadata: Dict[str, Any] = {}
