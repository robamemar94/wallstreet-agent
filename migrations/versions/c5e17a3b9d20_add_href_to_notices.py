"""add href to notices

Revision ID: c5e17a3b9d20
Revises: b3d92c7e4a51
Create Date: 2026-09-29 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c5e17a3b9d20'
down_revision: Union[str, Sequence[str], None] = 'b3d92c7e4a51'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('notices') as batch_op:
        batch_op.add_column(sa.Column('href', sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('notices') as batch_op:
        batch_op.drop_column('href')
