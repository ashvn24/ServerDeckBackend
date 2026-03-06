"""
Firewall Handler — manages UFW rules.
"""
import re
import logging
from serverdeck_agent.utils import run_cmd

logger = logging.getLogger("serverdeck.agent.firewall")


async def handle_list(params: dict) -> dict:
    """List UFW rules."""
    result = await run_cmd("ufw status numbered", timeout=10)
    if result["returncode"] != 0:
        return {"error": result["stderr"], "rules": []}

    rules = []
    for line in result["stdout"].splitlines():
        match = re.match(r"\[\s*(\d+)\]\s+(.+)", line)
        if match:
            rules.append({
                "number": int(match.group(1)),
                "rule": match.group(2).strip(),
            })

    return {"rules": rules, "raw": result["stdout"]}


async def handle_allow(params: dict) -> dict:
    port = params["port"]
    proto = params.get("proto", "tcp")
    result = await run_cmd(f"ufw allow {port}/{proto}", timeout=10)
    if result["returncode"] != 0:
        return {"error": result["stderr"]}
    return {"port": port, "proto": proto, "status": "allowed"}


async def handle_deny(params: dict) -> dict:
    port = params["port"]
    proto = params.get("proto", "tcp")
    result = await run_cmd(f"ufw deny {port}/{proto}", timeout=10)
    if result["returncode"] != 0:
        return {"error": result["stderr"]}
    return {"port": port, "proto": proto, "status": "denied"}


async def handle_delete(params: dict) -> dict:
    rule_number = params["rule_number"]
    result = await run_cmd(f"echo y | ufw delete {rule_number}", timeout=10)
    if result["returncode"] != 0:
        return {"error": result["stderr"]}
    return {"rule_number": rule_number, "status": "deleted"}
