from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from enum import Enum
from datetime import datetime


class AttendanceStatus(str, Enum):
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"


class AttendanceUpdateReq(BaseModel):
    is_present: bool = Field(True, description="True for PRESENT, False for ABSENT")
    attendance_status: Optional[str] = Field(None, description="'PRESENT' or 'ABSENT'")
    notes: Optional[str] = None


class BulkAttendanceUpdateReq(BaseModel):
    registration_ids: List[str] = Field(..., description="List of participant registration IDs to update")
    is_present: bool = Field(True, description="True to mark present, False to mark absent")
    attendance_status: Optional[str] = Field("PRESENT", description="'PRESENT' or 'ABSENT'")


class ParticipantAttendanceItem(BaseModel):
    registration_id: str
    registration_reference: str
    user_email: str
    user_name: str
    full_name: Optional[str] = None
    user_phone: Optional[str] = None
    profile_photo: Optional[str] = None
    category: Optional[str] = "General"
    stage_order: int = 0
    stage_status: str = "NOT_STARTED"
    is_current_performer: bool = False
    is_present: bool = False
    attendance_status: str = "ABSENT"
    attended_at: Optional[datetime] = None
    attended_by: Optional[str] = None
    is_verified: bool = False
    submission_id: Optional[str] = None
    registered_at: Optional[Any] = None


class CompetitionAttendanceDashboardResponse(BaseModel):
    competition_id: str
    competition_title: str
    is_offline: bool = False
    venue: Optional[str] = None
    total_registered: int
    present_count: int
    absent_count: int
    final_present_list: List[ParticipantAttendanceItem]
    all_participants: List[ParticipantAttendanceItem]


class EventAttendanceDashboardResponse(BaseModel):
    event_id: str
    event_title: str
    venue: Optional[str] = None
    total_registered: int
    present_count: int
    absent_count: int
    final_present_list: List[Dict[str, Any]]
    all_attendees: List[Dict[str, Any]]
