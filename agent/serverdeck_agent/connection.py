"""
WebSocket Connection — handles connection to portal with auto-reconnect.
"""
import asyncio
import json
import logging
import websockets
from serverdeck_agent.config import AgentConfig

logger = logging.getLogger("serverdeck.agent.connection")


class AgentConnection:
    def __init__(self, config: AgentConfig, command_handler):
        self.config = config
        self.command_handler = command_handler
        self.ws = None
        self.connected = False
        self._backoff = 5
        self._max_backoff = 60

    async def connect(self):
        """Connect to portal with auto-reconnect and exponential backoff."""
        while True:
            try:
                extra_headers = {"Authorization": f"Bearer {self.config.agent_token}"}
                uri = f"{self.config.portal_url}?token={self.config.agent_token}"

                async with websockets.connect(
                    uri,
                    additional_headers=extra_headers,
                    ping_interval=self.config.ping_interval,
                    ping_timeout=self.config.ping_timeout,
                    max_size=10 * 1024 * 1024,  # 10MB
                ) as ws:
                    self.ws = ws
                    self.connected = True
                    self._backoff = 5  # Reset backoff on success
                    logger.info("Connected to portal")

                    # Send registration
                    from serverdeck_agent.system_info import get_registration_data
                    reg_data = get_registration_data()
                    await self.send({"type": "register", "data": reg_data})

                    # Start telemetry and scan loops
                    await asyncio.gather(
                        self._listen(),
                        self._telemetry_loop(),
                        self._scan_loop(),
                    )

            except (websockets.ConnectionClosed, ConnectionRefusedError, OSError) as e:
                logger.warning(f"Disconnected: {e}. Reconnecting in {self._backoff}s...")
                self.connected = False
                self.ws = None
                await asyncio.sleep(self._backoff)
                self._backoff = min(self._backoff * 2, self._max_backoff)
            except Exception as e:
                logger.error(f"Unexpected error: {e}. Reconnecting in {self._backoff}s...")
                self.connected = False
                self.ws = None
                await asyncio.sleep(self._backoff)
                self._backoff = min(self._backoff * 2, self._max_backoff)

    async def send(self, data: dict):
        """Send JSON message to portal."""
        if self.ws and self.connected:
            await self.ws.send(json.dumps(data))

    async def _listen(self):
        """Listen for incoming commands from portal."""
        async for message in self.ws:
            try:
                data = json.loads(message)
                cmd_id = data.get("id")
                action = data.get("action")
                params = data.get("params", {})

                if action:
                    # Process command
                    result = await self.command_handler(action, params)
                    response = {"id": cmd_id, **result}
                    if "error" in result:
                        response["status"] = "error"
                    else:
                        response["status"] = "success"
                        response["data"] = result
                    await self.send(response)

            except json.JSONDecodeError:
                logger.error(f"Invalid JSON received: {message[:100]}")
            except Exception as e:
                logger.error(f"Error processing command: {e}")
                if cmd_id:
                    await self.send({"id": cmd_id, "status": "error", "error": str(e)})

    async def _telemetry_loop(self):
        """Send telemetry data at regular intervals."""
        from serverdeck_agent.system_info import get_telemetry_data
        while self.connected:
            try:
                data = get_telemetry_data()
                await self.send({"type": "telemetry", "data": data})
            except Exception as e:
                logger.error(f"Telemetry error: {e}")
            await asyncio.sleep(self.config.telemetry_interval)

    async def _scan_loop(self):
        """Send service scan data at regular intervals."""
        from serverdeck_agent.system_info import get_scan_data
        while self.connected:
            try:
                data = await get_scan_data()
                await self.send({"type": "scan", "data": data})
            except Exception as e:
                logger.error(f"Scan error: {e}")
            await asyncio.sleep(self.config.scan_interval)
