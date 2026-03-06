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

BACKEND_SSL_TEMPLATE = """# HTTP → HTTPS redirect
server {{
    listen 80;
    server_name {domain};
    return 301 https://$server_name$request_uri;
}}

# HTTPS
server {{
    listen 443 ssl http2;
    server_name {domain};

    # SSL Certificate
    ssl_certificate {ssl_cert_path};
    ssl_certificate_key {ssl_key_path};

    # SSL Configuration
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    # Proxy to backend app
    location / {{
        proxy_pass http://127.0.0.1:{port};
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket support
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";

        # Timeouts
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }}

    # Upload size
    client_max_body_size 50M;
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

STATIC_SSL_TEMPLATE = """# HTTP → HTTPS redirect
server {{
    listen 80;
    server_name {domain};
    return 301 https://$server_name$request_uri;
}}

# HTTPS
server {{
    listen 443 ssl http2;
    server_name {domain};

    # SSL Certificate
    ssl_certificate {ssl_cert_path};
    ssl_certificate_key {ssl_key_path};

    # SSL Configuration
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    root {root_path};
    index index.html;

    location / {{
        try_files $uri $uri/ /index.html;
    }}

    location ~* \\.(js|css|png|jpg|jpeg|gif|ico|svg|woff|woff2|ttf|eot)$ {{
        expires 1y;
        add_header Cache-Control "public, immutable";
    }}

    client_max_body_size 50M;
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
    ssl_cert_path = params.get("ssl_cert_path")
    ssl_key_path = params.get("ssl_key_path")

    if site_type == "backend":
        port = params.get("upstream_port", 3000)
        if ssl_cert_path and ssl_key_path:
            config = BACKEND_SSL_TEMPLATE.format(
                domain=domain, port=port,
                ssl_cert_path=ssl_cert_path, ssl_key_path=ssl_key_path,
            )
        else:
            config = BACKEND_TEMPLATE.format(domain=domain, port=port)
    else:
        root_path = params.get("root_path", f"/var/www/{domain}")
        if ssl_cert_path and ssl_key_path:
            config = STATIC_SSL_TEMPLATE.format(
                domain=domain, root_path=root_path,
                ssl_cert_path=ssl_cert_path, ssl_key_path=ssl_key_path,
            )
        else:
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

    return {"domain": domain, "status": "created", "type": site_type, "ssl": bool(ssl_cert_path)}


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
