import asyncio
import logging
from datetime import datetime, timezone, timedelta
from sqlalchemy import text, select
from app.database import async_session_factory, tenant_session, tenant_schema
from app.models.server import Server
from app.models.alerting import AlertRule, AlertRecord, AlertStatus, AlertMetric
from app.services.diagnosis_service import run_diagnosis
from app.ws.client_handler import forward_to_watchers

logger = logging.getLogger("serverdeck.alert_service")


async def evaluate_rule(rule: AlertRule, server: Server) -> tuple[bool, float | None]:
    """Evaluate an alert rule against server telemetry. Returns (is_breached, metric_value)."""
    now = datetime.now(timezone.utc)
    
    if rule.metric == AlertMetric.cpu:
        if server.cpu_percent is not None and server.cpu_percent >= (rule.threshold or 0):
            return True, server.cpu_percent
    
    elif rule.metric == AlertMetric.ram:
        if server.ram_used_mb and server.ram_total_mb:
            ram_pct = (server.ram_used_mb / server.ram_total_mb) * 100
            if ram_pct >= (rule.threshold or 0):
                return True, ram_pct
                
    elif rule.metric == AlertMetric.disk:
        if server.disk_used_percent is not None and server.disk_used_percent >= (rule.threshold or 0):
            return True, server.disk_used_percent
            
    elif rule.metric == AlertMetric.server_offline:
        if server.last_seen:
            diff = now - server.last_seen
            if diff > timedelta(minutes=5):
                return True, float(diff.total_seconds() / 60)
                
    elif rule.metric == AlertMetric.service_down:
        if server.systemd_services and rule.service_name:
            for svc in server.systemd_services:
                if svc.get("name") == rule.service_name:
                    if svc.get("status") != "running":
                        return True, 0.0
                        
    elif rule.metric == AlertMetric.ssl_expiry:
        if server.ssl_certs and rule.ssl_domain:
            for cert in server.ssl_certs:
                domains = cert.get("domains", [])
                if rule.ssl_domain in domains:
                    expiry = cert.get("expiry")
                    if expiry:
                        try:
                            exp_date = datetime.fromisoformat(expiry)
                            days_left = (exp_date - now).days
                            if days_left <= (rule.threshold or 0):
                                return True, float(days_left)
                        except (ValueError, TypeError):
                            pass
                            
    return False, None


# Keep strong references to fire-and-forget diagnosis tasks so they aren't
# garbage-collected mid-run.
_diagnosis_tasks: set[asyncio.Task] = set()


async def check_alerts():
    """Run alert checks on all servers across all tenants."""
    while True:
        # Discover tenant schemas from the database itself so individual users
        # (tenant_individual) and schemas missing from the organizations table
        # are covered too.
        schemas = []
        try:
            async with async_session_factory() as root_db:
                res = await root_db.execute(text(
                    "SELECT schema_name FROM information_schema.schemata "
                    "WHERE schema_name LIKE 'tenant\\_%'"
                ))
                schemas = res.scalars().all()
        except Exception as e:
            logger.error(f"Alert service: failed to list tenant schemas: {e}")

        for schema_name in schemas:
            # One broken tenant must not stop checks for the rest.
            try:
                tenant_schema.set(schema_name)

                async with tenant_session() as db:
                    # Query servers that have enabled alert rules
                    result = await db.execute(
                        select(Server)
                        .where(Server.alert_rules.any(AlertRule.enabled == True))
                    )
                    servers = result.scalars().all()
                    
                    for server in servers:
                        rules_res = await db.execute(
                            select(AlertRule).where(AlertRule.server_id == server.id, AlertRule.enabled == True)
                        )
                        rules = rules_res.scalars().all()
                        
                        for rule in rules:
                            is_breached, metric_val = await evaluate_rule(rule, server)
                            
                            # Check active records
                            active_record_res = await db.execute(
                                select(AlertRecord)
                                .where(AlertRecord.rule_id == rule.id)
                                .where(AlertRecord.status.in_([AlertStatus.active, AlertStatus.acknowledged]))
                            )
                            active_record = active_record_res.scalars().first()
                            
                            if is_breached:
                                if not active_record:
                                    # Create new alert record
                                    new_alert = AlertRecord(
                                        rule_id=rule.id,
                                        server_id=server.id,
                                        metric_value=metric_val,
                                        status=AlertStatus.active,
                                        triggered_at=datetime.now(timezone.utc)
                                    )
                                    db.add(new_alert)
                                    await db.commit()
                                    await db.refresh(new_alert)
                                    
                                    # Trigger AI diagnosis in background
                                    task = asyncio.create_task(
                                        run_diagnosis(
                                            alert_record_id=new_alert.id,
                                            server_id=server.id,
                                            schema_name=schema_name,
                                            metric_value=metric_val
                                        )
                                    )
                                    _diagnosis_tasks.add(task)
                                    task.add_done_callback(_diagnosis_tasks.discard)
                                    
                                    # Broadcast alert
                                    await forward_to_watchers(str(server.id), {
                                        "type": "alert_fired",
                                        "data": {
                                            "alert_id": str(new_alert.id),
                                            "rule_name": rule.name,
                                            "metric": rule.metric.value,
                                            "metric_value": metric_val,
                                            "server_id": str(server.id),
                                            "server_name": server.name
                                        }
                                    })
                            else:
                                if active_record:
                                    # Resolve alert
                                    active_record.status = AlertStatus.resolved
                                    active_record.resolved_at = datetime.now(timezone.utc)
                                    await db.commit()
                                    
            except Exception as e:
                logger.error(f"Alert service error in {schema_name}: {e}")

        await asyncio.sleep(60)
