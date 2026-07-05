"""extend waitlist request fields

Revision ID: d1e2f3a4b5c6
Revises: f6f993ae_add_enabled_modules
Create Date: 2026-07-05 18:25:00.000000

"""
import os
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd1e2f3a4b5c6'
down_revision: Union[str, None] = 'f6f993ae_add_enabled_modules'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Public-schema revision: no-op when a tenant schema replays the chain.
    if os.getenv("IS_TENANT_MIGRATION") == "true":
        return

    op.add_column('waitlist_requests', sa.Column('name', sa.String(length=255), nullable=True))
    op.add_column('waitlist_requests', sa.Column('request_type', sa.String(length=50), nullable=True))
    op.add_column('waitlist_requests', sa.Column('org_name', sa.String(length=255), nullable=True))
    op.add_column('waitlist_requests', sa.Column('password_hash', sa.String(length=255), nullable=True))


def downgrade() -> None:
    # Public-schema revision: no-op when a tenant schema replays the chain.
    if os.getenv("IS_TENANT_MIGRATION") == "true":
        return

    op.drop_column('waitlist_requests', 'password_hash')
    op.drop_column('waitlist_requests', 'org_name')
    op.drop_column('waitlist_requests', 'request_type')
    op.drop_column('waitlist_requests', 'name')
