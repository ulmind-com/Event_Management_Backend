import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_current_user
from app.core.notification_types import NotificationType, to_enum_val
from app.schemas.notification import (
    NotificationResponse,
    NotificationListResponse,
    UnreadCountResponse,
    MarkReadResponse,
    NotificationPreferencesUpdate,
    NotificationPreferencesResponse,
    DeviceRegisterRequest,
    DeviceResponse,
    DeviceListResponse,
)
from app.services.notification_service import NotificationService

logger = logging.getLogger("eventora.routes.notifications")

router = APIRouter()

# ── Static / Collection Endpoints ──

@router.get("", response_model=NotificationListResponse, summary="List notifications")
async def list_notifications(
    is_read: Optional[bool] = Query(None, description="Filter by read status"),
    notification_type: Optional[NotificationType] = Query(None, description="Filter by notification type"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    current_user: dict = Depends(get_current_user),
):
    """
    Returns paginated notifications for the authenticated user, ordered from newest to oldest.
    """
    user_email = current_user["email"]
    type_str = to_enum_val(notification_type) if notification_type else None
    return await NotificationService.get_user_notifications(
        user_email=user_email,
        is_read=is_read,
        notification_type=type_str,
        page=page,
        page_size=page_size,
    )

@router.get("/unread-count", response_model=UnreadCountResponse, summary="Get unread notification count")
async def get_unread_count(
    current_user: dict = Depends(get_current_user),
):
    """
    Returns the total count of unread notifications for the authenticated user.
    """
    user_email = current_user["email"]
    count = await NotificationService.get_unread_count(user_email)
    return UnreadCountResponse(unread_count=count)

@router.patch("/read-all", response_model=MarkReadResponse, summary="Mark all notifications as read")
async def mark_all_as_read(
    current_user: dict = Depends(get_current_user),
):
    """
    Marks all notifications for the authenticated user as read.
    """
    user_email = current_user["email"]
    count = await NotificationService.mark_all_as_read(user_email)
    return MarkReadResponse(success=True, modified_count=count, message=f"{count} notifications marked as read")

# ── Preferences Endpoints ──

@router.get("/preferences", response_model=NotificationPreferencesResponse, summary="Get notification preferences")
async def get_preferences(
    current_user: dict = Depends(get_current_user),
):
    """
    Returns user notification preferences. Defaults to all channels and topics enabled.
    """
    user_email = current_user["email"]
    return await NotificationService.get_preferences(user_email)

@router.patch("/preferences", response_model=NotificationPreferencesResponse, summary="Update notification preferences")
async def update_preferences(
    prefs: NotificationPreferencesUpdate,
    current_user: dict = Depends(get_current_user),
):
    """
    Updates user notification preferences.
    """
    user_email = current_user["email"]
    return await NotificationService.update_preferences(user_email, prefs)

# ── Device Push Token Endpoints ──

@router.post("/devices", response_model=DeviceResponse, summary="Register push device token")
async def register_device(
    req: DeviceRegisterRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Registers or updates an FCM, APNs, or Web Push device token for the authenticated user.
    """
    user_email = current_user["email"]
    return await NotificationService.register_device(user_email, req)

@router.get("/devices", response_model=DeviceListResponse, summary="List registered devices")
async def list_devices(
    current_user: dict = Depends(get_current_user),
):
    """
    Lists active devices registered for the authenticated user.
    """
    user_email = current_user["email"]
    devices = await NotificationService.get_user_devices(user_email)
    return DeviceListResponse(devices=devices, total=len(devices))

@router.delete("/devices/{device_id}", response_model=MarkReadResponse, summary="Unregister push device token")
async def unregister_device(
    device_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Deactivates a device token upon logout.
    """
    user_email = current_user["email"]
    success = await NotificationService.unregister_device(device_id, user_email)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device token not found or access denied")
    return MarkReadResponse(success=True, modified_count=1, message="Device unregistered successfully")

# ── Parameterized Notification Item Endpoints ──

@router.get("/{notification_id}", response_model=NotificationResponse, summary="Get notification by ID")
async def get_notification(
    notification_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Returns single notification details, strictly isolated to the authenticated user.
    """
    user_email = current_user["email"]
    notif = await NotificationService.get_notification_by_id(notification_id, user_email)
    if not notif:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
    return notif

@router.patch("/{notification_id}/read", response_model=MarkReadResponse, summary="Mark notification as read")
async def mark_as_read(
    notification_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Marks a notification as read. Idempotent operation.
    """
    user_email = current_user["email"]
    success = await NotificationService.mark_as_read(notification_id, user_email)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found or access denied")
    return MarkReadResponse(success=True, modified_count=1, message="Notification marked as read")

@router.patch("/{notification_id}/unread", response_model=MarkReadResponse, summary="Mark notification as unread")
async def mark_as_unread(
    notification_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Marks a notification as unread. Idempotent operation.
    """
    user_email = current_user["email"]
    success = await NotificationService.mark_as_unread(notification_id, user_email)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found or access denied")
    return MarkReadResponse(success=True, modified_count=1, message="Notification marked as unread")

@router.delete("/{notification_id}", response_model=MarkReadResponse, summary="Delete notification")
async def delete_notification(
    notification_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Deletes a notification record for the user. Does NOT touch underlying business or financial records.
    """
    user_email = current_user["email"]
    deleted = await NotificationService.delete_notification(notification_id, user_email)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found or access denied")
    return MarkReadResponse(success=True, modified_count=1, message="Notification deleted successfully")
