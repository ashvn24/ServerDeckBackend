"""
Nginx Handler — manages nginx site configs.
"""
import logging
import os
from pathlib import Path
from serverdeck_agent.utils import run_cmd

logger = logging.getLogger("serverdeck.agent.nginx")

SITES_AVAILABLE = Path("/etc/nginx/sites-available")
SITES_ENABLED = Path("/etc/nginx/sites-enabled")

BACKEND_TEMPLATE = """server {{
    listen 80;
    server_name {domain};
    location / {{
        proxy_pass http://127.0.0.1:{port};
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_cache_bypass $http_upgrade;
    }}
}}
"""

STATIC_TEMPLATE = """server {{
    listen 80;
    server_name {domain};
    root {root_path};
    index index.html;
    location / {{
        try_files $uri $uri/ /index.html;
    }}
    location ~* \\.(js|css|png|jpg|jpeg|gif|ico|svg|woff|woff2|ttf|eot)$ {{
        expires 1y;
        add_header Cache-Control "public, immutable";
    }}
}}
"""


async def handle_list(params: dict) -> dict:
    """List nginx sites from sites-enabled."""
    from serverdeck_agent.system_info import scan_nginx_sites
    sites = await scan_nginx_sites()
    return {"sites": sites}


async def handle_create(params: dict) -> dict:
    """Create a new nginx site config."""
    domain = params["domain"]
    site_type = params.get("type", "backend")

    if site_type == "backend":
        port = params.get("upstream_port", 3000)
        config = BACKEND_TEMPLATE.format(domain=domain, port=port)
    else:
        root_path = params.get("root_path", f"/var/www/{domain}")
        config = STATIC_TEMPLATE.format(domain=domain, root_path=root_path)

    # Write config
    conf_path = SITES_AVAILABLE / domain
    conf_path.write_text(config)

    # Symlink to sites-enabled
    link_path = SITES_ENABLED / domain
    if link_path.exists() or link_path.is_symlink():
        link_path.unlink()
    os.symlink(str(conf_path), str(link_path))

    # Test config
    test_result = await run_cmd("nginx -t", timeout=10)
    if test_result["returncode"] != 0:
        # Rollback
        link_path.unlink(missing_ok=True)
        conf_path.unlink(missing_ok=True)
        return {"error": f"Nginx config test failed: {test_result['stderr']}"}

    # Reload nginx
    await run_cmd("systemctl reload nginx", timeout=10)

    return {"domain": domain, "status": "created", "type": site_type}


async def handle_delete(params: dict) -> dict:
    """Delete an nginx site config."""
    domain = params["domain"]
    link_path = SITES_ENABLED / domain
    conf_path = SITES_AVAILABLE / domain

    link_path.unlink(missing_ok=True)
    conf_path.unlink(missing_ok=True)

    await run_cmd("systemctl reload nginx", timeout=10)
    return {"domain": domain, "status": "deleted"}


async def handle_enable(params: dict) -> dict:
    """Enable a site by creating symlink."""
    domain = params["domain"]
    conf_path = SITES_AVAILABLE / domain
    link_path = SITES_ENABLED / domain

    if not conf_path.exists():
        return {"error": f"Config not found: {domain}"}

    if link_path.exists() or link_path.is_symlink():
        link_path.unlink()
    os.symlink(str(conf_path), str(link_path))

    await run_cmd("systemctl reload nginx", timeout=10)
    return {"domain": domain, "status": "enabled"}


async def handle_disable(params: dict) -> dict:
    """Disable a site by removing symlink."""
    domain = params["domain"]
    link_path = SITES_ENABLED / domain
    link_path.unlink(missing_ok=True)

    await run_cmd("systemctl reload nginx", timeout=10)
    return {"domain": domain, "status": "disabled"}


async def handle_get_config(params: dict) -> dict:
    """Get raw nginx config file content."""
    domain = params["domain"]
    conf_path = SITES_AVAILABLE / domain
    if not conf_path.exists():
        return {"error": f"Config not found: {domain}"}
    return {"domain": domain, "config": conf_path.read_text()}


async def handle_update_config(params: dict) -> dict:
    """Update nginx config with new content."""
    domain = params["domain"]
    new_config = params["config"]
    conf_path = SITES_AVAILABLE / domain

    if not conf_path.exists():
        return {"error": f"Config not found: {domain}"}

    # Backup current config
    backup = conf_path.read_text()
    conf_path.write_text(new_config)

    # Test
    test_result = await run_cmd("nginx -t", timeout=10)
    if test_result["returncode"] != 0:
        # Rollback
        conf_path.write_text(backup)
        return {"error": f"Config test failed: {test_result['stderr']}"}

    await run_cmd("systemctl reload nginx", timeout=10)
    return {"domain": domain, "status": "updated"}


async def handle_test(params: dict) -> dict:
    """Test nginx configuration."""
    result = await run_cmd("nginx -t", timeout=10)
    return {
        "valid": result["returncode"] == 0,
        "output": result["stderr"] or result["stdout"],
    }
