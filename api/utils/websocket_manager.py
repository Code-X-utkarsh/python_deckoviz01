from fastapi import WebSocket
from typing import Dict, Set
from core.logger import logger

# Store active WebSocket connections
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, Set[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, room_id: str, is_accepted: bool = False):
        logger.debug(f"Managing websocket connection for room {room_id}")
        # Only accept the connection if it hasn't been accepted yet
        if not is_accepted:
            await websocket.accept()
        
        # Add to active connections
        if room_id not in self.active_connections:
            self.active_connections[room_id] = set()
        self.active_connections[room_id].add(websocket)

    def disconnect(self, websocket: WebSocket, room_id: str):
        if room_id in self.active_connections:
            self.active_connections[room_id].discard(websocket)
            if not self.active_connections[room_id]:
                del self.active_connections[room_id]

    async def send_to_client(self, websocket: WebSocket, message: dict) -> bool:
        """Send a message to a single client. Returns True if successful."""
        try:
            await websocket.send_json(message)
            return True
        except Exception as e:
            logger.debug(f"Failed to send to client: {e}")
            return False

    async def broadcast(self, room_id: str, message: dict, exclude: WebSocket = None):
        """Broadcast message to all clients in a room, optionally excluding one."""
        if room_id not in self.active_connections:
            return
        
        # Copy the set to avoid mutation during iteration
        dead_connections = []
        for connection in list(self.active_connections.get(room_id, set())):
            if exclude and connection == exclude:
                continue
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.debug(f"Failed to send to client in room {room_id}: {e}")
                dead_connections.append(connection)
        
        # Clean up dead connections after iteration
        for dead in dead_connections:
            self.disconnect(dead, room_id)
            logger.debug(f"Removed dead connection from room {room_id}")

# Create a singleton instance of the connection manager
manager = ConnectionManager()
