from decimal import Decimal

# Fixed identity for the system account that owns the AI-managed paper
# portfolio. Not a real login — password is a random, never-surfaced hash
# created on first use (see services/ai_trading.get_ai_trader_account).
#
# Fresh start, 2026-11-13: the user restarted the AI account with ₹15 lakh.
# It is a NEW system user rather than a reset of the old account, because
# PaperAccount.user_id is unique (one account per user) and resetting in place
# would mix two capital bases in one equity history. The first account, its
# trades and its equity snapshots stay in the database untouched.
AI_TRADER_EMAIL = "ai-trader-2@equitylens.internal"
AI_TRADER_NAME = "EquityLens AI Trader"
AI_TRADER_CAPITAL = Decimal("1500000.00")

# Earlier AI accounts. Whatever they still hold is closed by the next cycle, on
# a live quote only (see ai_trading._retire_previous_accounts) — left open,
# nothing would ever check their stops again.
RETIRED_AI_TRADER_EMAILS = ("ai-trader@equitylens.internal",)

# Runs shortly after the 09:15 IST daily-signals publish (GENERATION_HOUR/MINUTE
# in daily_signals_config.py), so a cycle always sees that day's shortlist.
RUN_HOUR = 9
RUN_MINUTE = 20
