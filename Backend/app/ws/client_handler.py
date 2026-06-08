"""
Client WebSocket Handler — /ws/client

Browser clients connect here with JWT. Handles:
  - watch/unwatch server (subscribe to real-time updates)
  - command (sends command to agent, awaits response)
  - terminal_open / terminal_input / terminal_resize / terminal_close
"""
import json
import logging
import uuid
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from jose import JWTError, jwt

from app.config import get_settings
from app.services.command_bridge import send_command_to_agent
from app.services.audit import record_audit
from app.database import async_session_factory

logger = logging.getLogger("serverdeck.ws.client")
router = APIRouter()
settings = get_settings()

# Maps server_id → set of WebSocket connections watching that server
server_watchers: dict[str, set[WebSocket]] = {}

# Maps websocket → set of server_ids being watched (for cleanup)
client_watches: dict[WebSocket, set[str]] = {}

# Maps ticket_id → set of WebSocket connections watching that ticket
ticket_watchers: dict[str, set[WebSocket]] = {}

# Maps websocket → set of ticket_ids being watched (for cleanup)
client_ticket_watches: dict[WebSocket, set[str]] = {}

# Maps stream_id (cmd_id used for logs.stream / terminal sessions) → WebSocket.
# This guarantees streaming output is delivered to the originator regardless
# of whether they have an active "watch" subscription on the server.
stream_subscribers: dict[str, WebSocket] = {}

# Maps websocket → set of stream_ids subscribed (for cleanup)
client_streams: dict[WebSocket, set[str]] = {}


def _subscribe_stream(ws: WebSocket, stream_id: str):
    stream_subscribers[stream_id] = ws
    client_streams.setdefault(ws, set()).add(stream_id)


def _unsubscribe_stream(stream_id: str):
    ws = stream_subscribers.pop(stream_id, None)
    if ws is not None:
        streams = client_streams.get(ws)
        if streams is not None:
            streams.discard(stream_id)


async def _send_agent_fire_and_forget(server_id: str, action: str, params: dict):
    """Send a command to the agent without awaiting a response."""
    from app.ws.agent_handler import agent_by_server_id

    ws = agent_by_server_id.get(server_id)
    if not ws:
        raise ConnectionError(f"Server {server_id} is not connected")

    await ws.send_text(json.dumps({
        "id": str(uuid.uuid4()),
        "action": action,
        "params": params,
    }))


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
        schema_name = payload.get("tenant_schema")
        is_platform_owner = payload.get("is_platform_owner", False)
        
        if not user_id or not schema_name:
            raise JWTError("Missing claims")
            
        if not is_platform_owner and not team_id:
            raise JWTError("Missing team_id for standard user")
    except JWTError:
        await websocket.send_json({"error": "Invalid token"})
        await websocket.close(code=4003)
        return

    from app.database import tenant_schema
    tenant_schema.set(schema_name)

    client_watches[websocket] = set()
    client_streams[websocket] = set()
    client_ticket_watches[websocket] = set()
    logger.info(f"Client connected: user_id={user_id} tenant={schema_name}")

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

            elif msg_type == "subscribe_ticket":
                ticket_id = data.get("ticket_id")
                if ticket_id:
                    if ticket_id not in ticket_watchers:
                        ticket_watchers[ticket_id] = set()
                    ticket_watchers[ticket_id].add(websocket)
                    client_ticket_watches[websocket].add(ticket_id)
                    await websocket.send_json({"type": "subscribed_ticket", "ticket_id": ticket_id})

            elif msg_type == "unsubscribe_ticket":
                ticket_id = data.get("ticket_id")
                if ticket_id and ticket_id in ticket_watchers:
                    ticket_watchers[ticket_id].discard(websocket)
                    client_ticket_watches[websocket].discard(ticket_id)
                    await websocket.send_json({"type": "unsubscribed_ticket", "ticket_id": ticket_id})

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

                # Subscribe the originator to streaming output before dispatch,
                # so chunks arriving faster than the response are not dropped.
                if action == "logs.stream":
                    _subscribe_stream(websocket, cmd_id)

                try:
                    result = await send_command_to_agent(
                        server_id=server_id,
                        action=action,
                        params=params,
                        cmd_id=cmd_id,
                        timeout=30,
                    )
                    await websocket.send_json(result)
                    
                    if action:
                        from app.database import tenant_session
                        async with tenant_session() as session:
                            await record_audit(session, user_id, server_id, action, params)
                            await session.commit()

                except TimeoutError:
                    if action == "logs.stream":
                        _unsubscribe_stream(cmd_id)
                    await websocket.send_json({
                        "id": cmd_id,
                        "status": "error",
                        "error": "Command timed out (30s)",
                    })
                except Exception as e:
                    if action == "logs.stream":
                        _unsubscribe_stream(cmd_id)
                    await websocket.send_json({
                        "id": cmd_id,
                        "status": "error",
                        "error": str(e),
                    })

            elif msg_type == "terminal_open":
                server_id = data.get("server_id")
                cols = data.get("cols", 80)
                rows = data.get("rows", 24)
                shell = data.get("shell", "/bin/bash")
                cmd_id = data.get("id") or str(uuid.uuid4())

                _subscribe_stream(websocket, cmd_id)
                try:
                    result = await send_command_to_agent(
                        server_id=server_id,
                        action="terminal.open",
                        params={
                            "session_id": cmd_id,
                            "cols": cols,
                            "rows": rows,
                            "shell": shell,
                        },
                        cmd_id=cmd_id,
                        timeout=15,
                    )
                    result.setdefault("type", "terminal_opened")
                    await websocket.send_json(result)

                    from app.database import tenant_session
                    async with tenant_session() as session:
                        await record_audit(session, user_id, server_id, "terminal.open", {"shell": shell})
                        await session.commit()

                except Exception as e:
                    _unsubscribe_stream(cmd_id)
                    await websocket.send_json({
                        "id": cmd_id,
                        "type": "terminal_error",
                        "status": "error",
                        "error": str(e),
                    })

            elif msg_type == "terminal_input":
                server_id = data.get("server_id")
                session_id = data.get("session_id")
                input_data = data.get("data", "")
                if server_id and session_id:
                    try:
                        await _send_agent_fire_and_forget(
                            server_id,
                            "terminal.input",
                            {"session_id": session_id, "data": input_data},
                        )
                    except ConnectionError as e:
                        await websocket.send_json({
                            "type": "terminal_error",
                            "id": session_id,
                            "error": str(e),
                        })

            elif msg_type == "terminal_resize":
                server_id = data.get("server_id")
                session_id = data.get("session_id")
                cols = data.get("cols", 80)
                rows = data.get("rows", 24)
                if server_id and session_id:
                    try:
                        await _send_agent_fire_and_forget(
                            server_id,
                            "terminal.resize",
                            {"session_id": session_id, "cols": cols, "rows": rows},
                        )
                    except ConnectionError:
                        pass

            elif msg_type == "terminal_close":
                server_id = data.get("server_id")
                session_id = data.get("session_id")
                if session_id:
                    _unsubscribe_stream(session_id)
                if server_id and session_id:
                    try:
                        await _send_agent_fire_and_forget(
                            server_id,
                            "terminal.close",
                            {"session_id": session_id},
                        )
                    except ConnectionError:
                        pass

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

        # Clean up ticket watchers
        ticket_watched = client_ticket_watches.pop(websocket, set())
        for tid in ticket_watched:
            if tid in ticket_watchers:
                ticket_watchers[tid].discard(websocket)
                if not ticket_watchers[tid]:
                    del ticket_watchers[tid]

        # Clean up stream subscriptions (terminal sessions, log streams)
        streams = client_streams.pop(websocket, set())
        for stream_id in streams:
            stream_subscribers.pop(stream_id, None)


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


async def forward_to_stream(stream_id: str, message: dict) -> bool:
    """Forward a message to the client that initiated this stream/terminal session.

    Returns True if delivered, False if no subscriber was registered.
    """
    ws = stream_subscribers.get(stream_id)
    if ws is None:
        return False
    try:
        await ws.send_json(message)
        return True
    except Exception:
        stream_subscribers.pop(stream_id, None)
        return False


async def forward_to_ticket_watchers(ticket_id: str, message: dict):
    """Forward a message to all browser clients watching a ticket."""
    watchers = ticket_watchers.get(ticket_id, set())
    disconnected = set()
    for ws in watchers:
        try:
            await ws.send_json(message)
        except Exception:
            disconnected.add(ws)
    # Clean up disconnected
    for ws in disconnected:
        watchers.discard(ws)
