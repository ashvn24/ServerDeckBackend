"""add enabled_modules

Revision ID: f6f993ae_add_enabled_modules
Revises: e4f8d9b1c2a3
Create Date: 2026-06-11 23:55:00.000000

"""
import os
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f6f993ae_add_enabled_modules'
down_revision: Union[str, None] = 'e4f8d9b1c2a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if os.getenv("IS_TENANT_MIGRATION") == "true":
        # Tenant-schema revision
        op.add_column('users', sa.Column('enabled_modules', sa.JSON(), nullable=True))
    else:
        # Public-schema revision
        op.add_column('organizations', sa.Column('enabled_modules', sa.JSON(), nullable=True))


def downgrade() -> None:
    if os.getenv("IS_TENANT_MIGRATION") == "true":
        # Tenant-schema revision
        op.drop_column('users', 'enabled_modules')
    else:
        # Public-schema revision
        op.drop_column('organizations', 'enabled_modules')
