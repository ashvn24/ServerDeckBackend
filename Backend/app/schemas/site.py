from pydantic import BaseModel
from uuid import UUID
from datetime import datetime


class SiteCreate(BaseModel):
    server_id: UUID
    domain: str
    site_type: str  # backend | frontend
    service_name: str | None = None
    upstream_port: int | None = None
    working_directory: str | None = None
    start_command: str | None = None
    env_file: str | None = None
    pm2_app_name: str | None = None
    build_directory: str | None = None
    is_ssr: bool = False
    nginx_config_path: str | None = None
    ssl_enabled: bool = False
    ssl_auto_renew: bool = False


class SiteResponse(BaseModel):
    id: UUID
    server_id: UUID
    domain: str
    site_type: str
    service_name: str | None = None
    upstream_port: int | None = None
    working_directory: str | None = None
    start_command: str | None = None
    env_file: str | None = None
    pm2_app_name: str | None = None
    build_directory: str | None = None
    is_ssr: bool
    nginx_config_path: str | None = None
    ssl_enabled: bool
    ssl_auto_renew: bool
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}
