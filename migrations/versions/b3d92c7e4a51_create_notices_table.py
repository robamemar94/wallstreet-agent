"""create notices table

Revision ID: b3d92c7e4a51
Revises: a7c41e9b2f10
Create Date: 2026-09-29 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3d92c7e4a51'
down_revision: Union[str, Sequence[str], None] = 'a7c41e9b2f10'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('notices',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('key', sa.String(), nullable=False),
    sa.Column('scope', sa.String(), nullable=False),
    sa.Column('category', sa.String(), nullable=False),
    sa.Column('ticker', sa.String(), nullable=True),
    sa.Column('kind', sa.String(), nullable=True),
    sa.Column('tone', sa.String(), nullable=True),
    sa.Column('text', sa.String(), nullable=False),
    sa.Column('date', sa.String(), nullable=True),
    sa.Column('since', sa.String(), nullable=True),
    sa.Column('first_seen', sa.String(), nullable=False),
    sa.Column('last_seen', sa.String(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('seen_at', sa.String(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_notices_id'), 'notices', ['id'], unique=False)
    op.create_index(op.f('ix_notices_key'), 'notices', ['key'], unique=False)
    op.create_index(op.f('ix_notices_scope'), 'notices', ['scope'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_notices_scope'), table_name='notices')
    op.drop_index(op.f('ix_notices_key'), table_name='notices')
    op.drop_index(op.f('ix_notices_id'), table_name='notices')
    op.drop_table('notices')
