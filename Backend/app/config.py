from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Database
    database_url: str

    # JWT
    jwt_secret: str
    jwt_algorithm: str
    jwt_expire_hours: int
    jwt_issuer: str = "serverdeck"
    jwt_audience: str = "serverdeck-api"

    # One-time platform-owner bootstrap secret. Must be set (and supplied by the
    # caller) for POST /api/admin/setup to succeed. If unset, setup is disabled.
    admin_setup_secret: str | None = None

    # CORS
    cors_origins: list[str]

    # App
    app_name: str
    portal_base_url: str
    ui_base_url: str

    # SMTP Configuration
    smtp_server: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    smtp_from_email: str
    smtp_from_name: str

    grok_api_key: str

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
