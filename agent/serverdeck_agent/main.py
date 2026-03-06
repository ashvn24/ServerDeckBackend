"""
ServerDeck Agent — Main Entry Point

Registers all command handlers and starts the WebSocket connection.
"""
import asyncio
import logging
import sys

from serverdeck_agent.config import load_config
from serverdeck_agent.connection import AgentConnection
from serverdeck_agent.handlers import nginx, systemd, pm2, ssl, logs, firewall, process

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("serverdeck.agent")

# Command action → handler mapping
HANDLERS = {
    # Nginx
    "nginx.list": nginx.handle_list,
    "nginx.create": nginx.handle_create,
    "nginx.delete": nginx.handle_delete,
    "nginx.enable": nginx.handle_enable,
    "nginx.disable": nginx.handle_disable,
    "nginx.get_config": nginx.handle_get_config,
    "nginx.update_config": nginx.handle_update_config,
    "nginx.test": nginx.handle_test,
    # Systemd
    "systemd.list": systemd.handle_list,
    "systemd.start": systemd.handle_start,
    "systemd.stop": systemd.handle_stop,
    "systemd.restart": systemd.handle_restart,
    "systemd.enable": systemd.handle_enable,
    "systemd.disable": systemd.handle_disable,
    "systemd.create": systemd.handle_create,
    "systemd.status": systemd.handle_status,
    # PM2
    "pm2.list": pm2.handle_list,
    "pm2.start": pm2.handle_start,
    "pm2.stop": pm2.handle_stop,
    "pm2.restart": pm2.handle_restart,
    "pm2.delete": pm2.handle_delete,
    "pm2.create": pm2.handle_create,
    # SSL
    "ssl.list": ssl.handle_list,
    "ssl.list_available": ssl.handle_list_available,
    "ssl.issue": ssl.handle_issue,
    "ssl.renew": ssl.handle_renew,
    # Logs
    "logs.fetch": logs.handle_fetch,
    "logs.stream": logs.handle_stream,
    # Firewall
    "firewall.list": firewall.handle_list,
    "firewall.allow": firewall.handle_allow,
    "firewall.deny": firewall.handle_deny,
    "firewall.delete": firewall.handle_delete,
    # Process
    "process.list": process.handle_list,
    "process.kill": process.handle_kill,
}

# Allowlist of valid actions (security: reject unknown commands)
ALLOWED_ACTIONS = set(HANDLERS.keys())


async def command_handler(action: str, params: dict) -> dict:
    """Route a command to the appropriate handler."""
    if action not in ALLOWED_ACTIONS:
        return {"error": f"Unknown action: {action}"}

    handler = HANDLERS[action]
    try:
        return await handler(params)
    except Exception as e:
        logger.error(f"Handler error for {action}: {e}")
        return {"error": str(e)}


async def main():
    config_path = None
    if len(sys.argv) > 1:
        config_path = sys.argv[1]

    try:
        config = load_config(config_path)
    except FileNotFoundError:
        logger.error("Config file not found. Run the installer first.")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Config error: {e}")
        sys.exit(1)

    logger.info(f"ServerDeck Agent starting — connecting to {config.portal_url}")
    connection = AgentConnection(config, command_handler)
    await connection.connect()


if __name__ == "__main__":
    asyncio.run(main())
