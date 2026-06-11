from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.user import User
from app.models.server import Server
from app.services.command_bridge import execute_on_server

router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("/{server_id}")
async def fetch_logs(
    server_id: str,
    source: str = Query(..., description="systemd|nginx|pm2"),
    name: str = Query(..., description="Service/site/app name"),
    lines: int = Query(100, description="Number of log lines"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Server).where(Server.id == server_id, Server.team_id == user.team_id)
    )
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    from app.database import tenant_schema
    from app.services.tenant import get_user_resolved_modules
    schema_name = tenant_schema.get(None)
    resolved = await get_user_resolved_modules(db, user, schema_name)
    if source in ("nginx", "pm2", "systemd") and source not in resolved:
        raise HTTPException(status_code=403, detail=f"Module '{source}' is disabled for your account.")

    response = await execute_on_server(
        server=server,
        action="logs.fetch",
        params={"source": source, "name": name, "lines": lines},
    )
    return response
