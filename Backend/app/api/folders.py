from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from uuid import UUID

from app.database import get_db
from app.models.user import User
from app.models.server import Server, ServerFolder
from app.schemas.server import FolderCreate, FolderResponse
from app.middleware.auth import get_current_user

router = APIRouter(prefix="/api/folders", tags=["folders"])

@router.get("/", response_model=list[FolderResponse])
async def list_folders(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ServerFolder).where(ServerFolder.team_id == user.team_id)
    )
    return result.scalars().all()

@router.post("/", response_model=FolderResponse, status_code=status.HTTP_201_CREATED)
async def create_folder(
    data: FolderCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    folder = ServerFolder(
        name=data.name,
        parent_id=data.parent_id,
        team_id=user.team_id
    )
    db.add(folder)
    await db.commit()
    await db.refresh(folder)
    return folder

@router.delete("/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_folder(
    folder_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ServerFolder).where(ServerFolder.id == folder_id, ServerFolder.team_id == user.team_id)
    )
    folder = result.scalar_one_or_none()
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")
        
    # Unlink any servers inside this folder
    from sqlalchemy import update
    await db.execute(
        update(Server)
        .where(Server.folder_id == folder_id)
        .values(folder_id=None)
    )
    
    # Reparent child folders to parent's level
    await db.execute(
        update(ServerFolder)
        .where(ServerFolder.parent_id == folder_id)
        .values(parent_id=folder.parent_id)
    )
    
    await db.delete(folder)
    await db.commit()

@router.patch("/{folder_id}/move")
async def move_folder(
    folder_id: UUID,
    parent_id: UUID | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ServerFolder).where(ServerFolder.id == folder_id, ServerFolder.team_id == user.team_id)
    )
    folder = result.scalar_one_or_none()
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")
        
    folder.parent_id = parent_id
    await db.commit()
    return {"message": "Folder moved successfully"}
