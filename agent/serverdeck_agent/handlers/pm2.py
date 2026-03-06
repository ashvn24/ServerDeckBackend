"""
PM2 Handler — manages PM2 applications.
"""
import json
import logging
import tempfile
from serverdeck_agent.utils import run_cmd

logger = logging.getLogger("serverdeck.agent.pm2")


async def handle_list(params: dict) -> dict:
    from serverdeck_agent.system_info import scan_pm2_apps
    apps = await scan_pm2_apps()
    return {"apps": apps}


async def handle_start(params: dict) -> dict:
    name = params["name"]
    result = await run_cmd(f"pm2 start {name}", timeout=15)
    if result["returncode"] != 0:
        return {"error": result["stderr"]}
    return {"name": name, "status": "started"}


async def handle_stop(params: dict) -> dict:
    name = params["name"]
    result = await run_cmd(f"pm2 stop {name}", timeout=15)
    if result["returncode"] != 0:
        return {"error": result["stderr"]}
    return {"name": name, "status": "stopped"}


async def handle_restart(params: dict) -> dict:
    name = params["name"]
    result = await run_cmd(f"pm2 restart {name}", timeout=15)
    if result["returncode"] != 0:
        return {"error": result["stderr"]}
    return {"name": name, "status": "restarted"}


async def handle_delete(params: dict) -> dict:
    name = params["name"]
    result = await run_cmd(f"pm2 delete {name}", timeout=15)
    if result["returncode"] != 0:
        return {"error": result["stderr"]}
    await run_cmd("pm2 save", timeout=10)
    return {"name": name, "status": "deleted"}


async def handle_create(params: dict) -> dict:
    """Create and start a new PM2 app from ecosystem config."""
    name = params["name"]
    script = params.get("script", "npm")
    cwd = params.get("cwd", "/opt")
    args = params.get("args", "start")
    interpreter = params.get("interpreter", "none")
    env = params.get("env", {})

    ecosystem = {
        "apps": [{
            "name": name,
            "script": script,
            "cwd": cwd,
            "args": args,
            "interpreter": interpreter,
            "env": {
                "NODE_ENV": "production",
                **env,
            },
        }]
    }

    # Write ecosystem config to temp file
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", prefix="pm2_eco_", delete=False, dir="/tmp"
    ) as f:
        json.dump(ecosystem, f)
        eco_path = f.name

    result = await run_cmd(f"pm2 start {eco_path}", timeout=30)
    if result["returncode"] != 0:
        return {"error": result["stderr"]}

    await run_cmd("pm2 save", timeout=10)
    return {"name": name, "status": "created"}
