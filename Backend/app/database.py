import contextvars
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from app.config import get_settings

settings = get_settings()

tenant_schema: contextvars.ContextVar[str] = contextvars.ContextVar("tenant_schema")

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
            await session.execute(text(f"SET search_path TO {schema}, public"))
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
