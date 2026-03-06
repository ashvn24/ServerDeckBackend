from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.user import User
from app.models.server import Server
from app.models.site import Site

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/")
async def get_dashboard(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Total servers
    total_servers = await db.execute(
        select(func.count(Server.id)).where(Server.team_id == user.team_id)
    )
    total = total_servers.scalar() or 0

    # Online servers
    online_servers = await db.execute(
        select(func.count(Server.id)).where(
            Server.team_id == user.team_id, Server.is_online == True  # noqa: E712
        )
    )
    online = online_servers.scalar() or 0

    # Total sites
    total_sites_q = await db.execute(
        select(func.count(Site.id))
        .join(Server, Site.server_id == Server.id)
        .where(Server.team_id == user.team_id)
    )
    total_sites = total_sites_q.scalar() or 0

    return {
        "total_servers": total,
        "online_servers": online,
        "offline_servers": total - online,
        "total_sites": total_sites,
    }
