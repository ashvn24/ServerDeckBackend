import contextvars
import re
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from app.config import get_settings

settings = get_settings()

tenant_schema: contextvars.ContextVar[str] = contextvars.ContextVar("tenant_schema")

# A valid, unquoted PostgreSQL schema identifier as used by this app:
# lowercase letters/digits/underscore, starting with a letter or underscore.
# All tenant schemas are of the form "tenant_<key>", plus "public".
_SCHEMA_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


def validate_schema_name(schema: str) -> str:
    """Validate a schema identifier before it is interpolated into a raw SQL
    statement (SET search_path / CREATE SCHEMA / DROP SCHEMA).

    Schema names cannot be passed as bound parameters, so they MUST be
    validated to prevent SQL/search_path injection. Raises ValueError on
    anything that is not a plain identifier.
    """
    if not isinstance(schema, str) or len(schema) > 63 or not _SCHEMA_NAME_RE.match(schema):
        raise ValueError("Invalid schema name")
    return schema


async def set_search_path(session: AsyncSession, schema: str) -> None:
    """Safely switch the active tenant schema for a session.

    The schema name is validated (never bound) to prevent injection.
    """
    schema = validate_schema_name(schema)
    await session.execute(text(f"SET search_path TO {schema}, public"))

# prepared_statement_cache_size=0: pooled connections switch search_path
# between tenant schemas, which invalidates asyncpg's cached statement plans
# for schema-less tables (InvalidCachedStatementError).
engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
    connect_args={"prepared_statement_cache_size": 0},
)
async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


import contextlib

@contextlib.asynccontextmanager
async def tenant_session():
    async with async_session_factory() as session:
        schema = tenant_schema.get(None)
        if schema:
            await set_search_path(session, schema)
        yield session


async def get_db() -> AsyncSession:
    async with tenant_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
