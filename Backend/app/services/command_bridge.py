"""
Command Bridge — sends commands to agents and awaits responses.

Used by:
  - Client WebSocket handler (for browser-initiated commands)
  - REST endpoints (for synchronous API calls like log fetching)
"""
import asyncio
import uuid
import json
import logging

logger = logging.getLogger("serverdeck.command_bridge")

# Maps cmd_id → asyncio.Future
pending_commands: dict[str, asyncio.Future] = {}


def resolve_command(cmd_id: str, result: dict):
    """Called by agent_handler when a command response is received."""
    future = pending_commands.pop(cmd_id, None)
    if future and not future.done():
        future.set_result(result)


async def send_command_to_agent(
    server_id: str,
    action: str,
    params: dict,
    cmd_id: str | None = None,
    timeout: int = 30,
) -> dict:
    """Send a command to an agent and wait for the response."""
    from app.ws.agent_handler import agent_by_server_id

    ws = agent_by_server_id.get(server_id)
    if not ws:
        raise ConnectionError(f"Server {server_id} is not connected")

    if not cmd_id:
        cmd_id = str(uuid.uuid4())

    # Create future for response
    loop = asyncio.get_event_loop()
    future = loop.create_future()
    pending_commands[cmd_id] = future

    # Send command to agent
    command = {
        "id": cmd_id,
        "action": action,
        "params": params,
    }
    await ws.send_text(json.dumps(command))

    try:
        result = await asyncio.wait_for(future, timeout=timeout)
        return result
    except asyncio.TimeoutError:
        pending_commands.pop(cmd_id, None)
        raise TimeoutError(f"Command {action} timed out after {timeout}s")


async def execute_on_server(
    server,
    action: str,
    params: dict,
    timeout: int = 30,
) -> dict:
    """Convenience wrapper for REST endpoints — takes a Server model object."""
    if not server.is_online:
        raise ConnectionError("Server is offline")

    return await send_command_to_agent(
        server_id=str(server.id),
        action=action,
        params=params,
        timeout=timeout,
    )
