import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.admin_auth import get_current_admin
from app.schemas.notification import (
    AnnouncementCreate,
    AnnouncementUpdate,
    AnnouncementResponse,
    AnnouncementListResponse,
    NotificationAnalyticsResponse,
    MarkReadResponse,
)
from app.services.announcement_service import AnnouncementService
from app.services.notification_service import NotificationService

logger = logging.getLogger("eventora.routes.admin_notifications")

router = APIRouter()

# ── Admin Announcement Endpoints ──

@router.post("/announcements", response_model=AnnouncementResponse, summary="Create announcement")
async def create_announcement(
    req: AnnouncementCreate,
    current_admin: dict = Depends(get_current_admin),
):
    """
    Creates a new draft or scheduled announcement for a targeted or global audience.
    """
    admin_email = current_admin.get("email", "admin")
    return await AnnouncementService.create_announcement(admin_email, req)

@router.get("/announcements", response_model=AnnouncementListResponse, summary="List announcements")
async def list_announcements(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_admin: dict = Depends(get_current_admin),
):
    """
    Lists all announcements created by administrators.
    """
    return await AnnouncementService.list_announcements(page=page, page_size=page_size)

@router.get("/announcements/{announcement_id}", response_model=AnnouncementResponse, summary="Get announcement by ID")
async def get_announcement(
    announcement_id: str,
    current_admin: dict = Depends(get_current_admin),
):
    """
    Returns single announcement details.
    """
    doc = await AnnouncementService.get_announcement_by_id(announcement_id)
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Announcement not found")
    return doc

@router.patch("/announcements/{announcement_id}", response_model=AnnouncementResponse, summary="Update announcement")
async def update_announcement(
    announcement_id: str,
    req: AnnouncementUpdate,
    current_admin: dict = Depends(get_current_admin),
):
    """
    Updates a draft or scheduled announcement prior to publication.
    """
    updated = await AnnouncementService.update_announcement(announcement_id, req)
    if not updated:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot update announcement (not found or already published/cancelled)")
    return updated

@router.post("/announcements/{announcement_id}/publish", response_model=AnnouncementResponse, summary="Publish announcement")
async def publish_announcement(
    announcement_id: str,
    current_admin: dict = Depends(get_current_admin),
):
    """
    Publishes an announcement immediately, delivering it to all targeted users across configured channels.
    """
    admin_email = current_admin.get("email", "admin")
    published = await AnnouncementService.publish_announcement(announcement_id, admin_email)
    if not published:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot publish announcement (not found or already published)")
    return published

@router.post("/announcements/{announcement_id}/cancel", response_model=MarkReadResponse, summary="Cancel announcement")
async def cancel_announcement(
    announcement_id: str,
    current_admin: dict = Depends(get_current_admin),
):
    """
    Cancels a pending scheduled or draft announcement.
    """
    cancelled = await AnnouncementService.cancel_announcement(announcement_id)
    if not cancelled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot cancel announcement (not found or already published)")
    return MarkReadResponse(success=True, modified_count=1, message="Announcement cancelled successfully")

# ── Operational Analytics ──

@router.get("/analytics", response_model=NotificationAnalyticsResponse, summary="Get notification system analytics")
async def get_analytics(
    current_admin: dict = Depends(get_current_admin),
):
    """
    Returns operational metrics for the unified notification system.
    """
    return await NotificationService.get_analytics()
