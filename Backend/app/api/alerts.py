from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, desc, func
from sqlalchemy.orm import selectinload
from pydantic import BaseModel
from typing import List, Optional
import uuid
from datetime import datetime, timezone, timedelta

from app.database import get_db
from app.models.alerting import AlertRule, AlertRecord, AlertStatus, AlertUrgency, AlertMetric, AlertDiagnosis
from app.models.server import Server

router = APIRouter()

# --- Pydantic Schemas ---

class AlertRuleCreate(BaseModel):
    name: str
    metric: str
    threshold: Optional[float] = None
    service_name: Optional[str] = None
    ssl_domain: Optional[str] = None

class AlertRuleUpdate(BaseModel):
    name: Optional[str] = None
    threshold: Optional[float] = None
    enabled: Optional[bool] = None

# --- Alert Rules ---

@router.post("/api/servers/{server_id}/alert-rules")
async def create_alert_rule(server_id: uuid.UUID, rule_in: AlertRuleCreate, db: AsyncSession = Depends(get_db)):
    server = await db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
        
    rule = AlertRule(
        server_id=server_id,
        name=rule_in.name,
        metric=AlertMetric(rule_in.metric),
        threshold=rule_in.threshold,
        service_name=rule_in.service_name,
        ssl_domain=rule_in.ssl_domain
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return rule

@router.get("/api/servers/{server_id}/alert-rules")
async def get_alert_rules(server_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AlertRule).where(AlertRule.server_id == server_id).order_by(AlertRule.created_at.desc()))
    return result.scalars().all()

@router.patch("/api/servers/{server_id}/alert-rules/{id}")
async def update_alert_rule(server_id: uuid.UUID, id: uuid.UUID, rule_in: AlertRuleUpdate, db: AsyncSession = Depends(get_db)):
    rule = await db.get(AlertRule, id)
    if not rule or rule.server_id != server_id:
        raise HTTPException(status_code=404, detail="Rule not found")
        
    if rule_in.name is not None:
        rule.name = rule_in.name
    if rule_in.threshold is not None:
        rule.threshold = rule_in.threshold
    if rule_in.enabled is not None:
        rule.enabled = rule_in.enabled
        
    await db.commit()
    await db.refresh(rule)
    return rule

@router.delete("/api/servers/{server_id}/alert-rules/{id}")
async def delete_alert_rule(server_id: uuid.UUID, id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    rule = await db.get(AlertRule, id)
    if not rule or rule.server_id != server_id:
        raise HTTPException(status_code=404, detail="Rule not found")
        
    await db.delete(rule)
    await db.commit()
    return {"status": "ok"}

# --- Alert Records & Summary ---

def _format_alert(record, rule, server, diagnosis=None):
    now = datetime.now(timezone.utc)
    
    # Ensure triggered_at is timezone-aware
    triggered = record.triggered_at
    if triggered.tzinfo is None:
        triggered = triggered.replace(tzinfo=timezone.utc)
        
    duration = now - triggered
    
    diag_data = None
    if diagnosis:
        diag_data = {
            "explanation": diagnosis.explanation,
            "suggested_fix": diagnosis.suggested_fix,
            "suggested_command": diagnosis.suggested_command,
            "urgency": diagnosis.urgency.value if diagnosis.urgency else "medium",
            "failed": diagnosis.failed
        }
        
    return {
        "id": record.id,
        "server_id": server.id,
        "server_name": server.name,
        "rule_name": rule.name,
        "metric": rule.metric.value,
        "metric_value": record.metric_value,
        "triggered_at": record.triggered_at,
        "duration": duration.total_seconds(),
        "status": record.status.value,
        "diagnosis": diag_data,
        "ticket_id": record.ticket.id if record.ticket else None
    }

@router.get("/api/alerts/summary")
async def get_alerts_summary(db: AsyncSession = Depends(get_db)):
    # active alerts
    active_res = await db.execute(
        select(AlertRecord)
        .where(AlertRecord.status.in_([AlertStatus.active, AlertStatus.acknowledged]))
    )
    active_alerts = active_res.scalars().all()
    
    active_count = len(active_alerts)
    servers_affected = len(set(a.server_id for a in active_alerts))
    
    # resolved last 24h
    now = datetime.now(timezone.utc)
    resolved_res = await db.execute(
        select(func.count(AlertRecord.id))
        .where(AlertRecord.status == AlertStatus.resolved)
        .where(AlertRecord.resolved_at >= now - timedelta(hours=24))
    )
    resolved_last_24h = resolved_res.scalar() or 0
    
    # critical count (based on diagnosis)
    critical_res = await db.execute(
        select(func.count(AlertDiagnosis.id))
        .join(AlertRecord, AlertRecord.id == AlertDiagnosis.alert_record_id)
        .where(AlertRecord.status.in_([AlertStatus.active, AlertStatus.acknowledged]))
        .where(AlertDiagnosis.urgency == AlertUrgency.critical)
    )
    critical_count = critical_res.scalar() or 0
    
    return {
        "active_count": active_count,
        "servers_affected": servers_affected,
        "resolved_last_24h": resolved_last_24h,
        "critical_count": critical_count
    }

@router.get("/api/alerts")
async def get_all_active_alerts(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AlertRecord, AlertRule, Server, AlertDiagnosis)
        .join(AlertRule, AlertRecord.rule_id == AlertRule.id)
        .join(Server, AlertRecord.server_id == Server.id)
        .outerjoin(AlertDiagnosis, AlertRecord.id == AlertDiagnosis.alert_record_id)
        .options(selectinload(AlertRecord.ticket))
        .where(AlertRecord.status.in_([AlertStatus.active, AlertStatus.acknowledged]))
        .order_by(AlertRecord.triggered_at.desc())
    )
    
    alerts = []
    for record, rule, server, diagnosis in result.all():
        alerts.append(_format_alert(record, rule, server, diagnosis))
        
    return alerts

@router.get("/api/servers/{server_id}/alerts")
async def get_server_alerts(server_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AlertRecord, AlertRule, Server, AlertDiagnosis)
        .join(AlertRule, AlertRecord.rule_id == AlertRule.id)
        .join(Server, AlertRecord.server_id == Server.id)
        .outerjoin(AlertDiagnosis, AlertRecord.id == AlertDiagnosis.alert_record_id)
        .options(selectinload(AlertRecord.ticket))
        .where(AlertRecord.server_id == server_id)
        .order_by(AlertRecord.triggered_at.desc())
    )
    
    alerts = []
    for record, rule, server, diagnosis in result.all():
        alerts.append(_format_alert(record, rule, server, diagnosis))
        
    return alerts

@router.post("/api/alerts/{id}/acknowledge")
async def acknowledge_alert(id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    record = await db.get(AlertRecord, id)
    if not record:
        raise HTTPException(status_code=404, detail="Alert not found")
        
    record.status = AlertStatus.acknowledged
    record.acknowledged_at = datetime.now(timezone.utc)
    await db.commit()
    return {"status": "ok"}

@router.post("/api/alerts/{id}/resolve")
async def resolve_alert(id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    record = await db.get(AlertRecord, id)
    if not record:
        raise HTTPException(status_code=404, detail="Alert not found")
        
    record.status = AlertStatus.resolved
    record.resolved_at = datetime.now(timezone.utc)
    await db.commit()
    return {"status": "ok"}
