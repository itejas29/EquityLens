"""add nse_symbols catalogue

The full NSE equity list, kept separate from `stocks` so search can cover the
whole exchange without ingesting a price history for every ticker.

Revision ID: f6a24d0b8e13
Revises: d3b8e51c7a92
Create Date: 2026-08-14 18:41:55.902213

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f6a24d0b8e13'
down_revision: Union[str, None] = 'd3b8e51c7a92'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'nse_symbols',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('symbol', sa.String(length=30), nullable=False),
        sa.Column('company_name', sa.String(length=200), nullable=False),
        sa.Column('series', sa.String(length=10), nullable=False),
        sa.Column('isin', sa.String(length=20), nullable=True),
        sa.Column('listed_on', sa.Date(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('symbol'),
    )
    op.create_index(op.f('ix_nse_symbols_symbol'), 'nse_symbols', ['symbol'], unique=False)
    # Name search is a leading-wildcard ILIKE, so this index helps ordering and
    # exact-name lookups rather than the substring scan itself.
    op.create_index(op.f('ix_nse_symbols_company_name'), 'nse_symbols', ['company_name'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_nse_symbols_company_name'), table_name='nse_symbols')
    op.drop_index(op.f('ix_nse_symbols_symbol'), table_name='nse_symbols')
    op.drop_table('nse_symbols')
