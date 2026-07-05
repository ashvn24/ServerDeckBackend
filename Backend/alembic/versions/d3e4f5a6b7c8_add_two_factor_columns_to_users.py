"""add two factor columns to users

Revision ID: d3e4f5a6b7c8
Revises: d2e3f4a5b6c7
Create Date: 2026-07-05 18:59:00.000000

"""
import os
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd3e4f5a6b7c8'
down_revision: Union[str, None] = 'd2e3f4a5b6c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('two_factor_enabled', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('users', sa.Column('two_factor_method', sa.String(length=50), nullable=True))
    op.add_column('users', sa.Column('two_factor_secret', sa.String(length=255), nullable=True))
    op.add_column('users', sa.Column('two_factor_otp_secret', sa.String(length=255), nullable=True))
    op.add_column('users', sa.Column('two_factor_otp_expires_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'two_factor_enabled')
    op.drop_column('users', 'two_factor_method')
    op.drop_column('users', 'two_factor_secret')
    op.drop_column('users', 'two_factor_otp_secret')
    op.drop_column('users', 'two_factor_otp_expires_at')
