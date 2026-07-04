import os
import sys
import subprocess
import logging
from pathlib import Path
from starlette.requests import HTTPConnection, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("serverdeck.tenant")

COMMON_DOMAINS = {
    "gmail", "yahoo", "hotmail", "outlook", "icloud", "aol", 
    "zoho", "protonmail", "proton", "mail", "live", "msn",
    "googlemail", "me", "mac", "ymail", "rocketmail",
}

# Shared schema for all individual (personal email) users
INDIVIDUAL_SCHEMA = "tenant_individual"

def is_personal_email(email: str) -> bool:
    """Returns True if the email is from a common personal/consumer provider."""
    if not email or "@" not in email:
        return False
    domain = email.split("@")[1].strip().lower()
    parts = domain.split(".")
    if len(parts) >= 2:
        return parts[0] in COMMON_DOMAINS
    return False

def get_org_key_from_email(email: str) -> str | None:
    """
    Derive an org key from an email address.
    - Personal email domains (gmail, outlook, etc.) → returns "individual"
    - Business/work email domains → returns the domain prefix (e.g. "acme" from acme.com)
    - Invalid email → returns None
    """
    if not email or "@" not in email:
        return None
    domain = email.split("@")[1].strip().lower()
    parts = domain.split(".")
    if len(parts) >= 2:
        key = parts[0]
        if key in COMMON_DOMAINS:
            return "individual"
        return key
    return None

async def create_tenant_schema(schema_name: str, db: AsyncSession):
    """Run CREATE SCHEMA query in PostgreSQL."""
    from app.database import validate_schema_name
    schema_name = validate_schema_name(schema_name)
    await db.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema_name}"))
    await db.commit()

def run_tenant_migrations(schema_name: str):
    """Run Alembic migrations programmatically for a specific schema via a subprocess."""
    from app.config import get_settings
    settings = get_settings()
        
    env = os.environ.copy()
    env["DATABASE_URL"] = settings.database_url
    env["IS_TENANT_MIGRATION"] = "true"
    env["IS_TENANT_MIGRATION_SCHEMA"] = schema_name
    
    # Get Backend directory path
    backend_dir = Path(__file__).resolve().parent.parent.parent
    
    logger.info(f"[run_tenant_migrations] Starting Alembic upgrade for schema: {schema_name}")
    logger.info(f"[run_tenant_migrations] Backend dir: {backend_dir}")
    logger.info(f"[run_tenant_migrations] Python: {sys.executable}")
    
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env=env,
        cwd=str(backend_dir),
        capture_output=True,
        text=True
    )
    
    logger.info(f"[run_tenant_migrations] Return code: {result.returncode}")
    if result.stdout:
        logger.info(f"[run_tenant_migrations] STDOUT:\n{result.stdout}")
    if result.stderr:
        logger.info(f"[run_tenant_migrations] STDERR:\n{result.stderr}")
    
    if result.returncode != 0:
        logger.error(f"[run_tenant_migrations] FAILED for schema '{schema_name}'. stderr={result.stderr}")
        raise Exception(f"Alembic migration failed: {result.stderr}")
        
    logger.info(f"[run_tenant_migrations] Successfully migrated schema: {schema_name}")


# Track whether individual schema has been initialised in this process lifetime.
# Avoids re-running migrations on every individual user login/register.
_individual_schema_ready = False

async def ensure_individual_schema_exists(db: AsyncSession):
    """
    Lazily create and migrate the shared individual-user schema (tenant_individual).
    Safe to call multiple times — idempotent.
    """
    global _individual_schema_ready
    logger.info(f"[ensure_individual_schema] Called. already_ready={_individual_schema_ready}")
    if _individual_schema_ready:
        logger.info("[ensure_individual_schema] Schema already initialised this session — skipping.")
        return

    # Check if schema already exists in PostgreSQL
    logger.info(f"[ensure_individual_schema] Querying information_schema for '{INDIVIDUAL_SCHEMA}'")
    result = await db.execute(
        text("SELECT schema_name FROM information_schema.schemata WHERE schema_name = :s"),
        {"s": INDIVIDUAL_SCHEMA},
    )
    exists = result.scalar_one_or_none() is not None
    logger.info(f"[ensure_individual_schema] Schema exists in DB: {exists}")

    if not exists:
        logger.info(f"[ensure_individual_schema] Creating schema: {INDIVIDUAL_SCHEMA}")
        try:
            await create_tenant_schema(INDIVIDUAL_SCHEMA, db)
            logger.info(f"[ensure_individual_schema] Schema created, starting Alembic migrations...")
            run_tenant_migrations(INDIVIDUAL_SCHEMA)
            logger.info(f"[ensure_individual_schema] Migrations completed for {INDIVIDUAL_SCHEMA}")
        except Exception as exc:
            logger.error(f"[ensure_individual_schema] FAILED to create/migrate {INDIVIDUAL_SCHEMA}: {exc}")
            raise
    else:
        logger.info(f"[ensure_individual_schema] Schema {INDIVIDUAL_SCHEMA} already exists — skipping migration.")

    _individual_schema_ready = True
    logger.info(f"[ensure_individual_schema] Done. _individual_schema_ready=True")

async def resolve_tenant(conn: HTTPConnection) -> str | None:
    """Dependency/Helper to dynamically resolve and set the active tenant_schema ContextVar.
    
    Uses HTTPConnection (base class for both Request and WebSocket) so this works
    for both HTTP routes and WebSocket routes.
    """
    schema_name = None
    
    # 1. Resolve from Bearer Auth Token or WebSocket query param
    auth_header = conn.headers.get("Authorization", "")
    token = None
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    else:
        token = conn.query_params.get("token")

    if token:
        token = token.strip()
        if ":" in token:
            # Invite token: format is token_value:org_key
            parts = token.split(":")
            if len(parts) == 2:
                _, org_key = parts
                schema_name = f"tenant_{org_key}"
        else:
            try:
                from app.security import decode_token
                payload = decode_token(token)
                schema_name = payload.get("tenant_schema")
            except Exception:
                pass

    # 2. Resolve from request body (login, register, accept-invite) or path params (invite details).
    # When FastAPI injects HTTPConnection for an HTTP route, conn IS already a Request instance
    # (Request subclasses HTTPConnection). We can use it directly — no reconstruction needed.
    if not schema_name and isinstance(conn, Request):
        path = conn.url.path
        if path in ("/api/auth/register", "/api/auth/login", "/api/users/accept-invite"):
            try:
                import json
                # Request.body() caches internally so it can be read again by FastAPI
                body_bytes = await conn.body()
                body = json.loads(body_bytes)
                if "token" in body and ":" in body["token"]:
                    parts = body["token"].split(":")
                    if len(parts) == 2:
                        _, org_key = parts
                        schema_name = f"tenant_{org_key}"
                elif "email" in body:
                    org_key = get_org_key_from_email(body["email"])
                    if org_key == "individual":
                        schema_name = INDIVIDUAL_SCHEMA
                    elif org_key:
                        schema_name = f"tenant_{org_key}"
            except Exception as e:
                logger.error(f"Error resolving tenant from body: {e}")
        elif "/api/users/invite-details/" in path:
            token_param = path.split("/")[-1]
            if ":" in token_param:
                parts = token_param.split(":")
                if len(parts) == 2:
                    _, org_key = parts
                    schema_name = f"tenant_{org_key}"

    if schema_name:
        from app.database import tenant_schema, validate_schema_name
        try:
            # Reject malformed schema names (e.g. injection via invite token
            # org_key) before they can reach any raw SET search_path statement.
            schema_name = validate_schema_name(schema_name)
        except ValueError:
            logger.warning("Rejected invalid tenant schema name during resolution")
            return None
        tenant_schema.set(schema_name)

    return schema_name


async def get_user_resolved_modules(db: AsyncSession, user, tenant_schema_name: str | None) -> list[str]:
    """Dynamically compute the active features/modules list for a user.
    Takes into account user-level custom settings and organization-level module settings."""
    from app.models.organization import Organization
    from sqlalchemy import select

    DEFAULT_MODULES = [
        "dashboard", "servers", "tickets", "settings",
        "nginx", "pm2", "systemd", "automation",
        "firewall", "processes", "ssl", "ssh", "files", "luxegenie", "sql"
    ]

    if getattr(user, "role", None) == "support":
        return ["tickets", "settings"]

    if tenant_schema_name and tenant_schema_name.startswith("tenant_") and tenant_schema_name != "tenant_individual":
        org_key = tenant_schema_name.split("tenant_")[1]
        result = await db.execute(select(Organization).where(Organization.org_key == org_key))
        org = result.scalar_one_or_none()
        org_modules = org.enabled_modules if (org and org.enabled_modules is not None) else DEFAULT_MODULES
    else:
        org_modules = DEFAULT_MODULES

    if getattr(user, "enabled_modules", None) is not None:
        return [m for m in user.enabled_modules if m in org_modules]

    return org_modules


async def get_org_enabled_modules(db: AsyncSession, tenant_schema_name: str | None) -> list[str]:
    """Retrieve the enabled modules list for the organization/tenant."""
    from app.models.organization import Organization
    from sqlalchemy import select

    DEFAULT_MODULES = [
        "dashboard", "servers", "tickets", "settings",
        "nginx", "pm2", "systemd", "automation",
        "firewall", "processes", "ssl", "ssh", "files", "luxegenie", "sql"
    ]

    if tenant_schema_name and tenant_schema_name.startswith("tenant_") and tenant_schema_name != "tenant_individual":
        org_key = tenant_schema_name.split("tenant_")[1]
        result = await db.execute(select(Organization).where(Organization.org_key == org_key))
        org = result.scalar_one_or_none()
        if org and org.enabled_modules is not None:
            return org.enabled_modules
    return DEFAULT_MODULES


