"""add dismissed_at to notices

Revision ID: d8a41f6c2e73
Revises: c5e17a3b9d20
Create Date: 2026-09-30 19:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd8a41f6c2e73'
down_revision: Union[str, Sequence[str], None] = 'c5e17a3b9d20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('notices') as batch_op:
        batch_op.add_column(sa.Column('dismissed_at', sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('notices') as batch_op:
        batch_op.drop_column('dismissed_at')
