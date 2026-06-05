"""
Admin API — platform owner only.
Endpoints for managing organizations and their initial superadmin users.
"""
from app.services.email_service import send_org_creation_email
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from passlib.context import CryptContext
from jose import jwt, JWTError
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.config import get_settings
from app.database import get_db
from app.models.organization import Organization, PlatformUser
from app.models.user import User, Team
from app.schemas.user import OrgCreate, OrgResponse, PlatformUserResponse, TokenResponse

router = APIRouter(prefix="/api/admin", tags=["admin"])
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
settings = get_settings()
bearer_scheme = HTTPBearer()


async def require_platform_owner(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> PlatformUser:
    """Dependency that ensures the caller is the platform owner."""
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    if not payload.get("is_platform_owner"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Platform owner access required")

    user_id = payload.get("sub")
    result = await db.execute(select(PlatformUser).where(PlatformUser.id == user_id))
    platform_user = result.scalar_one_or_none()
    if not platform_user or not platform_user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Platform owner account not found")

    return platform_user


# ── Setup ────────────────────────────────────────────────────────────────────

@router.post("/setup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def setup_platform_owner(
    name: str,
    email: str,
    password: str,
    db: AsyncSession = Depends(get_db),
):
    """One-time endpoint to create the platform owner account.
    Returns 409 if a platform owner already exists.
    """
    existing = await db.execute(select(PlatformUser))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Platform owner already exists")

    platform_user = PlatformUser(
        name=name,
        email=email,
        password_hash=pwd_context.hash(password),
    )
    db.add(platform_user)
    await db.commit()
    await db.refresh(platform_user)

    from app.api.auth import create_platform_owner_token
    token = create_platform_owner_token(platform_user)
    return TokenResponse(
        access_token=token,
        user=PlatformUserResponse(id=platform_user.id, name=platform_user.name, email=platform_user.email),
        is_platform_owner=True,
    )


# ── Organizations ─────────────────────────────────────────────────────────────

@router.get("/organizations", response_model=list[OrgResponse])
async def list_organizations(
    _: PlatformUser = Depends(require_platform_owner),
    db: AsyncSession = Depends(get_db),
):
    """List all provisioned organizations."""
    result = await db.execute(select(Organization).order_by(Organization.created_at.desc()))
    return result.scalars().all()


@router.post("/organizations", response_model=OrgResponse, status_code=status.HTTP_201_CREATED)
async def create_organization(
    background_tasks: BackgroundTasks,
    data: OrgCreate,
    _: PlatformUser = Depends(require_platform_owner),
    db: AsyncSession = Depends(get_db),
):
    """Create a new organization and provision its superadmin user."""
    from app.services.tenant import create_tenant_schema, run_tenant_migrations

    org_key = data.org_key.strip().lower().replace(" ", "_")
    schema_name = f"tenant_{org_key}"
    domain = data.domain.strip().lower()

    # Validate uniqueness
    existing_org = await db.execute(select(Organization).where(Organization.org_key == org_key))
    if existing_org.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Organization key already exists")

    existing_domain = await db.execute(select(Organization).where(Organization.domain == domain))
    if existing_domain.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Domain already registered")

    # Create organization record
    org = Organization(
        name=data.name,
        domain=domain,
        org_key=org_key,
        schema_name=schema_name,
    )
    db.add(org)
    await db.commit()

    # Provision schema + run migrations
    try:
        await create_tenant_schema(schema_name, db)
        run_tenant_migrations(schema_name)
    except Exception as e:
        await db.execute(text(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE"))
        await db.delete(org)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to initialize schema: {str(e)}"
        )

    # Create the org superadmin user inside the tenant schema
    await db.execute(text(f"SET search_path TO {schema_name}, public"))

    team = Team(name=f"{data.name} Team")
    db.add(team)
    await db.flush()

    admin_user = User(
        email=data.admin_email,
        password_hash=pwd_context.hash(data.admin_password),
        name=data.admin_name,
        team_id=team.id,
        role="owner",
    )
    db.add(admin_user)
    await db.commit()
    background_tasks.add_task(send_org_creation_email, data.admin_email, data.name, data.admin_name)
    await db.refresh(org)
    return org


@router.delete("/organizations/{org_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_organization(
    org_id: str,
    _: PlatformUser = Depends(require_platform_owner),
    db: AsyncSession = Depends(get_db),
):
    """Delete an organization and drop its schema entirely."""
    result = await db.execute(select(Organization).where(Organization.id == org_id))
    org = result.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")

    # Drop the tenant schema
    await db.execute(text(f"DROP SCHEMA IF EXISTS {org.schema_name} CASCADE"))
    await db.delete(org)
    await db.commit()
