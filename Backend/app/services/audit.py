import uuid
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.audit import AuditLog

async def record_audit(
    db: AsyncSession,
    user_id: uuid.UUID | str,
    server_id: uuid.UUID | str,
    action: str,
    details: dict | None = None
):
    """
    Record an entry in the audit log.
    """
    if isinstance(user_id, str):
        user_id = uuid.UUID(user_id)
    if isinstance(server_id, str):
        server_id = uuid.UUID(server_id)
        
    log = AuditLog(
        user_id=user_id,
        server_id=server_id,
        action=action,
        details=details or {},
        timestamp=datetime.now(timezone.utc)
    )
    db.add(log)
    await db.commit()
