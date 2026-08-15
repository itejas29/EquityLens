"""add daily_signals table

The dated, frozen pre-market shortlist. Unique on (date, stock_id) so a
re-run for a date replaces rather than duplicates that day's call.

Revision ID: d3b8e51c7a92
Revises: a1f7c2e94b30
Create Date: 2026-08-14 17:58:41.336209

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd3b8e51c7a92'
down_revision: Union[str, None] = 'a1f7c2e94b30'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'daily_signals',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('stock_id', sa.Integer(), nullable=False),
        sa.Column('rank', sa.Integer(), nullable=False),
        sa.Column('technical_score', sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column('fundamental_score', sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column('valuation_score', sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column('risk_score', sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column('overall_score', sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column('signal', sa.String(length=30), nullable=False),
        sa.Column('reference_close', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('reference_date', sa.Date(), nullable=False),
        sa.Column('entry_low', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('entry_high', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('stop_loss', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('target_price', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('risk_reward', sa.Numeric(precision=10, scale=4), nullable=False),
        sa.Column('stop_method', sa.String(length=10), nullable=False),
        sa.Column('rationale', sa.JSON(), nullable=False),
        sa.Column('generated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['stock_id'], ['stocks.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('date', 'stock_id', name='uq_daily_signals_date_stock'),
    )
    op.create_index(op.f('ix_daily_signals_date'), 'daily_signals', ['date'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_daily_signals_date'), table_name='daily_signals')
    op.drop_table('daily_signals')
