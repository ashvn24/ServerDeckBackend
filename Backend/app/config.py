from pydantic_settings import BaseSettings
from functools import lru_cache
from urllib.parse import quote_plus

_encoded_password = quote_plus("T3ch$upp0rt!!")

class Settings(BaseSettings):
    database_url: str = f"postgresql+asyncpg://postgres:{_encoded_password}@localhost:5432/serverdeck"

    # JWT
    jwt_secret: str = "change-me-in-production-use-a-real-secret-key"
    jwt_algorithm: str = "HS256"
    jwt_expire_hours: int = 24

    # CORS
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000", "https://serverdeck.koushik.cc"]

    # App
    app_name: str = "ServerDeck"
    portal_base_url: str = "https://serverdeck.koushik.cc"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
