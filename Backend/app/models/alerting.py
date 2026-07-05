import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Boolean, Float, DateTime, ForeignKey, Enum, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base
import enum


class AlertMetric(str, enum.Enum):
    cpu = "cpu"
    ram = "ram"
    disk = "disk"
    server_offline = "server_offline"
    service_down = "service_down"
    ssl_expiry = "ssl_expiry"


class AlertStatus(str, enum.Enum):
    active = "active"
    acknowledged = "acknowledged"
    resolved = "resolved"


class AlertUrgency(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class AlertRule(Base):
    __tablename__ = "alert_rules"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    server_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("servers.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    metric: Mapped[AlertMetric] = mapped_column(Enum(AlertMetric), nullable=False)
    threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    service_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ssl_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Relationships
    server = relationship("Server", back_populates="alert_rules")
    records = relationship("AlertRecord", back_populates="rule", cascade="all, delete-orphan")


class AlertRecord(Base):
    __tablename__ = "alert_records"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rule_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("alert_rules.id", ondelete="CASCADE"), nullable=False)
    server_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("servers.id", ondelete="CASCADE"), nullable=False)
    triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metric_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[AlertStatus] = mapped_column(Enum(AlertStatus), default=AlertStatus.active)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    rule = relationship("AlertRule", back_populates="records")
    server = relationship("Server", back_populates="alert_records")
    diagnosis = relationship("AlertDiagnosis", uselist=False, back_populates="alert_record", cascade="all, delete-orphan")
    ticket = relationship("Ticket", uselist=False, back_populates="alert")


class AlertDiagnosis(Base):
    __tablename__ = "alert_diagnoses"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    alert_record_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("alert_records.id", ondelete="CASCADE"), unique=True, nullable=False)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggested_fix: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggested_command: Mapped[str | None] = mapped_column(Text, nullable=True)
    urgency: Mapped[AlertUrgency | None] = mapped_column(Enum(AlertUrgency), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed: Mapped[bool] = mapped_column(Boolean, default=False)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    alert_record = relationship("AlertRecord", back_populates="diagnosis")
