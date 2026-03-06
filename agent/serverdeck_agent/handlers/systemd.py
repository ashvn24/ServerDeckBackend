"""
Systemd Handler — manages systemd services.
"""
import logging
from pathlib import Path
from serverdeck_agent.utils import run_cmd

logger = logging.getLogger("serverdeck.agent.systemd")

UNIT_TEMPLATE = """[Unit]
Description={description}
After=network.target

[Service]
Type=simple
User={user}
WorkingDirectory={working_directory}
ExecStart={exec_start}
Restart=on-failure
RestartSec=5
Environment=NODE_ENV=production
{env_file_line}

[Install]
WantedBy=multi-user.target
"""


async def handle_list(params: dict) -> dict:
    """List user-relevant systemd services."""
    from serverdeck_agent.system_info import scan_systemd_services
    services = await scan_systemd_services()
    return {"services": services}


async def handle_start(params: dict) -> dict:
    name = params["name"]
    result = await run_cmd(f"systemctl start {name}", timeout=15)
    if result["returncode"] != 0:
        return {"error": result["stderr"]}
    return {"name": name, "status": "started"}


async def handle_stop(params: dict) -> dict:
    name = params["name"]
    result = await run_cmd(f"systemctl stop {name}", timeout=15)
    if result["returncode"] != 0:
        return {"error": result["stderr"]}
    return {"name": name, "status": "stopped"}


async def handle_restart(params: dict) -> dict:
    name = params["name"]
    result = await run_cmd(f"systemctl restart {name}", timeout=15)
    if result["returncode"] != 0:
        return {"error": result["stderr"]}
    return {"name": name, "status": "restarted"}


async def handle_enable(params: dict) -> dict:
    name = params["name"]
    result = await run_cmd(f"systemctl enable {name}", timeout=10)
    if result["returncode"] != 0:
        return {"error": result["stderr"]}
    return {"name": name, "status": "enabled"}


async def handle_disable(params: dict) -> dict:
    name = params["name"]
    result = await run_cmd(f"systemctl disable {name}", timeout=10)
    if result["returncode"] != 0:
        return {"error": result["stderr"]}
    return {"name": name, "status": "disabled"}


async def handle_create(params: dict) -> dict:
    """Create a new systemd service unit file."""
    name = params["name"]
    description = params.get("description", name)
    user = params.get("user", "root")
    working_directory = params.get("working_directory", "/opt")
    exec_start = params["exec_start"]
    env_file = params.get("env_file")

    env_file_line = f"EnvironmentFile={env_file}" if env_file else ""

    unit_content = UNIT_TEMPLATE.format(
        description=description,
        user=user,
        working_directory=working_directory,
        exec_start=exec_start,
        env_file_line=env_file_line,
    )

    # Write unit file
    unit_path = Path(f"/etc/systemd/system/{name}.service")
    unit_path.write_text(unit_content)

    # Reload, enable, start
    await run_cmd("systemctl daemon-reload", timeout=10)
    result = await run_cmd(f"systemctl enable {name}", timeout=10)
    if result["returncode"] != 0:
        return {"error": f"Enable failed: {result['stderr']}"}

    result = await run_cmd(f"systemctl start {name}", timeout=15)
    if result["returncode"] != 0:
        return {"error": f"Start failed: {result['stderr']}"}

    return {"name": name, "status": "created"}


async def handle_status(params: dict) -> dict:
    name = params["name"]
    result = await run_cmd(f"systemctl status {name}", timeout=10)
    return {"name": name, "output": result["stdout"] or result["stderr"]}
