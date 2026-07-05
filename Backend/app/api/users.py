import secrets
import datetime
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from passlib.hash import bcrypt

from app.database import get_db
from app.middleware.auth import get_current_user, require_admin, require_owner, require_support
from app.models.user import User, UserInvite
from app.schemas.user import UserInviteCreate, UserAcceptInvite, UserManagementResponse, UserDirectCreate, UserResponse, UserModulesUpdate, PasswordChangeRequest, TwoFactorSetupRequest, TwoFactorVerifyRequest, TwoFactorDisableRequest

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
    from app.services.tenant import get_user_resolved_modules, get_org_enabled_modules
    
    schema_name = tenant_schema.get(None)
    resolved = await get_user_resolved_modules(db, user, schema_name)
    org_mods = await get_org_enabled_modules(db, schema_name)
    
    resp = UserResponse.model_validate(user)
    resp.enabled_modules = resolved
    resp.org_modules = org_mods
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

    from app.database import tenant_schema
    from app.services.tenant import get_user_resolved_modules, get_org_enabled_modules
    schema_name = tenant_schema.get(None)
    org_modules = await get_org_enabled_modules(db, schema_name)

    if data.enabled_modules is not None:
        target_user.enabled_modules = [m for m in data.enabled_modules if m in org_modules]
    else:
        target_user.enabled_modules = None

    await db.commit()
    await db.refresh(target_user)

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


@router.post("/change-password")
async def change_password(
    data: PasswordChangeRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Change password for the currently authenticated user."""
    if not bcrypt.verify(data.current_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incorrect current password"
        )
    user.password_hash = bcrypt.hash(data.new_password)
    await db.commit()
    return {"message": "Password changed successfully"}


@router.post("/2fa/setup")
async def setup_2fa(
    data: TwoFactorSetupRequest,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Initiate Two-Factor Authentication setup.
    If method is 'totp', returns a generated secret and Google Charts QR code URL.
    If method is 'email', generates and sends a verification OTP to the user's email.
    """
    if user.two_factor_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Two-factor authentication is already enabled."
        )

    if data.method not in ("totp", "email"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid 2FA method. Choose 'totp' or 'email'."
        )

    if data.method == "totp":
        from app.services.totp import generate_base32_secret
        secret = generate_base32_secret()
        # Create standard OTPAuth URI for authenticator apps
        otpauth_uri = f"otpauth://totp/ServerDeck:{user.email}?secret={secret}&issuer=ServerDeck"
        # Generate Google Charts QR code URL
        import urllib.parse
        encoded_uri = urllib.parse.quote(otpauth_uri)
        qr_code_url = f"https://chart.googleapis.com/chart?chs=200x200&chld=M|0&cht=qr&chl={encoded_uri}"
        
        return {
            "method": "totp",
            "secret": secret,
            "qr_code_url": qr_code_url
        }
    
    elif data.method == "email":
        import secrets
        import datetime
        from app.services.email_service import send_otp_email
        
        code = f"{secrets.randbelow(1000000):06d}"
        expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=5)
        
        # Save OTP hash in user record (using bcrypt for secure hash)
        user.two_factor_otp_secret = bcrypt.hash(code)
        user.two_factor_otp_expires_at = expires_at
        await db.commit()
        
        # Send OTP
        background_tasks.add_task(
            send_otp_email,
            to_email=user.email,
            name=user.name,
            code=code
        )
        
        return {
            "method": "email",
            "message": "Verification code sent to your email."
        }


@router.post("/2fa/verify")
async def verify_2fa(
    data: TwoFactorVerifyRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Verify and enable Two-Factor Authentication.
    """
    if user.two_factor_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Two-factor authentication is already enabled."
        )

    if data.method not in ("totp", "email"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid 2FA method."
        )

    if data.method == "totp":
        if not data.secret:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Secret key is required for TOTP verification."
            )
            
        from app.services.totp import verify_totp
        if not verify_totp(data.secret, data.code):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid verification code."
            )
            
        user.two_factor_enabled = True
        user.two_factor_method = "totp"
        user.two_factor_secret = data.secret
        user.two_factor_otp_secret = None
        user.two_factor_otp_expires_at = None
        await db.commit()
        
        return {"message": "Two-factor authentication via Authenticator App enabled."}

    elif data.method == "email":
        if not user.two_factor_otp_secret or not user.two_factor_otp_expires_at:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No pending verification request found."
            )
            
        import datetime
        if user.two_factor_otp_expires_at < datetime.datetime.now(datetime.timezone.utc):
            user.two_factor_otp_secret = None
            user.two_factor_otp_expires_at = None
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Verification code has expired. Please try again."
            )
            
        if not bcrypt.verify(data.code, user.two_factor_otp_secret):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid verification code."
            )
            
        user.two_factor_enabled = True
        user.two_factor_method = "email"
        user.two_factor_secret = None
        user.two_factor_otp_secret = None
        user.two_factor_otp_expires_at = None
        await db.commit()
        
        return {"message": "Two-factor authentication via Email OTP enabled."}


@router.post("/2fa/disable")
async def disable_2fa(
    data: TwoFactorDisableRequest,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Disable Two-Factor Authentication.
    """
    if not user.two_factor_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Two-factor authentication is not enabled."
        )

    if user.two_factor_method == "email" and data.code == "send_otp":
        import secrets
        import datetime
        from app.services.email_service import send_otp_email
        
        code = f"{secrets.randbelow(1000000):06d}"
        expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=5)
        
        user.two_factor_otp_secret = bcrypt.hash(code)
        user.two_factor_otp_expires_at = expires_at
        await db.commit()
        
        background_tasks.add_task(
            send_otp_email,
            to_email=user.email,
            name=user.name,
            code=code
        )
        return {"mfa_challenge_sent": True, "message": "Verification code sent to your email."}

    # Verify code
    if user.two_factor_method == "totp":
        from app.services.totp import verify_totp
        if not verify_totp(user.two_factor_secret, data.code):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid verification code."
            )
    elif user.two_factor_method == "email":
        if not user.two_factor_otp_secret or not user.two_factor_otp_expires_at:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No active verification code. Request a code first."
            )
            
        import datetime
        if user.two_factor_otp_expires_at < datetime.datetime.now(datetime.timezone.utc):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Verification code has expired. Request a new code."
            )
            
        if not bcrypt.verify(data.code, user.two_factor_otp_secret):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid verification code."
            )

    user.two_factor_enabled = False
    user.two_factor_method = None
    user.two_factor_secret = None
    user.two_factor_otp_secret = None
    user.two_factor_otp_expires_at = None
    await db.commit()
    
    return {"message": "Two-factor authentication disabled."}
