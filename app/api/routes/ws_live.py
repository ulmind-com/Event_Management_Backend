from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from app.core.websocket import ws_manager
from app.core.database import leaderboard_result_collection, judge_score_collection
from app.services.competition_stage_service import CompetitionStageService
import logging

logger = logging.getLogger(__name__)
router = APIRouter()

@router.websocket("/ws/competitions/{comp_id}/live")
async def websocket_live_scores(websocket: WebSocket, comp_id: str):
    """
    WebSocket endpoint for real-time score updates.
    Both Admin panels and User dashboards can connect to this.
    Every time a judge submits a score, all connected clients receive an update.
    """
    await ws_manager.connect(websocket, comp_id)
    
    # Send current state on connection
    try:
        pipeline = [
            {"$match": {"competition_id": comp_id}},
            {"$group": {
                "_id": "$submission_id",
                "total_score": {"$sum": "$total_score"},
                "judge_count": {"$sum": 1}
            }},
            {"$sort": {"total_score": -1}}
        ]
        current_scores = await judge_score_collection.aggregate(pipeline).to_list(length=1000)
        
        ranked = []
        for i, s in enumerate(current_scores):
            ranked.append({
                "rank": i + 1,
                "submission_id": s["_id"],
                "total_score": s["total_score"],
                "judge_count": s["judge_count"]
            })
        
        stage_state = await CompetitionStageService.get_public_stage_status(comp_id)

        await websocket.send_json({
            "event": "initial_state",
            "competition_id": comp_id,
            "leaderboard": ranked,
            "live_stage": stage_state
        })
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, comp_id)
        return
    except Exception as e:
        logger.error(f"Error sending initial state: {e}")
    
    # Keep connection alive
    try:
        while True:
            data = await websocket.receive_text()
            # Clients can send "ping" to keep alive
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, comp_id)

@router.websocket("/ws/competitions/{comp_id}/public")
async def websocket_public_leaderboard(websocket: WebSocket, comp_id: str):
    """
    Public WebSocket for users/audience watching the live leaderboard.
    Same broadcast channel as admin - everyone sees updates instantly.
    """
    await ws_manager.connect(websocket, comp_id)
    
    try:
        # Send published leaderboard or live scores
        results = await leaderboard_result_collection.find(
            {"competition_id": comp_id}
        ).sort("rank", 1).to_list(length=1000)
        
        for r in results:
            r["_id"] = str(r["_id"])
        
        stage_state = await CompetitionStageService.get_public_stage_status(comp_id)

        await websocket.send_json({
            "event": "initial_state",
            "competition_id": comp_id,
            "leaderboard": results,
            "live_stage": stage_state
        })
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, comp_id)
        return

    except Exception as e:
        logger.error(f"Error: {e}")
    
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, comp_id)
