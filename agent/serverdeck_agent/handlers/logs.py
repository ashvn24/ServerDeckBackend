"""
Logs Handler — fetches logs from various sources.
"""
import logging
from serverdeck_agent.utils import run_cmd

logger = logging.getLogger("serverdeck.agent.logs")


async def handle_fetch(params: dict) -> dict:
    """Fetch logs from systemd, nginx, or pm2."""
    source = params.get("source", "systemd")
    name = params.get("name", "")
    lines = params.get("lines", 100)

    if source == "systemd":
        cmd = f"journalctl -u {name} -n {lines} --no-pager"
    elif source == "nginx":
        # Try access log first, then error log
        log_path = f"/var/log/nginx/{name}.access.log"
        cmd = f"tail -n {lines} {log_path}"
    elif source == "pm2":
        cmd = f"pm2 logs {name} --lines {lines} --nostream"
    else:
        return {"error": f"Unknown log source: {source}"}

    result = await run_cmd(cmd, timeout=15)
    if result["returncode"] != 0 and source == "nginx":
        # Fallback to error log
        log_path = f"/var/log/nginx/{name}.error.log"
        result = await run_cmd(f"tail -n {lines} {log_path}", timeout=15)

    return {
        "source": source,
        "name": name,
        "lines": result["stdout"].splitlines() if result["stdout"] else [],
        "error": result["stderr"] if result["returncode"] != 0 else None,
    }


async def handle_stream(params: dict) -> dict:
    """Return the streaming command (actual streaming handled by connection module)."""
    source = params.get("source", "systemd")
    name = params.get("name", "")

    if source == "systemd":
        cmd = f"journalctl -u {name} -f --no-pager"
    elif source == "nginx":
        cmd = f"tail -f /var/log/nginx/{name}.access.log"
    elif source == "pm2":
        cmd = f"pm2 logs {name}"
    else:
        return {"error": f"Unknown source: {source}"}

    return {"command": cmd}
