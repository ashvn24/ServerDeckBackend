from pydantic import BaseModel
from uuid import UUID
from datetime import datetime
from typing import Any


class ServerCreate(BaseModel):
    name: str


class ServerResponse(BaseModel):
    id: UUID
    name: str
    hostname: str | None = None
    ip_address: str | None = None
    os_info: str | None = None
    agent_token: str
    agent_version: str | None = None
    is_online: bool = False
    last_seen: datetime | None = None
    cpu_percent: float | None = None
    ram_used_mb: float | None = None
    ram_total_mb: float | None = None
    disk_used_percent: float | None = None
    uptime_seconds: int | None = None
    nginx_sites: Any | None = None
    pm2_apps: Any | None = None
    systemd_services: Any | None = None
    ssl_certs: Any | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
