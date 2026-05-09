from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
import uuid

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.user import User
from app.models.audit import AuditLog
from app.models.server import Server

router = APIRouter(prefix="/api/audit", tags=["audit"])

@router.get("/")
async def list_audit_logs(
    server_id: str | None = Query(None),
    limit: int = Query(50),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(AuditLog)
        .join(Server)
        .where(Server.team_id == user.team_id)
        .options(selectinload(AuditLog.user), selectinload(AuditLog.server))
        .order_by(AuditLog.timestamp.desc())
    )
    
    if server_id:
        query = query.where(AuditLog.server_id == uuid.UUID(server_id))
        
    query = query.limit(limit)
    
    result = await db.execute(query)
    logs = result.scalars().all()
    
    return [
        {
            "id": str(log.id),
            "timestamp": log.timestamp,
            "action": log.action,
            "details": log.details,
            "user": {
                "id": str(log.user.id),
                "name": log.user.name,
                "email": log.user.email
            },
            "server": {
                "id": str(log.server.id),
                "name": log.server.name
            }
        }
        for log in logs
    ]
