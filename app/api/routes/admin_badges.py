from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional, Dict, Any
from bson import ObjectId

from app.core.admin_auth import get_current_admin
from app.core.database import user_badge_award_collection, user_collection
from app.schemas.badge import BadgePolicyConfig
from app.services.badge_service import BadgeService

router = APIRouter()

@router.get('/config')
async def get_badge_policy_config(
    admin: dict = Depends(get_current_admin)
):
    """Retrieves current badge qualification policy and Top Participation threshold."""
    return await BadgeService.get_badge_policy()

@router.put('/config')
async def update_badge_policy_config(
    req: BadgePolicyConfig,
    admin: dict = Depends(get_current_admin)
):
    """Updates badge qualification policy and threshold."""
    return await BadgeService.save_badge_policy(req.model_dump())

@router.post('/reconcile')
async def reconcile_badges(
    admin: dict = Depends(get_current_admin)
):
    """
    Evaluates all users across the platform and awards Top Participation badges
    to eligible users idempotently.
    """
    return await BadgeService.reconcile_all_badges()

@router.get('/analytics')
async def get_badge_analytics(
    admin: dict = Depends(get_current_admin)
):
    """Analytics on total badges awarded, active, and top participation distribution."""
    total_awards = await user_badge_award_collection.count_documents({})
    active_awards = await user_badge_award_collection.count_documents({'status': 'ACTIVE'})
    revoked_awards = await user_badge_award_collection.count_documents({'status': 'REVOKED'})
    top_part_awards = await user_badge_award_collection.count_documents({'badge_id': 'TOP_PARTICIPATION', 'status': 'ACTIVE'})

    total_users = await user_collection.count_documents({})

    return {
        'total_badges_awarded': total_awards,
        'active_badges': active_awards,
        'revoked_badges': revoked_awards,
        'top_participation_badges_active': top_part_awards,
        'total_registered_users': total_users,
        'top_participation_coverage_percentage': round((top_part_awards / max(1, total_users)) * 100, 2)
    }

@router.get('/')
async def list_badge_awards(
    badge_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=200),
    skip: int = Query(default=0, ge=0),
    admin: dict = Depends(get_current_admin)
):
    """Paginated list of user badge awards."""
    query = {}
    if badge_id:
        query['badge_id'] = badge_id
    if status:
        query['status'] = status

    total = await user_badge_award_collection.count_documents(query)
    awards = await user_badge_award_collection.find(query).skip(skip).limit(limit).to_list(length=limit)
    for a in awards:
        a['id'] = str(a.pop('_id'))

    return {
        'total': total,
        'skip': skip,
        'limit': limit,
        'awards': awards
    }

@router.post('/user-badges/{badge_award_id}/revoke')
async def revoke_badge_award(
    badge_award_id: str,
    reason: str = Query(..., min_length=3),
    admin: dict = Depends(get_current_admin)
):
    """Revokes a user's badge award with audit trail."""
    admin_email = admin.get('email', 'superadmin@eventora.com')
    res = await BadgeService.revoke_badge_award(badge_award_id, reason, admin_email)
    if not res.get('success'):
        raise HTTPException(status_code=404, detail=res.get('message'))
    return res
