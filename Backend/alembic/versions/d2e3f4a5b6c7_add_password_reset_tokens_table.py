"""add password reset tokens table

Revision ID: d2e3f4a5b6c7
Revises: d1e2f3a4b5c6
Create Date: 2026-07-05 18:42:00.000000

"""
import os
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'd2e3f4a5b6c7'
down_revision: Union[str, None] = 'd1e2f3a4b5c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Public-schema revision: no-op when a tenant schema replays the chain.
    if os.getenv("IS_TENANT_MIGRATION") == "true":
        return

    op.create_table('password_reset_tokens',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('schema_name', sa.String(length=255), nullable=False),
        sa.Column('token', sa.String(length=255), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        schema='public'
    )
    op.create_index(op.f('ix_public_password_reset_tokens_token'), 'password_reset_tokens', ['token'], unique=True, schema='public')


def downgrade() -> None:
    # Public-schema revision: no-op when a tenant schema replays the chain.
    if os.getenv("IS_TENANT_MIGRATION") == "true":
        return

    op.drop_index(op.f('ix_public_password_reset_tokens_token'), table_name='password_reset_tokens', schema='public')
    op.drop_table('password_reset_tokens', schema='public')
