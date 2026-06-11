from logging.config import fileConfig
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config
from alembic import context
import asyncio

# Import all models so Alembic can detect them
from app.database import Base
import app.models  # noqa: F401  # registers every model on Base.metadata

import os
from app.config import get_settings

config = context.config
settings = get_settings()
db_url = settings.database_url
if db_url:
    db_url = db_url.replace("%", "%%")
config.set_main_option("sqlalchemy.url", db_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def include_object(object, name, type_, reflected, compare_to):
    if type_ == "table":
        is_tenant = os.getenv("IS_TENANT_MIGRATION") == "true"
        table_schema = getattr(object, "schema", None)
        if is_tenant:
            return table_schema != "public"
        else:
            return table_schema == "public"
    return True


def process_revision_directives(context, revision, directives):
    # gen_migration.py sets this flag so a scope with no model changes doesn't
    # produce an empty revision file; plain `alembic revision` is unaffected.
    if config.attributes.get("skip_empty_autogenerate") and directives:
        if directives[0].upgrade_ops.is_empty():
            directives[:] = []


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
        process_revision_directives=process_revision_directives,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations():
    schema_name = os.getenv("IS_TENANT_MIGRATION_SCHEMA")
    connect_args = {}
    if schema_name:
        connect_args = {"server_settings": {"search_path": schema_name}}

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args=connect_args,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
