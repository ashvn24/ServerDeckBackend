"""
SSL Handler — manages Let's Encrypt certificates via certbot.
"""
import logging
from pathlib import Path
from serverdeck_agent.utils import run_cmd

logger = logging.getLogger("serverdeck.agent.ssl")

LETSENCRYPT_LIVE = Path("/etc/letsencrypt/live")


async def handle_list(params: dict) -> dict:
    from serverdeck_agent.system_info import scan_ssl_certs
    certs = await scan_ssl_certs()
    return {"certs": certs}


async def handle_list_available(params: dict) -> dict:
    """List available SSL certificates from /etc/letsencrypt/live/."""
    certs = []
    if LETSENCRYPT_LIVE.exists():
        for entry in LETSENCRYPT_LIVE.iterdir():
            if entry.is_dir() and not entry.name.startswith("README"):
                fullchain = entry / "fullchain.pem"
                privkey = entry / "privkey.pem"
                if fullchain.exists() and privkey.exists():
                    certs.append({
                        "name": entry.name,
                        "cert_path": str(fullchain),
                        "key_path": str(privkey),
                    })
    return {"certs": certs}


async def handle_issue(params: dict) -> dict:
    """Issue a new SSL certificate via certbot."""
    domain = params["domain"]
    email = params.get("email", "")

    cmd = f"certbot --nginx -d {domain} --non-interactive --agree-tos"
    if email:
        cmd += f" --email {email}"
    else:
        cmd += " --register-unsafely-without-email"

    result = await run_cmd(cmd, timeout=120)
    if result["returncode"] != 0:
        return {"error": f"Certbot failed: {result['stderr']}"}

    return {"domain": domain, "status": "issued"}


async def handle_renew(params: dict) -> dict:
    """Renew SSL certificate(s)."""
    domain = params.get("domain")

    cmd = "certbot renew"
    if domain:
        cmd += f" --cert-name {domain}"

    result = await run_cmd(cmd, timeout=120)
    if result["returncode"] != 0:
        return {"error": f"Renewal failed: {result['stderr']}"}

    return {"status": "renewed", "output": result["stdout"]}
