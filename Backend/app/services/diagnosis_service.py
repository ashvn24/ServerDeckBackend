from app.models.alerting import AlertRecord
import logging
import json
import os
import httpx
from datetime import datetime, timezone
from sqlalchemy import select, desc
from app.config import get_settings
from app.models.alerting import AlertDiagnosis, AlertRule, AlertUrgency
from app.models.server import Server
from app.models.audit import AuditLog
from app.services.command_bridge import send_command_to_agent
from app.ws.client_handler import forward_to_watchers

logger = logging.getLogger("serverdeck.diagnosis")
settings = get_settings()

from app.database import tenant_session, tenant_schema

async def run_diagnosis(alert_record_id: str, server_id: str, schema_name: str, metric_value: float):
    # Set the schema context for this background task
    tenant_schema.set(schema_name)
    
    async with tenant_session() as tenant_db:
        # 1. Create diagnosis record (loading)
        diagnosis = AlertDiagnosis(
            alert_record_id=alert_record_id,
            failed=False
        )
        tenant_db.add(diagnosis)
        await tenant_db.commit()

        try:
            # Fetch server
            server = await tenant_db.get(Server, server_id)
            if not server:
                raise ValueError("Server not found")
                
            # Fetch rule
            record = await tenant_db.get(AlertRecord, alert_record_id)
            if not record:
                raise ValueError("Alert record not found")
            rule = await tenant_db.get(AlertRule, record.rule_id)

            # 2. Collect context
            logs = ""
            services = ""
            try:
                log_res = await send_command_to_agent(str(server.id), "logs.tail", {
                    "service": rule.service_name or "syslog",
                    "lines": 200
                })
                if log_res.get("status") == "success":
                    logs = log_res.get("data", "")
            except Exception as e:
                logs = f"Failed to fetch logs: {e}"

            try:
                svc_res = await send_command_to_agent(str(server.id), "systemd.list", {})
                if svc_res.get("status") == "success":
                    services = json.dumps(svc_res.get("data", []), indent=2)
            except Exception as e:
                services = f"Failed to fetch services: {e}"

            # Audit logs
            audit_res = await tenant_db.execute(
                select(AuditLog).where(AuditLog.server_id == server.id).order_by(desc(AuditLog.timestamp)).limit(20)
            )
            audit_entries_list = audit_res.scalars().all()
            audit_entries = "\n".join([f"{a.timestamp}: {a.action} - {a.details}" for a in audit_entries_list])

            # 3. Build prompt
            prompt = f"""You are a Linux server operations expert diagnosing a production alert.

ALERT: {rule.metric.value} threshold breached on server {server.name}
TRIGGER VALUE: {metric_value}
RULE: {rule.name}

CURRENT SERVER STATE:
CPU: {server.cpu_percent}% | RAM: {server.ram_used_mb}/{server.ram_total_mb}MB | Disk: {server.disk_used_percent}%

RECENT LOGS (last 200 lines):
{logs}

SERVICE STATUS:
{services}

RECENT COMMANDS RUN ON THIS SERVER:
{audit_entries}

Respond in this exact JSON format:
{{
  "explanation": "Plain English explanation of likely cause (2-3 sentences)",
  "suggested_fix": "What the user should do to resolve this",
  "suggested_command": "exact shell command to fix it, or null if not applicable",
  "urgency": "low|medium|high|critical"
}}
Return only the JSON. No preamble.
"""
        
            # 4. Call Groq API
            api_key = settings.grok_api_key
            if not api_key:
                raise ValueError("GROK_API_KEY environment variable not set")

            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "model": "llama-3.3-70b-versatile",
                        "messages": [
                            {"role": "user", "content": prompt}
                        ],
                        "response_format": {"type": "json_object"}
                    },
                    timeout=45.0
                )
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                
                # Robust JSON parsing in case the model returns markdown backticks
                clean_content = content.strip()
                if clean_content.startswith("```json"):
                    clean_content = clean_content[7:]
                if clean_content.startswith("```"):
                    clean_content = clean_content[3:]
                if clean_content.endswith("```"):
                    clean_content = clean_content[:-3]
                    
                result = json.loads(clean_content.strip())
                
                # 5. Update success
                diagnosis.explanation = result.get("explanation")
                diagnosis.suggested_fix = result.get("suggested_fix")
                diagnosis.suggested_command = result.get("suggested_command")
                
                urgency_str = result.get("urgency", "medium").lower()
                if urgency_str in [e.value for e in AlertUrgency]:
                    diagnosis.urgency = AlertUrgency(urgency_str)
                else:
                    diagnosis.urgency = AlertUrgency.medium
                    
                diagnosis.completed_at = datetime.now(timezone.utc)
                await tenant_db.commit()
                
                # Broadcast update
                await forward_to_watchers(str(server.id), {
                    "type": "alert_diagnosis_ready",
                    "data": {
                        "alert_id": str(alert_record_id),
                        "explanation": diagnosis.explanation,
                        "suggested_fix": diagnosis.suggested_fix,
                        "suggested_command": diagnosis.suggested_command,
                        "urgency": urgency_str
                    }
                })
            
        except Exception as e:
            logger.error(f"Diagnosis failed for alert {alert_record_id}: {e}")
            if diagnosis:
                diagnosis.failed = True
                diagnosis.failure_reason = str(e)
                diagnosis.completed_at = datetime.now(timezone.utc)
                await tenant_db.commit()
                
                # Broadcast failure update so UI stops spinning
                if 'server' in locals() and server:
                    await forward_to_watchers(str(server.id), {
                        "type": "alert_diagnosis_ready",
                        "data": {
                            "alert_id": str(alert_record_id),
                            "failed": True,
                            "failure_reason": str(e)
                        }
                    })
