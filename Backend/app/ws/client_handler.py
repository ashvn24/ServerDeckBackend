"""
Client WebSocket Handler — /ws/client

Browser clients connect here with JWT. Handles:
  - watch/unwatch server (subscribe to real-time updates)
  - command (sends command to agent, awaits response)
"""
import json
import logging
import uuid
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from jose import JWTError, jwt

from app.config import get_settings
from app.services.command_bridge import send_command_to_agent

logger = logging.getLogger("serverdeck.ws.client")
router = APIRouter()
settings = get_settings()

# Maps server_id → set of WebSocket connections watching that server
server_watchers: dict[str, set[WebSocket]] = {}

# Maps websocket → set of server_ids being watched (for cleanup)
client_watches: dict[WebSocket, set[str]] = {}


@router.websocket("/ws/client")
async def client_websocket(websocket: WebSocket):
    """Handle browser client WebSocket connections."""
    await websocket.accept()

    # Authenticate via JWT query param
    token = websocket.query_params.get("token")
    if not token:
        await websocket.send_json({"error": "No authentication token"})
        await websocket.close(code=4001)
        return

    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        user_id = payload.get("sub")
        team_id = payload.get("team_id")
        if not user_id or not team_id:
            raise JWTError("Missing claims")
    except JWTError:
        await websocket.send_json({"error": "Invalid token"})
        await websocket.close(code=4003)
        return

    client_watches[websocket] = set()
    logger.info(f"Client connected: user_id={user_id}")

    try:
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            msg_type = data.get("type")

            if msg_type == "watch":
                server_id = data.get("server_id")
                if server_id:
                    if server_id not in server_watchers:
                        server_watchers[server_id] = set()
                    server_watchers[server_id].add(websocket)
                    client_watches[websocket].add(server_id)
                    await websocket.send_json({"type": "watched", "server_id": server_id})

            elif msg_type == "unwatch":
                server_id = data.get("server_id")
                if server_id and server_id in server_watchers:
                    server_watchers[server_id].discard(websocket)
                    client_watches[websocket].discard(server_id)

            elif msg_type == "command":
                server_id = data.get("server_id")
                action = data.get("action")
                params = data.get("params", {})
                cmd_id = data.get("id") or str(uuid.uuid4())

                try:
                    result = await send_command_to_agent(
                        server_id=server_id,
                        action=action,
                        params=params,
                        cmd_id=cmd_id,
                        timeout=30,
                    )
                    await websocket.send_json(result)
                except TimeoutError:
                    await websocket.send_json({
                        "id": cmd_id,
                        "status": "error",
                        "error": "Command timed out (30s)",
                    })
                except Exception as e:
                    await websocket.send_json({
                        "id": cmd_id,
                        "status": "error",
                        "error": str(e),
                    })

    except WebSocketDisconnect:
        logger.info(f"Client disconnected: user_id={user_id}")
    except Exception as e:
        logger.error(f"Client WS error: {e}")
    finally:
        # Clean up watchers
        watched = client_watches.pop(websocket, set())
        for sid in watched:
            if sid in server_watchers:
                server_watchers[sid].discard(websocket)
                if not server_watchers[sid]:
                    del server_watchers[sid]


async def forward_to_watchers(server_id: str, message: dict):
    """Forward a message to all browser clients watching a server."""
    watchers = server_watchers.get(server_id, set())
    disconnected = set()
    for ws in watchers:
        try:
            await ws.send_json(message)
        except Exception:
            disconnected.add(ws)
    # Clean up disconnected
    for ws in disconnected:
        watchers.discard(ws)
