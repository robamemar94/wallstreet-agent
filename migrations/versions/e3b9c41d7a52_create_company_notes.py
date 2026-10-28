"""create company_notes (diario de cada empresa)

Revision ID: e3b9c41d7a52
Revises: d8a41f6c2e73
Create Date: 2026-10-18 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e3b9c41d7a52'
down_revision: Union[str, Sequence[str], None] = 'd8a41f6c2e73'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'company_notes',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('ticker', sa.String(), nullable=False),
        sa.Column('date', sa.String(), nullable=False),
        sa.Column('text', sa.String(), nullable=False),
        sa.Column('created_at', sa.String(), nullable=False),
        sa.Column('updated_at', sa.String(), nullable=True),
    )
    op.create_index('ix_company_notes_id', 'company_notes', ['id'])
    op.create_index('ix_company_notes_ticker', 'company_notes', ['ticker'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_company_notes_ticker', table_name='company_notes')
    op.drop_index('ix_company_notes_id', table_name='company_notes')
    op.drop_table('company_notes')
