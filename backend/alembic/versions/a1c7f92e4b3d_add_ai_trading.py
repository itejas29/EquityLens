"""add AI trading support

Adds the columns the AI trading loop (app/services/ai_trading.py) needs to
carry a position's own stop-loss/target/entry-score forward from the
DailySignal it was bought against, plus the run-log table it uses for
once-per-day idempotency. All new PaperTrade columns are nullable and stay
NULL for every existing/human-originated trade — a manual position has no
strategy stop/target to hold it to, so there is nothing to backfill.

Revision ID: a1c7f92e4b3d
Revises: e91c4a7d2f18
"""

import sqlalchemy as sa
from alembic import op

revision = "a1c7f92e4b3d"
down_revision = "e91c4a7d2f18"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("paper_trades", sa.Column("stop_loss", sa.Numeric(12, 2), nullable=True))
    op.add_column("paper_trades", sa.Column("target_price", sa.Numeric(12, 2), nullable=True))
    op.add_column("paper_trades", sa.Column("entry_score", sa.Numeric(5, 2), nullable=True))
    op.add_column("paper_trades", sa.Column("exit_reason", sa.String(10), nullable=True))

    op.create_table(
        "ai_trading_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_date", sa.Date(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("bought_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sold_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("regime", sa.String(10), nullable=True),
    )
    op.create_unique_constraint("uq_ai_trading_runs_run_date", "ai_trading_runs", ["run_date"])
    op.create_index("ix_ai_trading_runs_run_date", "ai_trading_runs", ["run_date"])


def downgrade() -> None:
    op.drop_index("ix_ai_trading_runs_run_date", table_name="ai_trading_runs")
    op.drop_constraint("uq_ai_trading_runs_run_date", "ai_trading_runs", type_="unique")
    op.drop_table("ai_trading_runs")

    op.drop_column("paper_trades", "exit_reason")
    op.drop_column("paper_trades", "entry_score")
    op.drop_column("paper_trades", "target_price")
    op.drop_column("paper_trades", "stop_loss")
