"""
Admin API — platform owner only.
Endpoints for managing organizations and their initial superadmin users.
"""
import logging
import traceback
from app.services.email_service import send_org_creation_email
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from passlib.context import CryptContext
from jose import jwt, JWTError
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.config import get_settings
from app.database import get_db
from app.models.organization import Organization, PlatformUser, WaitlistRequest
from app.models.user import User, Team, UserInvite
from app.models.ticket import Ticket, TicketMessage
from app.schemas.user import OrgCreate, OrgResponse, PlatformUserResponse, TokenResponse, IndividualUserCreate, IndividualUserResponse, IndividualUserInviteResponse, WaitlistResponse, OrgModulesUpdate
from app.schemas.ticket import TicketResponse, TicketDetailResponse, TicketUpdate, TicketMessageCreate, TicketMessageResponse
router = APIRouter(prefix="/api/admin", tags=["admin"])
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
settings = get_settings()
bearer_scheme = HTTPBearer()
logger = logging.getLogger("serverdeck.admin")


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


@router.patch("/organizations/{org_id}/modules", response_model=OrgResponse)
async def update_organization_modules(
    org_id: str,
    data: OrgModulesUpdate,
    _: PlatformUser = Depends(require_platform_owner),
    db: AsyncSession = Depends(get_db),
):
    """Update enabled modules list for an organization."""
    result = await db.execute(select(Organization).where(Organization.id == org_id))
    org = result.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")

    org.enabled_modules = data.enabled_modules
    await db.commit()
    await db.refresh(org)
    return org


# ── Individual Users ──────────────────────────────────────────────────────────

@router.get("/users", response_model=list[IndividualUserResponse])
async def list_individual_users(
    _: PlatformUser = Depends(require_platform_owner),
    db: AsyncSession = Depends(get_db),
):
    """List all individual (personal email) users from the shared tenant_individual schema."""
    from app.services.tenant import INDIVIDUAL_SCHEMA, ensure_individual_schema_exists

    logger.info("[admin/users] Listing individual users")
    try:
        await ensure_individual_schema_exists(db)
        logger.info(f"[admin/users] Schema ready: {INDIVIDUAL_SCHEMA}, setting search_path")
        await db.execute(text(f"SET search_path TO {INDIVIDUAL_SCHEMA}, public"))
        result = await db.execute(select(User).order_by(User.created_at.desc()))
        users = result.scalars().all()
        logger.info(f"[admin/users] Found {len(users)} individual user(s)")
        return users
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"[admin/users] Unexpected error listing users: {exc}\n{traceback.format_exc()}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.post("/users", response_model=IndividualUserInviteResponse, status_code=status.HTTP_201_CREATED)
async def create_individual_user(
    data: IndividualUserCreate,
    background_tasks: BackgroundTasks,
    platform_user: PlatformUser = Depends(require_platform_owner),
    db: AsyncSession = Depends(get_db),
):
    """Invite a new individual user to the shared tenant_individual schema."""
    import secrets
    import datetime
    from app.services.tenant import INDIVIDUAL_SCHEMA, ensure_individual_schema_exists, is_personal_email

    logger.info(f"[admin/users] Inviting individual user: email={data.email}")

    # Step 1 — validate email domain
    is_personal = is_personal_email(data.email)
    logger.info(f"[admin/users] is_personal_email('{data.email}') = {is_personal}")
    if not is_personal:
        logger.warning(f"[admin/users] Rejected non-personal email: {data.email}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Use the Organizations section to onboard users with business email domains."
        )

    try:
        # Step 2 — ensure individual schema exists
        logger.info(f"[admin/users] Ensuring schema exists: {INDIVIDUAL_SCHEMA}")
        await ensure_individual_schema_exists(db)
        logger.info(f"[admin/users] Schema ready, setting search_path to {INDIVIDUAL_SCHEMA}")
        await db.execute(text(f"SET search_path TO {INDIVIDUAL_SCHEMA}, public"))

        # Step 3 — check for duplicate user
        logger.info(f"[admin/users] Checking for existing user with email: {data.email}")
        existing = await db.execute(select(User).where(User.email == data.email))
        if existing.scalar_one_or_none():
            logger.warning(f"[admin/users] Email already registered: {data.email}")
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")
            
        # Check for existing invite
        logger.info(f"[admin/users] Checking for existing invite with email: {data.email}")
        existing_invite = await db.execute(select(UserInvite).where(UserInvite.email == data.email))
        if existing_invite.scalar_one_or_none():
            logger.warning(f"[admin/users] Invite already sent to: {data.email}")
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Invite already sent to this email")

        # Step 4 — create Team
        team_name = data.email.split('@')[0].capitalize()
        logger.info(f"[admin/users] Creating team for user: {team_name}")
        team = Team(name=f"{team_name}'s Team")
        db.add(team)
        await db.flush()
        logger.info(f"[admin/users] Team created with id={team.id}")

        # Step 5 — create UserInvite
        logger.info(f"[admin/users] Creating user invite record")
        token = f"{secrets.token_urlsafe(32)}:individual"
        expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=7)
        
        invite = UserInvite(
            email=data.email,
            role="owner",
            team_id=team.id,
            token=token,
            expires_at=expires_at
        )
        db.add(invite)
        await db.commit()
        
        # Step 6 — send invite email
        from app.config import get_settings
        from app.services.email_service import send_invitation_email
        settings = get_settings()
        invite_url = f"{settings.ui_base_url}/invite?token={token}"
        
        background_tasks.add_task(
            send_invitation_email,
            to_email=data.email,
            inviter_name=platform_user.name,
            invite_link=invite_url,
            org_name="ServerDeck Personal"
        )
        
        logger.info(f"[admin/users] Individual user invite created successfully: email={data.email}, token={token}")
        
        return IndividualUserInviteResponse(
            message="Invitation sent successfully",
            token=token,
            invite_url=invite_url
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"[admin/users] Unexpected error creating user: {exc}\n{traceback.format_exc()}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Server error: {str(exc)}"
        )


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_individual_user(
    user_id: str,
    _: PlatformUser = Depends(require_platform_owner),
    db: AsyncSession = Depends(get_db),
):
    """Delete an individual user and their associated Team (and all cascaded data)."""
    from app.services.tenant import INDIVIDUAL_SCHEMA

    logger.info(f"[admin/users] Deleting individual user: id={user_id}")
    try:
        await db.execute(text(f"SET search_path TO {INDIVIDUAL_SCHEMA}, public"))
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            logger.warning(f"[admin/users] User not found for deletion: id={user_id}")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        logger.info(f"[admin/users] Found user email={user.email}, team_id={user.team_id}. Deleting team...")
        # Delete the user's Team — cascades to servers, folders, audit logs, etc.
        team_result = await db.execute(select(Team).where(Team.id == user.team_id))
        team = team_result.scalar_one_or_none()
        if team:
            await db.delete(team)
            logger.info(f"[admin/users] Team {team.id} deleted (cascades user + data)")
        else:
            logger.warning(f"[admin/users] No team found for user {user_id}, deleting user directly")
            await db.delete(user)

        await db.commit()
        logger.info(f"[admin/users] User {user_id} successfully deleted")

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"[admin/users] Unexpected error deleting user {user_id}: {exc}\n{traceback.format_exc()}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Server error: {str(exc)}"
        )


@router.patch("/users/{user_id}/modules", response_model=IndividualUserResponse)
async def update_individual_user_modules(
    user_id: str,
    data: OrgModulesUpdate,
    _: PlatformUser = Depends(require_platform_owner),
    db: AsyncSession = Depends(get_db),
):
    """Update enabled modules list for an individual user in the shared tenant_individual schema."""
    from app.services.tenant import INDIVIDUAL_SCHEMA
    import uuid
    target_uuid = uuid.UUID(user_id)
    
    await db.execute(text(f"SET search_path TO {INDIVIDUAL_SCHEMA}, public"))
    result = await db.execute(select(User).where(User.id == target_uuid))
    target_user = result.scalar_one_or_none()
    if not target_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        
    target_user.enabled_modules = data.enabled_modules
    await db.commit()
    await db.refresh(target_user)
    return target_user


# ── Tickets (Individual Users) ─────────────────────

@router.get("/tickets", response_model=list[TicketResponse])
async def list_individual_tickets(
    _: PlatformUser = Depends(require_platform_owner),
    db: AsyncSession = Depends(get_db),
):
    """List all tickets from individual users (tenant_individual)."""
    from app.services.tenant import INDIVIDUAL_SCHEMA, ensure_individual_schema_exists
    from sqlalchemy.orm import selectinload
    
    try:
        await ensure_individual_schema_exists(db)
        await db.execute(text(f"SET search_path TO {INDIVIDUAL_SCHEMA}, public"))
        
        query = select(Ticket).options(
            selectinload(Ticket.created_by),
            selectinload(Ticket.assigned_to)
        ).order_by(Ticket.updated_at.desc())
        
        result = await db.execute(query)
        return result.scalars().all()
    except Exception as exc:
        logger.error(f"[admin/tickets] Error listing tickets: {exc}\n{traceback.format_exc()}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.get("/tickets/{ticket_id}", response_model=TicketDetailResponse)
async def get_individual_ticket(
    ticket_id: str,
    _: PlatformUser = Depends(require_platform_owner),
    db: AsyncSession = Depends(get_db),
):
    """Get ticket details from tenant_individual."""
    from app.services.tenant import INDIVIDUAL_SCHEMA
    from sqlalchemy.orm import selectinload
    
    await db.execute(text(f"SET search_path TO {INDIVIDUAL_SCHEMA}, public"))
    
    result = await db.execute(
        select(Ticket)
        .where(Ticket.id == ticket_id)
        .options(
            selectinload(Ticket.created_by),
            selectinload(Ticket.assigned_to),
            selectinload(Ticket.messages).selectinload(TicketMessage.sender)
        )
    )
    ticket = result.scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
        
    ticket.messages = sorted(ticket.messages, key=lambda m: m.created_at)
    return ticket


@router.patch("/tickets/{ticket_id}", response_model=TicketResponse)
async def update_individual_ticket(
    ticket_id: str,
    data: TicketUpdate,
    _: PlatformUser = Depends(require_platform_owner),
    db: AsyncSession = Depends(get_db),
):
    """Update a ticket from tenant_individual (status only)."""
    from app.services.tenant import INDIVIDUAL_SCHEMA
    from sqlalchemy.orm import selectinload
    import datetime
    
    await db.execute(text(f"SET search_path TO {INDIVIDUAL_SCHEMA}, public"))
    
    result = await db.execute(
        select(Ticket)
        .where(Ticket.id == ticket_id)
        .options(selectinload(Ticket.created_by), selectinload(Ticket.assigned_to))
    )
    ticket = result.scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")

    if data.status is not None:
        ticket.status = data.status
        
    if data.priority is not None:
        ticket.priority = data.priority

    ticket.updated_at = datetime.datetime.now(datetime.timezone.utc)
    await db.flush()

    # Broadcast update to watchers
    from app.ws.client_handler import forward_to_ticket_watchers
    ticket_response = TicketResponse.model_validate(ticket)
    await forward_to_ticket_watchers(
        str(ticket.id),
        {"type": "ticket_update", "ticket": ticket_response.model_dump(mode="json")}
    )

    await db.commit()
    return ticket


@router.post("/tickets/{ticket_id}/messages", response_model=TicketMessageResponse, status_code=status.HTTP_201_CREATED)
async def add_individual_ticket_message(
    ticket_id: str,
    data: TicketMessageCreate,
    _: PlatformUser = Depends(require_platform_owner),
    db: AsyncSession = Depends(get_db),
):
    """Add a message to a ticket from tenant_individual as the platform admin."""
    from app.services.tenant import INDIVIDUAL_SCHEMA
    from sqlalchemy.orm import selectinload
    import datetime
    
    await db.execute(text(f"SET search_path TO {INDIVIDUAL_SCHEMA}, public"))
    
    result = await db.execute(select(Ticket).where(Ticket.id == ticket_id))
    ticket = result.scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
        
    message = TicketMessage(
        ticket_id=ticket.id,
        sender_id=None,  # None implies Platform Owner / System
        body=data.body,
        is_internal=data.is_internal,
    )
    db.add(message)
    
    ticket.updated_at = datetime.datetime.now(datetime.timezone.utc)
    await db.flush()
    
    # Load for response (sender will be None, which is handled gracefully by frontend)
    result_msg = await db.execute(
        select(TicketMessage)
        .where(TicketMessage.id == message.id)
        .options(selectinload(TicketMessage.sender))
    )
    db_message = result_msg.scalar_one()
    
    # Broadcast to watchers
    from app.ws.client_handler import forward_to_ticket_watchers
    msg_response = TicketMessageResponse.model_validate(db_message)
    await forward_to_ticket_watchers(
        str(ticket.id),
        {"type": "ticket_message", "message": msg_response.model_dump(mode="json")}
    )
    
    await db.commit()
    return db_message


# ── Waitlist (Platform Admin) ──────────────────────────────────────────────────

@router.get("/waitlist", response_model=list[WaitlistResponse])
async def list_waitlist(
    _: PlatformUser = Depends(require_platform_owner),
    db: AsyncSession = Depends(get_db)
):
    """List all waitlist requests and calculate their status."""
    from app.services.tenant import INDIVIDUAL_SCHEMA
    
    result = await db.execute(select(WaitlistRequest).order_by(WaitlistRequest.created_at.desc()))
    requests = result.scalars().all()
    if not requests:
        return []
        
    emails = [req.email for req in requests]
    
    await db.execute(text(f"SET search_path TO {INDIVIDUAL_SCHEMA}, public"))
    
    # Check who has an invite
    invite_result = await db.execute(select(UserInvite.email).where(UserInvite.email.in_(emails)))
    invited_emails = set(invite_result.scalars().all())
    
    response = []
    for req in requests:
        status = "invited" if req.email in invited_emails else "pending"
        response.append(
            WaitlistResponse(
                id=req.id, 
                email=req.email, 
                created_at=req.created_at, 
                status=status
            )
        )
    return response


@router.delete("/waitlist/{request_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_waitlist(
    request_id: str,
    _: PlatformUser = Depends(require_platform_owner),
    db: AsyncSession = Depends(get_db)
):
    """Reject/Delete a waitlist request."""
    result = await db.execute(select(WaitlistRequest).where(WaitlistRequest.id == request_id))
    req = result.scalar_one_or_none()
    if not req:
        raise HTTPException(status_code=404, detail="Waitlist request not found")
    await db.delete(req)
    await db.commit()


@router.post("/waitlist/{request_id}/approve", response_model=IndividualUserInviteResponse)
async def approve_waitlist(
    request_id: str,
    background_tasks: BackgroundTasks,
    platform_user: PlatformUser = Depends(require_platform_owner),
    db: AsyncSession = Depends(get_db)
):
    """Approve a waitlist request by creating an individual user account and sending an invite. If invited, resends."""
    result = await db.execute(select(WaitlistRequest).where(WaitlistRequest.id == request_id))
    req = result.scalar_one_or_none()
    if not req:
        raise HTTPException(status_code=404, detail="Waitlist request not found")

    email = req.email
    name = email.split("@")[0].title()

    from app.services.tenant import INDIVIDUAL_SCHEMA, ensure_individual_schema_exists
    import datetime
    import secrets
    from app.config import get_settings
    from app.services.email_service import send_invitation_email
    settings = get_settings()

    await ensure_individual_schema_exists(db)
    await db.execute(text(f"SET search_path TO {INDIVIDUAL_SCHEMA}, public"))

    user_result = await db.execute(select(User).where(User.email == email))
    if user_result.scalar_one_or_none():
        # If they already registered, we can just delete the waitlist request here
        await db.delete(req)
        await db.commit()
        raise HTTPException(status_code=400, detail="User already exists")

    # Check for existing invite
    existing_invite_res = await db.execute(select(UserInvite).where(UserInvite.email == email))
    existing_invite = existing_invite_res.scalar_one_or_none()
    
    if existing_invite:
        # Resend scenario
        token = existing_invite.token
        existing_invite.expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=7)
    else:
        # Create their personal Team
        team = Team(name=f"{name}'s Workspace")
        db.add(team)
        await db.flush()

        # Create invite token
        token = f"{secrets.token_urlsafe(32)}:individual"
        existing_invite = UserInvite(
            email=email,
            role="owner",
            team_id=team.id,
            token=token,
            expires_at=datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=7)
        )
        db.add(existing_invite)

    # Note: We do NOT delete the waitlist request here anymore.
    # It will be deleted when they actually accept the invite.
    await db.commit()

    invite_url = f"{settings.ui_base_url}/invite?token={token}"

    background_tasks.add_task(
        send_invitation_email,
        to_email=email,
        inviter_name=platform_user.name,
        invite_link=invite_url,
        org_name="ServerDeck Personal"
    )

    return IndividualUserInviteResponse(
        message="Waitlist approved and invitation sent successfully",
        token=token,
        invite_url=invite_url
    )
