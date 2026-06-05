from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/serverdeck"

    # JWT
    jwt_secret: str = "change-me-in-production-use-a-real-secret-key"
    jwt_algorithm: str = "HS256"
    jwt_expire_hours: int = 24

    # CORS
    cors_origins: list[str] = [
        "http://localhost:5173", 
        "http://localhost:3000", 
        "https://serverdeck.dynamiqqr.com", 
        "https://server-deck-frontend.vercel.app"
    ]

    # App
    app_name: str = "ServerDeck"
    portal_base_url: str = "https://serverdeck.dynamiqqr.com"

    ui_base_url: str = "https://server-deck-frontend.vercel.app"

    # SMTP Configuration
    smtp_server: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_username: str = "ashwinvk77@gmail.com"
    smtp_password: str = "ienf eubn qyag ntuf"
    smtp_from_email: str = "ashwinvk77@gmail.com"
    smtp_from_name: str = "ServerDeck"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
