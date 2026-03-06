from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql+asyncpg://postgres:T3ch$upp0rt!!@localhost:5432/serverdeck"

    # JWT
    jwt_secret: str = "change-me-in-production-use-a-real-secret-key"
    jwt_algorithm: str = "HS256"
    jwt_expire_hours: int = 24

    # CORS
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    # App
    app_name: str = "ServerDeck"
    portal_base_url: str = "http://localhost:8000"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
