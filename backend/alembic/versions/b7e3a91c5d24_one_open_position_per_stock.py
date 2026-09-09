"""one open paper position per account per stock

Revision ID: b7e3a91c5d24
Revises: a1c7f92e4b3d

paper_trading.buy() has always refused a second open position in the same
stock, but only in Python, and only outside a lock. Two concurrent buys both
passed that check — reproduced on Postgres 16, two threads, two open rows in
one stock and a 400,240.00 debit lost from cash. The lock (SELECT ... FOR
UPDATE, see services/paper_trading.py) is the fix; this index is the backstop
that turns a future missing lock into an IntegrityError instead of silent
pyramiding.

Partial on status = 'open': closing a position and re-entering the same stock
is normal and must stay possible, so closed rows are not constrained.

IF THIS MIGRATION FAILS with a uniqueness violation, the database already
contains duplicate open positions and the upgrade is telling you so rather
than picking one to discard. Find them with:

    SELECT account_id, stock_id, count(*), array_agg(id)
    FROM paper_trades WHERE status = 'open'
    GROUP BY account_id, stock_id HAVING count(*) > 1;

and decide per pair which row is real — they represent real cash that was
debited, so merging them is a ledger decision, not a cleanup.
"""

from alembic import op
import sqlalchemy as sa

revision = "b7e3a91c5d24"
down_revision = "a1c7f92e4b3d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_paper_trade_open_position",
        "paper_trades",
        ["account_id", "stock_id"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
        sqlite_where=sa.text("status = 'open'"),
    )


def downgrade() -> None:
    op.drop_index("uq_paper_trade_open_position", table_name="paper_trades")
