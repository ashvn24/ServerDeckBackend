"""Add luxegenie_health column

Revision ID: e4f8d9b1c2a3
Revises: a2b2c2d2e2f2
Create Date: 2026-06-11 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e4f8d9b1c2a3'
down_revision: Union[str, None] = 'a2b2c2d2e2f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('servers', sa.Column('luxegenie_health', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('servers', 'luxegenie_health')
