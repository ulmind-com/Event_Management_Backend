import json
import logging
from typing import Optional
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, status

from app.core.security import decode_token
from app.core.database import notification_collection
from app.core.notification_websocket import notification_ws_manager

logger = logging.getLogger("eventora.routes.notification_ws")

router = APIRouter()

@router.websocket("/ws")
async def notification_websocket(
    websocket: WebSocket,
    token: Optional[str] = Query(None, description="User JWT access token"),
):
    """
    Authenticated real-time WebSocket connection for user notifications.
    Clients pass JWT access token via query string: /api/notifications/ws?token=<JWT>
    Emits real-time notification.created and notification.unread_count events.
    """
    if not token:
        # Check Authorization header as fallback
        auth_header = websocket.headers.get("authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:]

    if not token:
        logger.warning("Notification WS rejected: Missing authentication token")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    payload = decode_token(token)
    if not payload or not payload.get("sub"):
        logger.warning("Notification WS rejected: Invalid or expired token")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    user_email = payload["sub"]

    # Connect authenticated socket
    await notification_ws_manager.connect(websocket, user_email)

    try:
        # Send initial connection handshake with unread count
        unread_count = await notification_collection.count_documents({
            "recipient_user_id": user_email,
            "is_read": False
        })
        await websocket.send_text(json.dumps({
            "event": "connection.established",
            "data": {
                "user_email": user_email,
                "unread_count": unread_count,
                "sound_support": True
            }
        }))

        # Message loop for ping/pong heartbeat
        while True:
            data_text = await websocket.receive_text()
            try:
                msg = json.loads(data_text)
                if msg.get("event") == "ping":
                    await websocket.send_text(json.dumps({"event": "pong"}))
            except Exception:
                # Non-JSON or standard text heartbeat
                if data_text.strip().lower() == "ping":
                    await websocket.send_text("pong")

    except WebSocketDisconnect:
        notification_ws_manager.disconnect(websocket, user_email)
    except Exception as e:
        logger.warning(f"Notification WS exception for {user_email}: {e}")
        notification_ws_manager.disconnect(websocket, user_email)
