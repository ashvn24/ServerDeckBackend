import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Boolean, Integer, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base


class Site(Base):
    __tablename__ = "sites"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    server_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("servers.id"), nullable=False)
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    site_type: Mapped[str] = mapped_column(String(50), nullable=False)  # backend | frontend
    service_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    upstream_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    working_directory: Mapped[str | None] = mapped_column(String(500), nullable=True)
    start_command: Mapped[str | None] = mapped_column(String(500), nullable=True)
    env_file: Mapped[str | None] = mapped_column(String(500), nullable=True)
    pm2_app_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    build_directory: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_ssr: Mapped[bool] = mapped_column(Boolean, default=False)
    nginx_config_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    ssl_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    ssl_auto_renew: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Relationships
    server: Mapped["Server"] = relationship("Server", back_populates="sites")


from app.models.server import Server  # noqa: E402, F401
