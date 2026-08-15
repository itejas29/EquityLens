"""add cost_basis to paper_trades

Stores the exact cash debited to open a position, so realized/unrealized P&L
reconcile with the cash ledger instead of being derived from the per-share
`price` column (rounded to the paisa, and therefore drifting from what was
actually paid by up to half a paisa per share per leg).

Revision ID: a1f7c2e94b30
Revises: b4639adcd3f5
Create Date: 2026-08-14 17:31:08.204517

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1f7c2e94b30'
down_revision: Union[str, None] = 'b4639adcd3f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('paper_trades', sa.Column('cost_basis', sa.Numeric(precision=12, scale=2), nullable=True))
    # Backfill for rows written before this fix. `price * quantity` is the cash
    # outflow those rows implied under the old model — a faithful reconstruction
    # of the pre-fix behaviour, accurate to within the rounding error being
    # fixed here. New rows never take this path; buy() writes cost_basis
    # directly from the amount it debits.
    op.execute('UPDATE paper_trades SET cost_basis = ROUND(price * quantity, 2) WHERE cost_basis IS NULL')
    op.alter_column('paper_trades', 'cost_basis', nullable=False)


def downgrade() -> None:
    op.drop_column('paper_trades', 'cost_basis')
