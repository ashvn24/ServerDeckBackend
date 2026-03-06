"""
System Info Collector — gathers CPU, RAM, disk, OS, and service data.
"""
import json
import logging
import os
import re
import socket
import time
from pathlib import Path

import psutil

from serverdeck_agent.utils import run_cmd

logger = logging.getLogger("serverdeck.agent.system_info")


def get_cpu_percent() -> float:
    return psutil.cpu_percent(interval=1)


def get_memory_info() -> dict:
    mem = psutil.virtual_memory()
    return {
        "ram_used_mb": round(mem.used / (1024 * 1024), 1),
        "ram_total_mb": round(mem.total / (1024 * 1024), 1),
    }


def get_disk_usage() -> float:
    usage = psutil.disk_usage("/")
    return round(usage.percent, 1)


def get_uptime_seconds() -> int:
    return int(time.time() - psutil.boot_time())


def get_ip_address() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "unknown"


def get_os_info() -> str:
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except FileNotFoundError:
        pass
    return "Linux"


def get_hostname() -> str:
    return socket.gethostname()


def get_registration_data() -> dict:
    mem = get_memory_info()
    return {
        "hostname": get_hostname(),
        "os_info": get_os_info(),
        "ip_address": get_ip_address(),
        "cpu_count": psutil.cpu_count(),
        "ram_total_mb": mem["ram_total_mb"],
    }


def get_telemetry_data() -> dict:
    mem = get_memory_info()
    return {
        "cpu_percent": get_cpu_percent(),
        "ram_used_mb": mem["ram_used_mb"],
        "ram_total_mb": mem["ram_total_mb"],
        "disk_used_percent": get_disk_usage(),
        "uptime_seconds": get_uptime_seconds(),
    }


async def scan_nginx_sites() -> list:
    """Parse nginx sites from /etc/nginx/sites-enabled/."""
    sites = []
    sites_dir = Path("/etc/nginx/sites-enabled")
    if not sites_dir.exists():
        return sites

    for conf_file in sites_dir.iterdir():
        if conf_file.is_file() or conf_file.is_symlink():
            try:
                content = conf_file.read_text()
                site = {"filename": conf_file.name}

                # Parse server_name
                match = re.search(r"server_name\s+(.+?);", content)
                if match:
                    site["server_name"] = match.group(1).strip()

                # Parse proxy_pass port
                proxy_match = re.search(r"proxy_pass\s+https?://127\.0\.0\.1:(\d+)", content)
                if proxy_match:
                    site["upstream_port"] = int(proxy_match.group(1))
                    site["type"] = "backend"
                else:
                    site["type"] = "static"

                # Check for SSL
                site["ssl"] = "ssl_certificate" in content or "listen 443" in content

                sites.append(site)
            except Exception as e:
                logger.error(f"Error parsing nginx config {conf_file}: {e}")

    return sites


async def scan_pm2_apps() -> list:
    """Get PM2 app list via `pm2 jlist`."""
    result = await run_cmd("pm2 jlist", timeout=10)
    if result["returncode"] != 0:
        return []
    try:
        apps_raw = json.loads(result["stdout"])
        apps = []
        for app in apps_raw:
            apps.append({
                "name": app.get("name"),
                "status": app.get("pm2_env", {}).get("status", "unknown"),
                "pid": app.get("pid"),
                "memory": app.get("monit", {}).get("memory", 0),
                "cpu": app.get("monit", {}).get("cpu", 0),
                "uptime": app.get("pm2_env", {}).get("pm_uptime", 0),
                "restart_count": app.get("pm2_env", {}).get("restart_time", 0),
            })
        return apps
    except (json.JSONDecodeError, KeyError):
        return []


async def scan_systemd_services() -> list:
    """List user-relevant systemd services."""
    result = await run_cmd(
        "systemctl list-units --type=service --all --output=json", timeout=10
    )
    if result["returncode"] != 0:
        # Fallback: parse text output
        result = await run_cmd(
            "systemctl list-units --type=service --all --no-pager", timeout=10
        )
        return _parse_systemd_text(result["stdout"])

    try:
        services_raw = json.loads(result["stdout"])
        # Filter out system internals
        skip_prefixes = (
            "systemd-", "dbus", "ssh", "getty", "serial-getty",
            "user@", "user-runtime", "modprobe", "kmod",
            "plymouth", "emergency", "rescue", "initrd",
        )
        services = []
        for svc in services_raw:
            name = svc.get("unit", "")
            if name.endswith(".service"):
                short = name.replace(".service", "")
                if not any(short.startswith(p) for p in skip_prefixes):
                    services.append({
                        "name": short,
                        "description": svc.get("description", ""),
                        "load_state": svc.get("load", ""),
                        "active_state": svc.get("active", ""),
                        "sub_state": svc.get("sub", ""),
                    })
        return services
    except (json.JSONDecodeError, KeyError):
        return []


def _parse_systemd_text(text: str) -> list:
    """Fallback parser for systemctl text output."""
    services = []
    skip_prefixes = (
        "systemd-", "dbus", "ssh", "getty", "serial-getty",
        "user@", "user-runtime", "modprobe", "kmod",
    )
    for line in text.splitlines():
        line = line.strip()
        if ".service" in line:
            parts = line.split()
            if len(parts) >= 4:
                name = parts[0].replace(".service", "").lstrip("●").strip()
                if not any(name.startswith(p) for p in skip_prefixes):
                    services.append({
                        "name": name,
                        "load_state": parts[1] if len(parts) > 1 else "",
                        "active_state": parts[2] if len(parts) > 2 else "",
                        "sub_state": parts[3] if len(parts) > 3 else "",
                        "description": " ".join(parts[4:]) if len(parts) > 4 else "",
                    })
    return services


async def scan_ssl_certs() -> list:
    """Parse certbot certificates output."""
    result = await run_cmd("certbot certificates 2>/dev/null", timeout=15)
    if result["returncode"] != 0:
        return []

    certs = []
    current = None
    for line in result["stdout"].splitlines():
        line = line.strip()
        if line.startswith("Certificate Name:"):
            if current:
                certs.append(current)
            current = {"name": line.split(":", 1)[1].strip(), "domains": []}
        elif line.startswith("Domains:") and current:
            current["domains"] = line.split(":", 1)[1].strip().split()
        elif line.startswith("Expiry Date:") and current:
            # e.g. "Expiry Date: 2025-03-15 (VALID: 89 days)"
            date_part = line.split(":", 1)[1].strip().split("(")[0].strip()
            current["expiry"] = date_part
        elif line.startswith("Certificate Path:") and current:
            current["cert_path"] = line.split(":", 1)[1].strip()

    if current:
        certs.append(current)

    return certs


async def get_scan_data() -> dict:
    """Collect all service scan data."""
    return {
        "nginx_sites": await scan_nginx_sites(),
        "pm2_apps": await scan_pm2_apps(),
        "systemd_services": await scan_systemd_services(),
        "ssl_certs": await scan_ssl_certs(),
    }
