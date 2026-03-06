from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.user import User
from app.models.server import Server
from app.models.site import Site
from app.schemas.site import SiteCreate, SiteResponse

router = APIRouter(prefix="/api/sites", tags=["sites"])


@router.get("/", response_model=list[SiteResponse])
async def list_sites(
    server_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Verify server belongs to user's team
    srv = await db.execute(
        select(Server).where(Server.id == server_id, Server.team_id == user.team_id)
    )
    if not srv.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")

    result = await db.execute(
        select(Site).where(Site.server_id == server_id).order_by(Site.created_at.desc())
    )
    return result.scalars().all()


@router.post("/", response_model=SiteResponse, status_code=status.HTTP_201_CREATED)
async def create_site(
    data: SiteCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Verify server belongs to user's team
    srv = await db.execute(
        select(Server).where(Server.id == data.server_id, Server.team_id == user.team_id)
    )
    if not srv.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")

    site = Site(**data.model_dump())
    db.add(site)
    await db.flush()
    await db.refresh(site)
    return site


@router.delete("/{site_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_site(
    site_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Site).where(Site.id == site_id))
    site = result.scalar_one_or_none()
    if not site:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Site not found")

    # Verify server belongs to user's team
    srv = await db.execute(
        select(Server).where(Server.id == site.server_id, Server.team_id == user.team_id)
    )
    if not srv.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    await db.delete(site)
