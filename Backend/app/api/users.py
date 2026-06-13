import secrets
import datetime
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from passlib.hash import bcrypt

from app.database import get_db
from app.middleware.auth import get_current_user, require_admin, require_owner, require_support
from app.models.user import User, UserInvite
from app.schemas.user import UserInviteCreate, UserAcceptInvite, UserManagementResponse, UserDirectCreate, UserResponse, UserModulesUpdate

router = APIRouter(prefix="/api/users", tags=["users"])

@router.get("/", response_model=list[UserManagementResponse])
async def list_users(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(User).where(User.team_id == user.team_id).order_by(User.created_at.desc())
    )
    users = result.scalars().all()

    from app.database import tenant_schema
    from app.services.tenant import get_user_resolved_modules
    schema_name = tenant_schema.get(None)

    response_users = []
    for u in users:
        resolved = await get_user_resolved_modules(db, u, schema_name)
        user_data = UserManagementResponse.model_validate(u)
        user_data.enabled_modules = resolved
        user_data.custom_modules = u.enabled_modules
        response_users.append(user_data)

    return response_users


@router.get("/me", response_model=UserResponse)
async def get_my_profile(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve details and dynamically resolved modules for the logged in user."""
    from app.database import tenant_schema
    from app.services.tenant import get_user_resolved_modules
    
    schema_name = tenant_schema.get(None)
    resolved = await get_user_resolved_modules(db, user, schema_name)
    
    resp = UserResponse.model_validate(user)
    resp.enabled_modules = resolved
    return resp


@router.patch("/{user_id}/modules", response_model=UserManagementResponse)
async def update_user_modules(
    user_id: str,
    data: UserModulesUpdate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Update custom modules list for a user within the same organization."""
    import uuid
    target_uuid = uuid.UUID(user_id)
    result = await db.execute(
        select(User).where(User.id == target_uuid, User.team_id == admin.team_id)
    )
    target_user = result.scalar_one_or_none()
    if not target_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if target_user.role == "owner" and admin.role != "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the team owner can modify owner modules"
        )

    target_user.enabled_modules = data.enabled_modules
    await db.commit()
    await db.refresh(target_user)

    from app.database import tenant_schema
    from app.services.tenant import get_user_resolved_modules
    schema_name = tenant_schema.get(None)
    resolved = await get_user_resolved_modules(db, target_user, schema_name)

    resp = UserManagementResponse.model_validate(target_user)
    resp.enabled_modules = resolved
    resp.custom_modules = target_user.enabled_modules
    return resp



@router.post("/invite", status_code=status.HTTP_201_CREATED)
async def invite_user(
    data: UserInviteCreate,
    background_tasks: BackgroundTasks,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    # Check if user already exists
    existing_user = await db.execute(select(User).where(User.email == data.email))
    if existing_user.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User already exists")

    # Create invite
    from app.services.tenant import get_org_key_from_email
    org_key = get_org_key_from_email(admin.email)
    if not org_key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Could not determine organization from admin email")
        
    token = f"{secrets.token_urlsafe(32)}:{org_key}"
    expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=7)
    
    invite = UserInvite(
        email=data.email,
        role=data.role,
        team_id=admin.team_id,
        token=token,
        expires_at=expires_at
    )
    db.add(invite)
    await db.commit()

    # Send the invite email asynchronously
    from app.config import get_settings
    from app.services.email_service import send_invitation_email
    
    settings = get_settings()
    invite_url = f"{settings.ui_base_url}/invite?token={token}"
    
    background_tasks.add_task(
        send_invitation_email,
        to_email=data.email,
        inviter_name=admin.name,
        invite_link=invite_url,
        org_name=org_key.capitalize()
    )
    
    return {"message": "Invitation sent", "token": token}

@router.get("/invite-details/{token}")
async def get_invite_details(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(UserInvite).where(UserInvite.token == token))
    invite = result.scalar_one_or_none()
    
    if not invite:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found")
    
    if invite.expires_at < datetime.datetime.now(datetime.timezone.utc):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invitation expired")
        
    return {
        "email": invite.email,
        "role": invite.role
    }

@router.post("/accept-invite")
async def accept_invite(
    data: UserAcceptInvite,
    db: AsyncSession = Depends(get_db),
):
    # Verify invite
    result = await db.execute(select(UserInvite).where(UserInvite.token == data.token))
    invite = result.scalar_one_or_none()
    
    if not invite:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found")
    
    if invite.expires_at < datetime.datetime.now(datetime.timezone.utc):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invitation expired")

    # Create user
    user = User(
        name=data.name,
        email=invite.email,
        password_hash=bcrypt.hash(data.password),
        team_id=invite.team_id,
        role=invite.role,
        is_active=True
    )
    db.add(user)
    
    # Delete invite
    await db.delete(invite)

    # Clean up WaitlistRequest in public schema if it exists
    from app.models.organization import WaitlistRequest
    from sqlalchemy import text
    # The current search_path is tenant_individual, but WaitlistRequest is bound to 'public' schema
    waitlist_res = await db.execute(select(WaitlistRequest).where(WaitlistRequest.email == invite.email))
    waitlist_req = waitlist_res.scalar_one_or_none()
    if waitlist_req:
        await db.delete(waitlist_req)

    await db.commit()
    
    return {"message": "Account created successfully"}

@router.post("/direct", status_code=status.HTTP_201_CREATED)
async def create_user_direct(
    data: UserDirectCreate,
    owner: User = Depends(require_owner),
    db: AsyncSession = Depends(get_db),
):
    # Check if user already exists
    existing_user = await db.execute(select(User).where(User.email == data.email))
    if existing_user.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User already exists")

    # Create user directly
    user = User(
        name=data.name,
        email=data.email,
        password_hash=bcrypt.hash(data.password),
        team_id=owner.team_id,
        role=data.role,
        is_active=True
    )
    db.add(user)
    await db.commit()
    
    return {"message": "User created successfully", "id": str(user.id)}

@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: str,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    if str(admin.id) == user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot delete your own account")
        
    result = await db.execute(
        select(User).where(User.id == user_id, User.team_id == admin.team_id)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        
    await db.delete(user)
    await db.commit()
