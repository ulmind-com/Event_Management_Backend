from fastapi import WebSocket
from typing import Dict, List, Set
import json
import logging

logger = logging.getLogger(__name__)

class ConnectionManager:
    """Manages WebSocket connections for real-time leaderboard updates."""
    
    def __init__(self):
        # Key: competition_id, Value: set of connected WebSockets
        self.active_connections: Dict[str, List[WebSocket]] = {}
    
    async def connect(self, websocket: WebSocket, competition_id: str):
        await websocket.accept()
        if competition_id not in self.active_connections:
            self.active_connections[competition_id] = []
        self.active_connections[competition_id].append(websocket)
        logger.info(f"WebSocket connected for competition {competition_id}. Total: {len(self.active_connections[competition_id])}")
    
    def disconnect(self, websocket: WebSocket, competition_id: str):
        if competition_id in self.active_connections:
            if websocket in self.active_connections[competition_id]:
                self.active_connections[competition_id].remove(websocket)
            if not self.active_connections[competition_id]:
                del self.active_connections[competition_id]
    
    async def broadcast_to_competition(self, competition_id: str, data: dict):
        """Broadcasts a message to ALL connected clients (Admin + User) for a competition."""
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

# Singleton instance
ws_manager = ConnectionManager()
