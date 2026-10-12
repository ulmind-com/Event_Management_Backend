import json
import logging
from typing import Dict, List, Set, Optional, Any
from fastapi import WebSocket

logger = logging.getLogger("eventora.notification_ws")

class NotificationConnectionManager:
    """
    Manages authenticated WebSocket connections for user notifications.
    Supports multiple concurrent connections (tabs/devices) per user email.
    Single-instance in-memory design with clear hooks for Redis Pub/Sub in multi-worker setups.
    """
    def __init__(self):
        # Key: user_email, Value: set of connected WebSockets
        self.active_user_connections: Dict[str, Set[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, user_email: str):
        await websocket.accept()
        if user_email not in self.active_user_connections:
            self.active_user_connections[user_email] = set()
        self.active_user_connections[user_email].add(websocket)
        logger.info(f"User '{user_email}' connected to Notification WS. Total active sockets for user: {len(self.active_user_connections[user_email])}")

    def disconnect(self, websocket: WebSocket, user_email: str):
        if user_email in self.active_user_connections:
            self.active_user_connections[user_email].discard(websocket)
            if not self.active_user_connections[user_email]:
                del self.active_user_connections[user_email]
        logger.info(f"User '{user_email}' disconnected from Notification WS.")

    async def send_to_user(self, user_email: str, event: str, data: Dict[str, Any]) -> int:
        """
        Sends an event message to all active WebSocket connections of a specific user.
        Returns the count of sockets successfully messaged.
        """
        if user_email not in self.active_user_connections:
            return 0

        payload = json.dumps({"event": event, "data": data}, default=str)
        dead_sockets: List[WebSocket] = []
        sent_count = 0

        for ws in list(self.active_user_connections[user_email]):
            try:
                await ws.send_text(payload)
                sent_count += 1
            except Exception as e:
                logger.warning(f"Error sending WS notification to '{user_email}': {e}")
                dead_sockets.append(ws)

        for ws in dead_sockets:
            self.disconnect(ws, user_email)

        return sent_count

    async def broadcast_event(self, event: str, data: Dict[str, Any]) -> int:
        """
        Broadcasts an event message to ALL connected users (e.g. global announcements).
        """
        payload = json.dumps({"event": event, "data": data}, default=str)
        total_sent = 0
        for user_email, sockets in list(self.active_user_connections.items()):
            dead_sockets = []
            for ws in list(sockets):
                try:
                    await ws.send_text(payload)
                    total_sent += 1
                except Exception:
                    dead_sockets.append(ws)
            for ws in dead_sockets:
                self.disconnect(ws, user_email)
        return total_sent

    def is_user_connected(self, user_email: str) -> bool:
        return user_email in self.active_user_connections and len(self.active_user_connections[user_email]) > 0

    def get_connected_user_count(self) -> int:
        return len(self.active_user_connections)

# Global singleton
notification_ws_manager = NotificationConnectionManager()
