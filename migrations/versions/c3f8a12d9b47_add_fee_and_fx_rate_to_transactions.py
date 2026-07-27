"""add fee and fx_rate_at_purchase to transactions, total_fees and total_cost_eur to portfolio_items

Revision ID: c3f8a12d9b47
Revises: a9dd56fd61c5
Create Date: 2026-06-02 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c3f8a12d9b47'
down_revision: Union[str, Sequence[str], None] = 'a9dd56fd61c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('transactions', sa.Column('fee', sa.Float(), nullable=True, server_default='0.0'))
    op.add_column('transactions', sa.Column('fx_rate_at_purchase', sa.Float(), nullable=True))
    op.add_column('portfolio_items', sa.Column('total_fees', sa.Float(), nullable=True, server_default='0.0'))
    op.add_column('portfolio_items', sa.Column('total_cost_eur', sa.Float(), nullable=True, server_default='0.0'))


def downgrade() -> None:
    op.drop_column('transactions', 'fee')
    op.drop_column('transactions', 'fx_rate_at_purchase')
    op.drop_column('portfolio_items', 'total_fees')
    op.drop_column('portfolio_items', 'total_cost_eur')
