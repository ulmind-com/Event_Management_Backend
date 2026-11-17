from fastapi import WebSocket
from typing import Dict, List, Set
import json
import logging
from app.core.realtime_bus import event_bus

logger = logging.getLogger(__name__)

class ConnectionManager:
    """Manages WebSocket connections for real-time competitions and event updates."""
    
    def __init__(self):
        # Key: competition_id or event_id, Value: list of connected WebSockets
        self.active_connections: Dict[str, List[WebSocket]] = {}
    
    async def connect(self, websocket: WebSocket, channel_id: str):
        await websocket.accept()
        if channel_id not in self.active_connections:
            self.active_connections[channel_id] = []
        self.active_connections[channel_id].append(websocket)
        logger.info(f"WebSocket connected for channel {channel_id}. Total: {len(self.active_connections[channel_id])}")
    
    def disconnect(self, websocket: WebSocket, channel_id: str):
        if channel_id in self.active_connections:
            if websocket in self.active_connections[channel_id]:
                self.active_connections[channel_id].remove(websocket)
            if not self.active_connections[channel_id]:
                del self.active_connections[channel_id]
    
    async def broadcast_to_competition(self, competition_id: str, data: dict):
        """Broadcasts a message to ALL connected clients (Admin + User) for a competition."""
        # Also bridge to RealtimeEventBus
        try:
            evt_name = data.get("event", "COMPETITION_UPDATE")
            await event_bus.publish(f"competition:{competition_id}", evt_name, data)
            await event_bus.publish("admin:competitions", evt_name, data)
        except Exception:
            pass

        if competition_id not in self.active_connections:
            return
        
        dead_connections = []
        message = json.dumps(data, default=str)
        
        for ws in self.active_connections[competition_id]:
            try:
                await ws.send_text(message)
            except Exception:
                dead_connections.append(ws)
        
        # Cleanup dead connections
        for ws in dead_connections:
            self.disconnect(ws, competition_id)

    async def broadcast_to_event(self, event_id: str, data: dict):
        """Broadcasts a message to ALL connected clients for an event."""
        try:
            evt_name = data.get("event", "EVENT_UPDATE")
            await event_bus.publish(f"event:{event_id}", evt_name, data)
            await event_bus.publish("admin:events", evt_name, data)
        except Exception:
            pass

        if event_id not in self.active_connections:
            return

        dead_connections = []
        message = json.dumps(data, default=str)

        for ws in self.active_connections[event_id]:
            try:
                await ws.send_text(message)
            except Exception:
                dead_connections.append(ws)

        for ws in dead_connections:
            self.disconnect(ws, event_id)

# Singleton instance
ws_manager = ConnectionManager()
