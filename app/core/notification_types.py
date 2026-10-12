from enum import Enum
from typing import Dict, Any

class NotificationType(str, Enum):
    REGISTRATION_CONFIRMATION = "REGISTRATION_CONFIRMATION"
    TICKET_CONFIRMATION = "TICKET_CONFIRMATION"
    PAYMENT_CONFIRMATION = "PAYMENT_CONFIRMATION"
    EVENT_REMINDER = "EVENT_REMINDER"
    VOTING_REMINDER = "VOTING_REMINDER"
    LEADERBOARD_UPDATE = "LEADERBOARD_UPDATE"
    COMPETITION_RESULT = "COMPETITION_RESULT"
    CERTIFICATE_AVAILABLE = "CERTIFICATE_AVAILABLE"
    REWARD_COIN_UPDATE = "REWARD_COIN_UPDATE"
    COURSE_WORKSHOP_UPDATE = "COURSE_WORKSHOP_UPDATE"
    ORDER_UPDATE = "ORDER_UPDATE"
    IMPORTANT_ANNOUNCEMENT = "IMPORTANT_ANNOUNCEMENT"

class NotificationPriority(str, Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    URGENT = "URGENT"

class NotificationChannel(str, Enum):
    IN_APP = "IN_APP"
    WEBSOCKET = "WEBSOCKET"
    PUSH = "PUSH"
    EMAIL = "EMAIL"

class DeliveryStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    ACCEPTED = "ACCEPTED"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    RETRY_SCHEDULED = "RETRY_SCHEDULED"
    CANCELLED = "CANCELLED"

class SoundCategory(str, Enum):
    DEFAULT = "DEFAULT"
    SUCCESS = "SUCCESS"
    ALERT = "ALERT"
    TICKET = "TICKET"
    COINS = "COINS"
    ANNOUNCEMENT = "ANNOUNCEMENT"
    NONE = "NONE"

class AudienceType(str, Enum):
    ALL = "ALL"
    EVENT_REGISTRANTS = "EVENT_REGISTRANTS"
    COMPETITION_REGISTRANTS = "COMPETITION_REGISTRANTS"
    COURSE_ENROLLEES = "COURSE_ENROLLEES"
    EXPLICIT_USERS = "EXPLICIT_USERS"

class DevicePlatform(str, Enum):
    WEB = "WEB"
    ANDROID = "ANDROID"
    IOS = "IOS"

def to_enum_val(val: Any) -> str:
    if val is None:
        return ""
    if hasattr(val, "value"):
        return str(val.value)
    s = str(val)
    if "." in s:
        return s.split(".", 1)[1]
    return s


# Default metadata and templates for each notification type
NOTIFICATION_TYPE_CONFIG: Dict[NotificationType, Dict[str, Any]] = {
    NotificationType.REGISTRATION_CONFIRMATION: {
        "title": "Registration Confirmed",
        "default_priority": NotificationPriority.HIGH,
        "sound_category": SoundCategory.SUCCESS,
        "is_transactional": True,
        "default_resource_type": "registration",
    },
    NotificationType.TICKET_CONFIRMATION: {
        "title": "Ticket Confirmed",
        "default_priority": NotificationPriority.HIGH,
        "sound_category": SoundCategory.TICKET,
        "is_transactional": True,
        "default_resource_type": "ticket",
    },
    NotificationType.PAYMENT_CONFIRMATION: {
        "title": "Payment Successful",
        "default_priority": NotificationPriority.HIGH,
        "sound_category": SoundCategory.SUCCESS,
        "is_transactional": True,
        "default_resource_type": "payment",
    },
    NotificationType.EVENT_REMINDER: {
        "title": "Upcoming Event Reminder",
        "default_priority": NotificationPriority.NORMAL,
        "sound_category": SoundCategory.ALERT,
        "is_transactional": False,
        "default_resource_type": "event",
    },
    NotificationType.VOTING_REMINDER: {
        "title": "Voting Window Open",
        "default_priority": NotificationPriority.NORMAL,
        "sound_category": SoundCategory.ALERT,
        "is_transactional": False,
        "default_resource_type": "competition",
    },
    NotificationType.LEADERBOARD_UPDATE: {
        "title": "Leaderboard Updated",
        "default_priority": NotificationPriority.NORMAL,
        "sound_category": SoundCategory.DEFAULT,
        "is_transactional": False,
        "default_resource_type": "leaderboard",
    },
    NotificationType.COMPETITION_RESULT: {
        "title": "Competition Results Published",
        "default_priority": NotificationPriority.HIGH,
        "sound_category": SoundCategory.SUCCESS,
        "is_transactional": False,
        "default_resource_type": "competition_result",
    },
    NotificationType.CERTIFICATE_AVAILABLE: {
        "title": "Certificate Ready",
        "default_priority": NotificationPriority.HIGH,
        "sound_category": SoundCategory.SUCCESS,
        "is_transactional": False,
        "default_resource_type": "certificate",
    },
    NotificationType.REWARD_COIN_UPDATE: {
        "title": "Reward Coins Updated",
        "default_priority": NotificationPriority.NORMAL,
        "sound_category": SoundCategory.COINS,
        "is_transactional": True,
        "default_resource_type": "wallet",
    },
    NotificationType.COURSE_WORKSHOP_UPDATE: {
        "title": "Course / Workshop Update",
        "default_priority": NotificationPriority.NORMAL,
        "sound_category": SoundCategory.DEFAULT,
        "is_transactional": False,
        "default_resource_type": "course",
    },
    NotificationType.ORDER_UPDATE: {
        "title": "Order Status Update",
        "default_priority": NotificationPriority.NORMAL,
        "sound_category": SoundCategory.DEFAULT,
        "is_transactional": True,
        "default_resource_type": "order",
    },
    NotificationType.IMPORTANT_ANNOUNCEMENT: {
        "title": "Important Announcement",
        "default_priority": NotificationPriority.URGENT,
        "sound_category": SoundCategory.ANNOUNCEMENT,
        "is_transactional": False,
        "default_resource_type": "announcement",
    },
}
