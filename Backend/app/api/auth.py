from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from passlib.context import CryptContext
from jose import jwt

from app.config import get_settings
from app.database import get_db
from app.models.user import User, Team
from app.models.organization import Organization, PlatformUser
from app.schemas.user import UserCreate, UserLogin, TokenResponse, UserResponse, PlatformUserResponse

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
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_platform_owner_token(platform_user: PlatformUser) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=settings.jwt_expire_hours)
    payload = {
        "sub": str(platform_user.id),
        "team_id": None,
        "tenant_schema": "public",
        "is_platform_owner": True,
        "exp": expire,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(data: UserCreate, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    from app.services.tenant import get_org_key_from_email, create_tenant_schema, run_tenant_migrations
    from app.services.email_service import send_org_creation_email
    from sqlalchemy import text

    org_key = get_org_key_from_email(data.email)
    if not org_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="Registration is only allowed for work/business emails."
        )

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
            await db.execute(text(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE"))
            await db.delete(new_org)
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to initialize organization schema: {str(e)}"
            )

        await db.execute(text(f"SET search_path TO {schema_name}, public"))

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

    token = create_access_token(user, schema_name)
    
    # Send welcome email asynchronously
    background_tasks.add_task(send_org_creation_email, data.email, org_key.capitalize(), data.name)

    return TokenResponse(
        access_token=token,
        user=UserResponse.model_validate(user),
        is_platform_owner=False,
    )


@router.post("/login", response_model=TokenResponse)
async def login(data: UserLogin, db: AsyncSession = Depends(get_db)):
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

    result = await db.execute(select(Organization).where(Organization.org_key == org_key))
    org = result.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    schema_name = org.schema_name
    await db.execute(text(f"SET search_path TO {schema_name}, public"))

    result = await db.execute(select(User).where(User.email == data.email))
    user = result.scalar_one_or_none()
    if not user or not pwd_context.verify(data.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled")

    token = create_access_token(user, schema_name)
    return TokenResponse(
        access_token=token,
        user=UserResponse.model_validate(user),
        is_platform_owner=False,
    )
