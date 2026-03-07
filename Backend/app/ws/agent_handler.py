"""
Agent WebSocket Handler — /ws/agent

Agents connect here with their token. On connect:
  - Validates agent_token against DB
  - Marks server online
  - Registers in connected_agents dict

On messages:
  - register → update server hostname/IP/OS
  - telemetry → update server telemetry fields + forward to browser watchers
  - scan → update server service cache + forward to watchers
  - command response → resolve pending command future
"""
import json
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select, update

from app.database import async_session_factory
from app.models.server import Server

logger = logging.getLogger("serverdeck.ws.agent")
router = APIRouter()

# In-memory state
# Maps agent_token → WebSocket connection
connected_agents: dict[str, WebSocket] = {}

# Maps server_id → WebSocket (for routing commands to agents)
agent_by_server_id: dict[str, WebSocket] = {}

# Maps server_id → agent_token (for reverse lookup)
server_token_map: dict[str, str] = {}


@router.websocket("/ws/agent")
async def agent_websocket(websocket: WebSocket):
    """Handle agent WebSocket connections."""
    await websocket.accept()

    # Extract token from query param or header
    token = websocket.query_params.get("token")
    if not token:
        auth_header = websocket.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]

    if not token:
        await websocket.send_json({"error": "No authentication token provided"})
        await websocket.close(code=4001)
        return
        
    token = token.strip()

    # Verify token against DB
    async with async_session_factory() as db:
        result = await db.execute(select(Server).where(Server.agent_token == token))
        server = result.scalar_one_or_none()
        if not server:
            await websocket.send_json({"error": "Invalid agent token"})
            await websocket.close(code=4003)
            return

        server_id = str(server.id)

        # Mark online
        await db.execute(
            update(Server).where(Server.id == server.id).values(
                is_online=True,
                last_seen=datetime.now(timezone.utc),
            )
        )
        await db.commit()

    # Register in connected dicts
    connected_agents[token] = websocket
    agent_by_server_id[server_id] = websocket
    server_token_map[server_id] = token
    logger.info(f"Agent connected: server_id={server_id}")

    try:
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            msg_type = data.get("type")

            if msg_type == "register":
                await _handle_register(server_id, data.get("data", {}))
            elif msg_type == "telemetry":
                await _handle_telemetry(server_id, data.get("data", {}))
            elif msg_type == "scan":
                await _handle_scan(server_id, data.get("data", {}))
            elif msg_type in ("stream_chunk", "stream_ended"):
                # Forward log streams directly to watchers
                from app.ws.client_handler import forward_to_watchers
                await forward_to_watchers(server_id, {
                    "type": msg_type,
                    "server_id": server_id,
                    "id": data.get("id"),
                    "chunk": data.get("chunk"),
                })
            else:
                # Command response — resolve pending future
                cmd_id = data.get("id")
                if cmd_id:
                    from app.services.command_bridge import resolve_command
                    resolve_command(cmd_id, data)

    except WebSocketDisconnect:
        logger.info(f"Agent disconnected: server_id={server_id}")
    except Exception as e:
        logger.error(f"Agent WS error: {e}")
    finally:
        # Clean up
        connected_agents.pop(token, None)
        agent_by_server_id.pop(server_id, None)
        server_token_map.pop(server_id, None)

        # Mark offline
        async with async_session_factory() as db:
            await db.execute(
                update(Server).where(Server.id == server_id).values(
                    is_online=False,
                    last_seen=datetime.now(timezone.utc),
                )
            )
            await db.commit()


async def _handle_register(server_id: str, data: dict):
    """Update server info from agent registration."""
    async with async_session_factory() as db:
        await db.execute(
            update(Server).where(Server.id == server_id).values(
                hostname=data.get("hostname"),
                ip_address=data.get("ip_address"),
                os_info=data.get("os_info"),
                ram_total_mb=data.get("ram_total_mb"),
            )
        )
        await db.commit()


async def _handle_telemetry(server_id: str, data: dict):
    """Update server telemetry and forward to watching browser clients."""
    async with async_session_factory() as db:
        await db.execute(
            update(Server).where(Server.id == server_id).values(
                cpu_percent=data.get("cpu_percent"),
                ram_used_mb=data.get("ram_used_mb"),
                ram_total_mb=data.get("ram_total_mb"),
                disk_used_percent=data.get("disk_used_percent"),
                uptime_seconds=data.get("uptime_seconds"),
                last_seen=datetime.now(timezone.utc),
            )
        )
        await db.commit()

    # Forward to browser clients watching this server
    from app.ws.client_handler import forward_to_watchers
    await forward_to_watchers(server_id, {"type": "telemetry", "server_id": server_id, "data": data})


async def _handle_scan(server_id: str, data: dict):
    """Update server service cache and forward to watchers."""
    async with async_session_factory() as db:
        await db.execute(
            update(Server).where(Server.id == server_id).values(
                nginx_sites=data.get("nginx_sites"),
                pm2_apps=data.get("pm2_apps"),
                systemd_services=data.get("systemd_services"),
                ssl_certs=data.get("ssl_certs"),
                last_seen=datetime.now(timezone.utc),
            )
        )
        await db.commit()

    from app.ws.client_handler import forward_to_watchers
    await forward_to_watchers(server_id, {"type": "scan", "server_id": server_id, "data": data})
