"""Add platform_users and org_name to public schema

Revision ID: 515296b676e8
Revises: 38e248fd43cb
Create Date: 2026-06-05 01:52:14.967478

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
import os


# revision identifiers, used by Alembic.
revision: str = '515296b676e8'
down_revision: Union[str, None] = '38e248fd43cb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if os.getenv("IS_TENANT_MIGRATION") == "true":
        return

    op.create_table('platform_users',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    schema='public'
    )
    op.create_index(op.f('ix_public_platform_users_email'), 'platform_users', ['email'], unique=True, schema='public')

    # Add name column with a server_default for existing rows, then remove the default
    op.add_column('organizations', sa.Column('name', sa.String(length=255), nullable=False, server_default='Unnamed'), schema='public')
    op.alter_column('organizations', 'name', server_default=None, schema='public')


def downgrade() -> None:
    if os.getenv("IS_TENANT_MIGRATION") == "true":
        return

    op.drop_column('organizations', 'name', schema='public')
    op.drop_index(op.f('ix_public_platform_users_email'), table_name='platform_users', schema='public')
    op.drop_table('platform_users', schema='public')
