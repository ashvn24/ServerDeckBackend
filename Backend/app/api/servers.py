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
from app.services.audit import record_audit

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
    from app.models.organization import AgentTokenMapping
    from app.database import tenant_schema

    agent_token = secrets.token_urlsafe(32)
    server = Server(
        name=data.name,
        team_id=user.team_id,
        folder_id=data.folder_id,
        agent_token=agent_token,
    )
    db.add(server)
    await db.flush()

    # Store mapping in public schema
    current_schema = tenant_schema.get()
    token_mapping = AgentTokenMapping(
        agent_token=agent_token,
        schema_name=current_schema
    )
    db.add(token_mapping)
    await db.commit()
    
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

    # Log server deletion
    await record_audit(db, user.id, server_id, "server.delete", details={"name": server.name})

    # If the server is online, try to send an uninstall command
    if server.is_online:
        from app.ws.client_handler import _send_agent_fire_and_forget
        try:
            await _send_agent_fire_and_forget(
                server_id=str(server.id),
                action="agent.uninstall",
                params={}
            )
        except Exception as e:
            # We don't block deletion if uninstall fails (e.g. agent disconnected mid-request)
            print(f"Failed to send uninstall command to server {server.id}: {e}")

    # Delete token mapping from public schema
    from app.models.organization import AgentTokenMapping
    mapping_result = await db.execute(
        select(AgentTokenMapping).where(AgentTokenMapping.agent_token == server.agent_token)
    )
    mapping = mapping_result.scalar_one_or_none()
    if mapping:
        await db.delete(mapping)

    await db.delete(server)
    await record_audit(db, user.id, server_id, "server.delete", details={"name": server.name})
    await db.commit()


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

@router.patch("/{server_id}/move")
async def move_server(
    server_id: str,
    folder_id: str | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Server).where(Server.id == server_id, Server.team_id == user.team_id)
    )
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
        
    # Convert empty string to None
    server.folder_id = folder_id if folder_id else None
    await db.commit()
    return {"message": "Server moved successfully"}
