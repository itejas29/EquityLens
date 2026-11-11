"""AI trading decision log and on/off setting

Revision ID: c4d2a8f1e6b7
Revises: b7e3a91c5d24
"""

from alembic import op
import sqlalchemy as sa

revision = "c4d2a8f1e6b7"
down_revision = "b7e3a91c5d24"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_trading_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("run_date", sa.Date(), nullable=False),
        sa.Column("action", sa.String(10), nullable=False),
        sa.Column("symbol", sa.String(20), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=True),
        sa.Column("price", sa.Numeric(12, 2), nullable=True),
        sa.Column("reason", sa.String(300), nullable=False),
    )
    op.create_index("ix_ai_trading_log_run_date", "ai_trading_log", ["run_date"])
    op.create_table(
        "ai_trading_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_by", sa.String(255), nullable=True),
    )
    # New installs start OFF: the switch is opt-in. A database where the cycle
    # has already been running (it predates the switch) starts ON, so adding
    # the switch does not silently stop an account that is mid-position.
    op.execute(
        "INSERT INTO ai_trading_settings (id, enabled) "
        "SELECT 1, EXISTS (SELECT 1 FROM ai_trading_runs)"
    )


def downgrade() -> None:
    op.drop_table("ai_trading_settings")
    op.drop_index("ix_ai_trading_log_run_date", table_name="ai_trading_log")
    op.drop_table("ai_trading_log")
