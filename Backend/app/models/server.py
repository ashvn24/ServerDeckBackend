import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Boolean, Float, Integer, DateTime, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base


class Server(Base):
    __tablename__ = "servers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    hostname: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    os_info: Mapped[str | None] = mapped_column(String(255), nullable=True)
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("teams.id"), nullable=False)
    agent_token: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    agent_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_online: Mapped[bool] = mapped_column(Boolean, default=False)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Telemetry fields (updated every 10s)
    cpu_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    ram_used_mb: Mapped[float | None] = mapped_column(Float, nullable=True)
    ram_total_mb: Mapped[float | None] = mapped_column(Float, nullable=True)
    disk_used_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    uptime_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Cached service data (updated every 60s as JSON)
    nginx_sites: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    pm2_apps: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    systemd_services: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ssl_certs: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Relationships
    team: Mapped["Team"] = relationship("Team", back_populates="servers")
    sites: Mapped[list["Site"]] = relationship("Site", back_populates="server", cascade="all, delete-orphan")
    audit_logs: Mapped[list["AuditLog"]] = relationship("AuditLog", back_populates="server", cascade="all, delete-orphan")


from app.models.user import Team  # noqa: E402, F401
from app.models.site import Site  # noqa: E402, F401
from app.models.audit import AuditLog  # noqa: E402, F401
