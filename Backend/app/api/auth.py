import logging
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from passlib.context import CryptContext
from jose import jwt

from app.config import get_settings
from app.database import get_db, set_search_path, validate_schema_name
from app.security import encode_token, decode_token

logger = logging.getLogger("serverdeck.auth")
from app.models.user import User, Team
from app.models.organization import Organization, PlatformUser, WaitlistRequest, PasswordResetToken
from app.schemas.user import UserCreate, UserLogin, TokenResponse, UserResponse, PlatformUserResponse, WaitlistCreate, WaitlistResponse, ForgotPasswordRequest, PasswordResetRequest, TwoFactorLoginRequest
from app.services.tenant import INDIVIDUAL_SCHEMA
from app.services.audit import record_audit

router = APIRouter(prefix="/api/auth", tags=["auth"])
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
settings = get_settings()


def create_access_token(user: User, tenant_schema: str, is_platform_owner: bool = False) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=settings.jwt_expire_hours)
    payload = {
        "sub": str(user.id),
        "team_id": str(user.team_id),
        "tenant_schema": tenant_schema,
        "is_platform_owner": is_platform_owner,
        "exp": expire,
    }
    return encode_token(payload)


def create_platform_owner_token(platform_user: PlatformUser) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=settings.jwt_expire_hours)
    payload = {
        "sub": str(platform_user.id),
        "team_id": None,
        "tenant_schema": "public",
        "is_platform_owner": True,
        "exp": expire,
    }
    return encode_token(payload)


def create_mfa_token(user: User, tenant_schema: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=5)
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "tenant_schema": tenant_schema,
        "mfa_handshake": True,
        "exp": expire,
    }
    return encode_token(payload)


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(data: UserCreate, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    from app.services.tenant import get_org_key_from_email, create_tenant_schema, run_tenant_migrations, ensure_individual_schema_exists
    from app.services.email_service import send_org_creation_email
    from sqlalchemy import text

    org_key = get_org_key_from_email(data.email)
    if not org_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid email address."
        )

    # ── Individual user (personal email: gmail, outlook, etc.) ──────────────
    if org_key == "individual":
        schema_name = INDIVIDUAL_SCHEMA
        # Ensure the shared individual schema exists (lazy init, idempotent)
        await ensure_individual_schema_exists(db)
        await set_search_path(db, schema_name)

        # Check if this personal email is already registered
        existing = await db.execute(select(User).where(User.email == data.email))
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

        # Each individual user gets their own Team for data isolation
        team = Team(name=f"{data.name}'s Team")
        db.add(team)
        await db.flush()

        user = User(
            email=data.email,
            password_hash=pwd_context.hash(data.password),
            name=data.name,
            team_id=team.id,
            role="owner",
        )
        db.add(user)
        await db.flush()

        await record_audit(db, user.id, None, "auth.register", details={"email": data.email})
        token = create_access_token(user, schema_name)
        background_tasks.add_task(send_org_creation_email, data.email, "ServerDeck", data.name)
        
        from app.services.tenant import get_user_resolved_modules, get_org_enabled_modules
        resolved = await get_user_resolved_modules(db, user, schema_name)
        org_mods = await get_org_enabled_modules(db, schema_name)
        user_resp = UserResponse.model_validate(user)
        user_resp.enabled_modules = resolved
        user_resp.org_modules = org_mods

        return TokenResponse(
            access_token=token,
            user=user_resp,
            is_platform_owner=False,
        )

    # ── Organization / business email ────────────────────────────────────────
    # Check if organization domain is already registered
    result = await db.execute(select(Organization).where(Organization.org_key == org_key))
    org = result.scalar_one_or_none()

    schema_name = f"tenant_{org_key}"

    if org:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Your organization is already registered. Please ask your administrator for an invitation."
        )
    else:
        # Onboard new organization
        new_org = Organization(
            name=org_key.capitalize(),
            domain=data.email.split("@")[1].strip().lower(),
            org_key=org_key,
            schema_name=schema_name
        )
        db.add(new_org)
        await db.commit()

        try:
            await create_tenant_schema(schema_name, db)
            run_tenant_migrations(schema_name)
        except Exception as e:
            logger.error(f"Failed to initialize organization schema {schema_name}: {e}")
            await db.execute(
                text("DROP SCHEMA IF EXISTS " + validate_schema_name(schema_name) + " CASCADE")
            )
            await db.delete(new_org)
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to initialize organization. Please try again later."
            )

        await set_search_path(db, schema_name)

    # Check if user already exists
    existing = await db.execute(select(User).where(User.email == data.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    # Create team
    team = Team(name=f"{data.name}'s Team")
    db.add(team)
    await db.flush()

    # Create user
    user = User(
        email=data.email,
        password_hash=pwd_context.hash(data.password),
        name=data.name,
        team_id=team.id,
        role="owner",
    )
    db.add(user)
    await db.flush()

    await record_audit(db, user.id, None, "auth.register", details={"email": data.email})

    token = create_access_token(user, schema_name)

    # Send welcome email asynchronously
    background_tasks.add_task(send_org_creation_email, data.email, org_key.capitalize(), data.name)

    from app.services.tenant import get_user_resolved_modules, get_org_enabled_modules
    resolved = await get_user_resolved_modules(db, user, schema_name)
    org_mods = await get_org_enabled_modules(db, schema_name)
    user_resp = UserResponse.model_validate(user)
    user_resp.enabled_modules = resolved
    user_resp.org_modules = org_mods

    return TokenResponse(
        access_token=token,
        user=user_resp,
        is_platform_owner=False,
    )


@router.post("/login", response_model=TokenResponse)
async def login(data: UserLogin, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    from app.services.tenant import get_org_key_from_email
    from sqlalchemy import text

    # --- Check platform owner first (public.platform_users) ---
    result = await db.execute(
        select(PlatformUser).where(PlatformUser.email == data.email)
    )
    platform_user = result.scalar_one_or_none()
    if platform_user:
        if not pwd_context.verify(data.password, platform_user.password_hash):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
        if not platform_user.is_active:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled")

        token = create_platform_owner_token(platform_user)
        # Return a compatible user response for the platform owner
        owner_response = PlatformUserResponse(
            id=platform_user.id,
            name=platform_user.name,
            email=platform_user.email,
        )
        return TokenResponse(
            access_token=token,
            user=owner_response,
            is_platform_owner=True,
        )

    # --- Standard tenant user login ---
    org_key = get_org_key_from_email(data.email)
    if not org_key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid email domain")

    # ── Individual user (personal email) ────────────────────────────────────
    if org_key == "individual":
        schema_name = INDIVIDUAL_SCHEMA
        await set_search_path(db, schema_name)
        result = await db.execute(select(User).where(User.email == data.email))
        user = result.scalar_one_or_none()
        if not user or not pwd_context.verify(data.password, user.password_hash):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
        if not user.is_active:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled")

        if user.two_factor_enabled:
            mfa_token = create_mfa_token(user, schema_name)
            if user.two_factor_method == "email":
                import secrets
                import datetime
                from app.services.email_service import send_otp_email
                code = f"{secrets.randbelow(1000000):06d}"
                user.two_factor_otp_secret = pwd_context.hash(code)
                user.two_factor_otp_expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=5)
                await db.commit()
                background_tasks.add_task(
                    send_otp_email,
                    to_email=user.email,
                    name=user.name,
                    code=code
                )
            return TokenResponse(
                mfa_required=True,
                mfa_token=mfa_token,
                mfa_method=user.two_factor_method
            )

        await record_audit(db, user.id, None, "auth.login", details={"email": data.email})
        token = create_access_token(user, schema_name)
        from app.services.tenant import get_user_resolved_modules, get_org_enabled_modules
        resolved = await get_user_resolved_modules(db, user, schema_name)
        org_mods = await get_org_enabled_modules(db, schema_name)
        user_resp = UserResponse.model_validate(user)
        user_resp.enabled_modules = resolved
        user_resp.org_modules = org_mods
        return TokenResponse(
            access_token=token,
            user=user_resp,
            is_platform_owner=False,
        )

    # ── Organization / business email ────────────────────────────────────────
    result = await db.execute(select(Organization).where(Organization.org_key == org_key))
    org = result.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    schema_name = org.schema_name
    await set_search_path(db, schema_name)

    result = await db.execute(select(User).where(User.email == data.email))
    user = result.scalar_one_or_none()
    if not user or not pwd_context.verify(data.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled")

    if user.two_factor_enabled:
        mfa_token = create_mfa_token(user, schema_name)
        if user.two_factor_method == "email":
            import secrets
            import datetime
            from app.services.email_service import send_otp_email
            code = f"{secrets.randbelow(1000000):06d}"
            user.two_factor_otp_secret = pwd_context.hash(code)
            user.two_factor_otp_expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=5)
            await db.commit()
            background_tasks.add_task(
                send_otp_email,
                to_email=user.email,
                name=user.name,
                code=code
            )
        return TokenResponse(
            mfa_required=True,
            mfa_token=mfa_token,
            mfa_method=user.two_factor_method
        )

    await record_audit(db, user.id, None, "auth.login", details={"email": data.email})

    token = create_access_token(user, schema_name)
    from app.services.tenant import get_user_resolved_modules, get_org_enabled_modules
    resolved = await get_user_resolved_modules(db, user, schema_name)
    org_mods = await get_org_enabled_modules(db, schema_name)
    user_resp = UserResponse.model_validate(user)
    user_resp.enabled_modules = resolved
    user_resp.org_modules = org_mods
    return TokenResponse(
        access_token=token,
        user=user_resp,
        is_platform_owner=False,
    )


@router.post("/login/2fa", response_model=TokenResponse)
async def login_2fa(data: TwoFactorLoginRequest, db: AsyncSession = Depends(get_db)):
    """
    Verify the 2FA code and return full login tokens.
    """
    from jose import jwt
    from app.config import get_settings
    settings = get_settings()

    try:
        payload = decode_token(data.mfa_token)
        if not payload.get("mfa_handshake"):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid verification token.")
        user_id = payload.get("sub")
        email = payload.get("email")
        schema_name = payload.get("tenant_schema")
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired verification token.")

    # Switch path to user schema
    await set_search_path(db, schema_name)

    # Fetch user
    import uuid
    user_uuid = uuid.UUID(user_id)
    result = await db.execute(select(User).where(User.id == user_uuid))
    user = result.scalar_one_or_none()
    if not user or not user.is_active or not user.two_factor_enabled:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid user or 2FA not enabled.")

    # Verify code
    if user.two_factor_method == "totp":
        from app.services.totp import verify_totp
        if not verify_totp(user.two_factor_secret, data.code):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid verification code.")
    elif user.two_factor_method == "email":
        if not user.two_factor_otp_secret or not user.two_factor_otp_expires_at:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No active verification code found.")

        import datetime
        if user.two_factor_otp_expires_at < datetime.datetime.now(datetime.timezone.utc):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Verification code has expired.")

        if not pwd_context.verify(data.code, user.two_factor_otp_secret):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid verification code.")

        # Clear OTP values
        user.two_factor_otp_secret = None
        user.two_factor_otp_expires_at = None
        await db.flush()

    await record_audit(db, user.id, None, "auth.login.2fa", details={"email": email})

    token = create_access_token(user, schema_name)
    from app.services.tenant import get_user_resolved_modules, get_org_enabled_modules
    resolved = await get_user_resolved_modules(db, user, schema_name)
    org_mods = await get_org_enabled_modules(db, schema_name)
    user_resp = UserResponse.model_validate(user)
    user_resp.enabled_modules = resolved
    user_resp.org_modules = org_mods

    # Revert to public to commit
    await set_search_path(db, "public")
    await db.commit()

    return TokenResponse(
        access_token=token,
        user=user_resp,
        is_platform_owner=False,
    )


# ── Waitlist (Public) ────────────────────────────────────────────────────────

@router.post("/waitlist", response_model=WaitlistResponse, status_code=status.HTTP_201_CREATED)
async def join_waitlist(data: WaitlistCreate, db: AsyncSession = Depends(get_db)):
    """Join the waitlist/request access from the login or landing page."""
    # Check if they are already on the waitlist
    result = await db.execute(select(WaitlistRequest).where(WaitlistRequest.email == data.email))
    existing = result.scalar_one_or_none()
    if existing:
        if data.name:
            existing.name = data.name
        if data.request_type:
            existing.request_type = data.request_type
        if data.org_name:
            existing.org_name = data.org_name
        if data.password:
            existing.password_hash = pwd_context.hash(data.password)
        await db.commit()
        await db.refresh(existing)
        return existing

    # Check if email is already registered in individual schema
    from app.services.tenant import INDIVIDUAL_SCHEMA, ensure_individual_schema_exists, get_org_key_from_email
    await ensure_individual_schema_exists(db)
    await set_search_path(db, INDIVIDUAL_SCHEMA)
    user_result = await db.execute(select(User).where(User.email == data.email))
    if user_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email is already registered as a personal user."
        )

    # Check if email belongs to an organization domain and if organization user already exists
    org_key = get_org_key_from_email(data.email)
    if org_key and org_key != "individual":
        # Switch path to public schema to check Organization
        await set_search_path(db, "public")
        org_result = await db.execute(select(Organization).where(Organization.org_key == org_key))
        org = org_result.scalar_one_or_none()
        if org:
            await set_search_path(db, org.schema_name)
            org_user_result = await db.execute(select(User).where(User.email == data.email))
            if org_user_result.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Email is already registered under your organization. Please ask your admin for an invite."
                )

    # Hashing password if provided
    password_hash = pwd_context.hash(data.password) if data.password else None

    # Reset search path to public for public schema operations
    await set_search_path(db, "public")

    waitlist_req = WaitlistRequest(
        email=data.email,
        name=data.name,
        request_type=data.request_type,
        org_name=data.org_name,
        password_hash=password_hash
    )
    db.add(waitlist_req)
    await db.commit()
    await db.refresh(waitlist_req)
    return waitlist_req


@router.post("/forgot-password")
async def forgot_password(
    data: ForgotPasswordRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """Request a password reset link by email."""
    # Prevent user enumeration: if anything fails, return success anyway.
    success_resp = {"message": "If this email is registered, a password reset link has been sent."}

    from app.services.tenant import get_org_key_from_email, INDIVIDUAL_SCHEMA, ensure_individual_schema_exists
    from app.services.email_service import send_password_reset_email

    email = data.email.strip().lower()
    org_key = get_org_key_from_email(email)
    if not org_key:
        return success_resp

    schema_name = INDIVIDUAL_SCHEMA
    if org_key != "individual":
        # Switch path to public schema to check Organization
        await set_search_path(db, "public")
        org_result = await db.execute(select(Organization).where(Organization.org_key == org_key))
        org = org_result.scalar_one_or_none()
        if not org:
            return success_resp
        schema_name = org.schema_name

    # Set path to target schema to verify user exists
    await set_search_path(db, schema_name)
    user_result = await db.execute(select(User).where(User.email == email))
    user = user_result.scalar_one_or_none()
    if not user:
        return success_resp

    # Reset search path to public to save token
    await set_search_path(db, "public")

    # Generate token
    import secrets
    import datetime
    token = secrets.token_urlsafe(32)
    expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)

    # Delete any existing active reset tokens for this email to avoid duplicates
    existing_tokens = await db.execute(
        select(PasswordResetToken).where(PasswordResetToken.email == email)
    )
    for tok in existing_tokens.scalars().all():
        await db.delete(tok)

    reset_token = PasswordResetToken(
        email=email,
        schema_name=schema_name,
        token=token,
        expires_at=expires_at
    )
    db.add(reset_token)
    await db.commit()

    # Send email
    reset_link = f"{settings.ui_base_url}/reset-password?token={token}"
    background_tasks.add_task(
        send_password_reset_email,
        to_email=email,
        name=user.name,
        reset_link=reset_link
    )

    return success_resp


@router.post("/reset-password")
async def reset_password(
    data: PasswordResetRequest,
    db: AsyncSession = Depends(get_db)
):
    """Reset user password using a valid reset token."""
    # Ensure starting in public schema
    await set_search_path(db, "public")

    result = await db.execute(
        select(PasswordResetToken).where(PasswordResetToken.token == data.token)
    )
    reset_token = result.scalar_one_or_none()
    if not reset_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token."
        )

    # Check expiration
    import datetime
    if reset_token.expires_at < datetime.datetime.now(datetime.timezone.utc):
        await db.delete(reset_token)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token."
        )

    # Switch search path to target schema
    await set_search_path(db, reset_token.schema_name)

    # Fetch and update user
    user_result = await db.execute(
        select(User).where(User.email == reset_token.email)
    )
    user = user_result.scalar_one_or_none()
    if not user:
        # Revert path to public to delete token
        await set_search_path(db, "public")
        await db.delete(reset_token)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token."
        )

    user.password_hash = pwd_context.hash(data.new_password)
    await db.flush()

    # Revert path to public to delete token & commit
    await set_search_path(db, "public")
    await db.delete(reset_token)
    await db.commit()

    return {"message": "Password reset successfully. You can now log in."}
