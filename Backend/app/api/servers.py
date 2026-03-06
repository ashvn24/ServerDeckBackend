import secrets
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.user import User
from app.models.server import Server
from app.schemas.server import ServerCreate, ServerResponse

router = APIRouter(prefix="/api/servers", tags=["servers"])
settings = get_settings()


@router.get("/", response_model=list[ServerResponse])
async def list_servers(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Server).where(Server.team_id == user.team_id).order_by(Server.created_at.desc())
    )
    return result.scalars().all()


@router.post("/", response_model=ServerResponse, status_code=status.HTTP_201_CREATED)
async def create_server(
    data: ServerCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    server = Server(
        name=data.name,
        team_id=user.team_id,
        agent_token=secrets.token_urlsafe(32),
    )
    db.add(server)
    await db.flush()
    await db.refresh(server)
    return server


@router.get("/{server_id}", response_model=ServerResponse)
async def get_server(
    server_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Server).where(Server.id == server_id, Server.team_id == user.team_id)
    )
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    return server


@router.delete("/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_server(
    server_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Server).where(Server.id == server_id, Server.team_id == user.team_id)
    )
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    await db.delete(server)


@router.get("/{server_id}/install-command")
async def get_install_command(
    server_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Server).where(Server.id == server_id, Server.team_id == user.team_id)
    )
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")

    # Build the install command
    base = settings.portal_base_url.rstrip("/")
    ws_scheme = "wss" if base.startswith("https") else "ws"
    ws_host = base.replace("https://", "").replace("http://", "")
    portal_ws = f"{ws_scheme}://{ws_host}/ws/agent"
    install_cmd = (
        f"curl -s {base}/install.sh | "
        f"bash -s -- --token={server.agent_token} --portal={portal_ws}"
    )
    return {"install_command": install_cmd, "agent_token": server.agent_token}
