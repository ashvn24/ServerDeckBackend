"""Upgrade the public schema and every tenant schema to the current Alembic head.

Run after deploying new migrations. Tenant schemas are discovered from the
database itself (everything named tenant_*), so it covers org schemas,
tenant_individual, and anything created outside the organizations table.
A failing schema is reported and skipped; the rest still migrate.
"""
import argparse
import asyncio
import sys

from sqlalchemy import text

from alembic import command

from gen_migration import make_config, set_scope_env
from app.database import engine


async def list_tenant_schemas() -> list[str]:
    async with engine.connect() as conn:
        schemas = (
            await conn.execute(
                text(
                    "SELECT schema_name FROM information_schema.schemata "
                    "WHERE schema_name LIKE 'tenant\\_%' ORDER BY 1"
                )
            )
        ).scalars().all()
    await engine.dispose()
    return list(schemas)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--skip-public", action="store_true", help="Only migrate tenant schemas"
    )
    parser.add_argument(
        "--schema", action="append", default=None,
        help="Migrate only this tenant schema (repeatable); default: all tenant_* schemas",
    )
    args = parser.parse_args()

    schemas = args.schema if args.schema else asyncio.run(list_tenant_schemas())
    cfg = make_config()
    failures = []

    if not args.skip_public:
        print("Upgrading public schema...")
        set_scope_env(tenant=False, tenant_schema="")
        try:
            command.upgrade(cfg, "head")
        except Exception as exc:
            failures.append(("public", exc))
            print(f"  FAILED: {exc}")

    for schema in schemas:
        print(f"Upgrading {schema}...")
        set_scope_env(tenant=True, tenant_schema=schema)
        try:
            command.upgrade(cfg, "head")
        except Exception as exc:
            failures.append((schema, exc))
            print(f"  FAILED: {exc}")

    total = len(schemas) + (0 if args.skip_public else 1)
    print(f"\n{total - len(failures)}/{total} schemas migrated to head.")
    if failures:
        for schema, exc in failures:
            print(f"  FAILED {schema}: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
