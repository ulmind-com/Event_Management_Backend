from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum

class CertificateType(str, Enum):
    PARTICIPATION = 'PARTICIPATION'
    AWARD = 'AWARD'
    COURSE_COMPLETION = 'COURSE_COMPLETION'
    WORKSHOP_COMPLETION = 'WORKSHOP_COMPLETION'

class CertificateStatus(str, Enum):
    VALID = 'VALID'
    REVOKED = 'REVOKED'

class DeliveryStatus(str, Enum):
    GENERATED = 'GENERATED'
    ISSUED = 'ISSUED'
    DELIVERY_PENDING = 'DELIVERY_PENDING'
    DELIVERED = 'DELIVERED'
    DELIVERY_FAILED = 'DELIVERY_FAILED'

class TieHandlingPolicy(str, Enum):
    OFFICIAL_RANKING = 'OFFICIAL_RANKING'
    INCLUDE_ALL_TIES = 'INCLUDE_ALL_TIES'
    STRICT_LIMIT = 'STRICT_LIMIT'

class AwardConfigReq(BaseModel):
    enabled: bool = True
    top_rank_limit: int = Field(default=3, ge=1, le=100)
    tie_handling: TieHandlingPolicy = TieHandlingPolicy.OFFICIAL_RANKING
    award_title_template: Optional[str] = 'Rank {rank} Winner'
    custom_titles: Optional[Dict[str, str]] = {
        '1': 'Champion',
        '2': '1st Runner Up',
        '3': '2nd Runner Up'
    }

class CertificateGenerateReq(BaseModel):
    notify_participants: bool = True
    custom_note: Optional[str] = None

class CertificateRevokeReq(BaseModel):
    reason: str = Field(..., min_length=3, max_length=500)

class CertificatePublicVerifyResp(BaseModel):
    verification_status: str
    certificate_id: Optional[str] = None
    certificate_type: Optional[str] = None
    participant_name: Optional[str] = None
    activity_title: Optional[str] = None
    activity_type: Optional[str] = None
    rank: Optional[int] = None
    award_title: Optional[str] = None
    issue_date: Optional[str] = None
    status: Optional[str] = None
