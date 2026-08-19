"""unique paper equity snapshot per account per day

Every other table in this schema guards its natural key this way
(uq_price_history_stock_date, uq_indicators_stock_date,
uq_fundamentals_stock_as_of_date, uq_watchlist_user_stock); this one was
missed. Without it any re-run of the 20:00 incremental for a date inserts a
second snapshot for that same day, so the equity curve grows a duplicate
point and the series stops being one-row-per-session.

Confirmed live on 2026-08-19: a manual run and the scheduler's own run both
recorded a snapshot for that date, leaving two rows for one day.

Duplicates are collapsed to the most recently created row before the
constraint is added — that is the one written by the run that finished last,
so it reflects the completest price data for the day.

Revision ID: e91c4a7d2f18
Revises: 0ae582190d5d
"""

from alembic import op

revision = "e91c4a7d2f18"
down_revision = "0ae582190d5d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM paper_equity_snapshots a
        USING paper_equity_snapshots b
        WHERE a.account_id = b.account_id
          AND a.date = b.date
          AND (a.created_at, a.id) < (b.created_at, b.id)
        """
    )
    op.create_unique_constraint(
        "uq_paper_equity_snapshot_account_date",
        "paper_equity_snapshots",
        ["account_id", "date"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_paper_equity_snapshot_account_date",
        "paper_equity_snapshots",
        type_="unique",
    )
