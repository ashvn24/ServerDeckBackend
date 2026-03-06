"""
Alerting Service — background task that checks server health thresholds.
"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from sqlalchemy import select

from app.database import async_session_factory
from app.models.server import Server

logger = logging.getLogger("serverdeck.alerting")

# Thresholds
CPU_THRESHOLD = 90.0
RAM_THRESHOLD = 90.0
DISK_THRESHOLD = 90.0
OFFLINE_MINUTES = 5
SSL_EXPIRY_DAYS = 14


async def check_alerts():
    """Run alert checks on all servers."""
    while True:
        try:
            async with async_session_factory() as db:
                result = await db.execute(select(Server))
                servers = result.scalars().all()

                for server in servers:
                    alerts = []

                    # CPU check
                    if server.cpu_percent and server.cpu_percent > CPU_THRESHOLD:
                        alerts.append(f"CPU at {server.cpu_percent:.1f}%")

                    # RAM check
                    if server.ram_used_mb and server.ram_total_mb:
                        ram_pct = (server.ram_used_mb / server.ram_total_mb) * 100
                        if ram_pct > RAM_THRESHOLD:
                            alerts.append(f"RAM at {ram_pct:.1f}%")

                    # Disk check
                    if server.disk_used_percent and server.disk_used_percent > DISK_THRESHOLD:
                        alerts.append(f"Disk at {server.disk_used_percent:.1f}%")

                    # Offline check
                    if server.last_seen:
                        offline_threshold = datetime.now(timezone.utc) - timedelta(minutes=OFFLINE_MINUTES)
                        if not server.is_online and server.last_seen < offline_threshold:
                            alerts.append(f"Offline for >{OFFLINE_MINUTES} minutes")

                    # SSL expiry check
                    if server.ssl_certs:
                        for cert in server.ssl_certs:
                            expiry = cert.get("expiry")
                            if expiry:
                                try:
                                    exp_date = datetime.fromisoformat(expiry)
                                    if exp_date - datetime.now(timezone.utc) < timedelta(days=SSL_EXPIRY_DAYS):
                                        domains = cert.get("domains", [])
                                        alerts.append(
                                            f"SSL cert for {', '.join(domains)} expires in "
                                            f"{(exp_date - datetime.now(timezone.utc)).days} days"
                                        )
                                except (ValueError, TypeError):
                                    pass

                    if alerts:
                        logger.warning(
                            f"Server '{server.name}' ({server.hostname}): {'; '.join(alerts)}"
                        )

        except Exception as e:
            logger.error(f"Alert check error: {e}")

        await asyncio.sleep(60)
