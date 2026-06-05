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
    "yahoo", "hotmail", "outlook", "icloud", "aol", 
    "zoho", "protonmail", "proton", "mail", "live", "msn"
}

def get_org_key_from_email(email: str) -> str | None:
    if not email or "@" not in email:
        return None
    domain = email.split("@")[1].strip().lower()
    parts = domain.split(".")
    if len(parts) >= 2:
        key = parts[0]
        # Ignore common/personal email subdomains or top domains if they equal common domains
        if key in COMMON_DOMAINS:
            return None
        return key
    return None

async def create_tenant_schema(schema_name: str, db: AsyncSession):
    """Run CREATE SCHEMA query in PostgreSQL."""
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
    
    logger.info(f"Running database migrations for schema: {schema_name}")
    
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env=env,
        cwd=str(backend_dir),
        capture_output=True,
        text=True
    )
    
    if result.returncode != 0:
        logger.error(f"Alembic migration failed for {schema_name}: {result.stderr}")
        raise Exception(f"Alembic migration failed: {result.stderr}")
        
    logger.info(f"Successfully migrated schema: {schema_name}")

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
                from jose import jwt
                from app.config import get_settings
                settings = get_settings()
                payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
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
                    if org_key:
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
        from app.database import tenant_schema
        tenant_schema.set(schema_name)
        
    return schema_name
