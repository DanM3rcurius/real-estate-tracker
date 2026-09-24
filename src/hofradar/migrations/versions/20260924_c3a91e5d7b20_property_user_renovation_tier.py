"""property user_renovation_tier

Revision ID: c3a91e5d7b20
Revises: 8670e2b33adc
Create Date: 2026-09-24 10:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'c3a91e5d7b20'
down_revision = '8670e2b33adc'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('properties', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('user_renovation_tier', sa.String(length=16), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table('properties', schema=None) as batch_op:
        batch_op.drop_column('user_renovation_tier')
