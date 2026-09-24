"""add alert_id to tickets

Revision ID: e1f2a3b4c5d6
Revises: d3e4f5a6b7c8
Create Date: 2026-08-21 22:45:00.000000

"""
import os
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, None] = 'd3e4f5a6b7c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Tenant-schema revision: no-op during the public-schema upgrade.
    if os.getenv("IS_TENANT_MIGRATION") != "true":
        return

    op.add_column('tickets', sa.Column('alert_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        'fk_tickets_alert_id_alert_records',
        'tickets',
        'alert_records',
        ['alert_id'],
        ['id'],
        ondelete='SET NULL'
    )


def downgrade() -> None:
    # Tenant-schema revision: no-op during the public-schema upgrade.
    if os.getenv("IS_TENANT_MIGRATION") != "true":
        return

    op.drop_constraint('fk_tickets_alert_id_alert_records', 'tickets', type_='foreignkey')
    op.drop_column('tickets', 'alert_id')
