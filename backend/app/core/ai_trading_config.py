# Fixed identity for the system account that owns the AI-managed paper
# portfolio. Not a real login — password is a random, never-surfaced hash
# created on first use (see services/ai_trading.get_ai_trader_account).
AI_TRADER_EMAIL = "ai-trader@equitylens.internal"
AI_TRADER_NAME = "EquityLens AI Trader"

# Runs shortly after the 09:15 IST daily-signals publish (GENERATION_HOUR/MINUTE
# in daily_signals_config.py), so a cycle always sees that day's shortlist.
RUN_HOUR = 9
RUN_MINUTE = 20
