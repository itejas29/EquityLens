"""The text of every Telegram message the app sends. Pure functions: values
in, HTML string out; sending is services/notifications.py.

Every interpolated value goes through escape() — messages are sent with
parse_mode="HTML" and five active NSE symbols contain "&" (see notifications).
A figure that is not available is left out of the message, never filled in.
"""

from datetime import date as date_type
from decimal import Decimal

from app.services.notifications import escape

REGIME_LABEL = {"bull": "NORMAL", "bear": "CAUTION"}
DISCLAIMER = "⚠️ Research tool, not investment advice."
# Phase 26 (docs/experiments/phase26_crash_brake/FINDINGS.md): the daily
# 200-day rule on NIFTY 50, 2008-07 → 2026-10, 0.10% per trade, cash at 0%.
REGIME_EVIDENCE = (
    "Measured on NIFTY, 2008–2026 (an 18-year backtest of this fixed rule): leaving below the "
    "200-day average cut the worst fall to −26% from −45%, and cost about 4 points a year, "
    "mostly from sitting out recoveries."
)


def _money(value) -> str:
    return f"₹{Decimal(str(value)):,.2f}"


def _signed_pct(value) -> str:
    return f"{Decimal(str(value)):+.2f}%"


def regime_line(regime: dict) -> str | None:
    """"🛡️ Regime: CAUTION (NIFTY 4.66% below its 200-day average)", or None
    when the regime cannot be computed."""
    close, ma = regime.get("nifty_close"), regime.get("nifty_200dma")
    label = REGIME_LABEL.get(regime.get("regime"))
    if label is None or close is None or not ma:
        return None
    distance = (close - ma) / ma * 100
    return (f"🛡️ Regime: {label} (NIFTY {abs(distance):.2f}% "
            f"{'above' if distance >= 0 else 'below'} its 200-day average)")


def shortlist_message(as_of: date_type, regime: dict, picks: list[dict]) -> str:
    """picks: dicts with symbol, score, entry_low, entry_high, stop_loss,
    target_price, risk_reward — in rank order."""
    lines = [f"📊 <b>EquityLens Daily Shortlist — {as_of.isoformat()}</b>", ""]
    reg = regime_line(regime)
    if reg:
        lines += [reg, ""]
    if not picks:
        lines.append("No stock passed today's gates.")
    else:
        lines.append("Top picks:")
        for i, p in enumerate(picks, 1):
            lines.append(f"{i}. <b>{escape(p['symbol'])}</b> — Score {Decimal(str(p['score'])):.0f} — "
                         f"Buy {_money(p['entry_low'])}–{_money(p['entry_high'])}")
            lines.append(f"   Stop {_money(p['stop_loss'])} | Target {_money(p['target_price'])} | "
                         f"R:R {Decimal(str(p['risk_reward'])):.2f}")
    lines += ["", DISCLAIMER]
    return "\n".join(lines)


def cycle_message(result, equity: Decimal | None) -> str:
    """One message per AI paper-account cycle: its buys, sells and warnings."""
    kind = "monthly rebalance" if result.rebalanced else "daily stop/target check"
    lines = [f"<b>AI Trading — {result.as_of.isoformat()}</b> "
             f"({kind}, regime: {escape(REGIME_LABEL.get(result.regime, result.regime))}) — paper account"]
    for b in result.bought:
        lines.append(f"🟢 AUTO BUY: {escape(b['symbol'])} × {b['quantity']} @ {_money(b['price'])}")
        if b.get("stop") is not None and b.get("target") is not None:
            lines.append(f"   Stop {_money(b['stop'])} | Target {_money(b['target'])}")
    for s in result.sold:
        head = f"🔴 AUTO SELL: {escape(s['symbol'])}"
        if s.get("quantity") is not None and s.get("price") is not None:
            head += f" × {s['quantity']} @ {_money(s['price'])}"
        lines.append(head)
        lines.append(f"   Reason: {escape(s['reason'])}")
        pnl = s["pnl"]
        pnl_text = f"{'+' if pnl >= 0 else '−'}{_money(abs(pnl))}"
        if s.get("pnl_pct") is not None:
            pnl_text += f" ({_signed_pct(s['pnl_pct'])})"
        # "&amp;", not "&": a raw ampersand makes Telegram reject the message.
        lines.append(f"   P&amp;L: {pnl_text}")
        if s.get("days") is not None:
            lines.append(f"   Held: {s['days']} days")
    if equity is not None:
        lines.append(f"Account equity: {_money(equity)}")

    # A degraded risk check is the one thing here worth interrupting someone
    # for, so it goes in the message rather than only the log. "Nothing sold"
    # and "nothing could be checked" look identical otherwise.
    if result.unpriced:
        names = ", ".join(escape(s) for s in result.unpriced)
        lines.append(f"⚠️ NO PRICE for {names} — stop/target not evaluated today")
    if result.stale_marked:
        names = ", ".join(escape(s) for s in result.stale_marked)
        lines.append(f"⚠️ {names} checked against the previous close, not a live quote")
    no_quote = [s["symbol"] for s in result.entries_skipped if s["reason"] == "no live quote"]
    if no_quote:
        lines.append(f"⚠️ Skipped (no live price): {', '.join(escape(s) for s in no_quote)}")
    for s in result.entries_skipped:
        if s["reason"] != "no live quote":
            lines.append(f"⚠️ Did not enter {escape(s['symbol'])}: {escape(s['reason'])}")
    return "\n".join(lines)


def regime_change_message(previous: str, regime: dict) -> str:
    now = regime["regime"]
    return "\n".join([
        f"🚨 <b>REGIME CHANGE: {REGIME_LABEL[previous]} → {REGIME_LABEL[now]}</b>",
        # Index points, not rupees.
        f"NIFTY closed at {regime['nifty_close']:,.2f}; its 200-day average is {regime['nifty_200dma']:,.2f}.",
        REGIME_EVIDENCE,
    ])


def weekly_summary_message(s: dict) -> str:
    """s: start, end, equity, and optionally week_pct, nifty_week_pct, open,
    max_positions, buys, sells, best, worst (each (symbol, pct)), total_pct,
    nifty_total_pct. A missing key leaves its line out."""
    lines = [f"📈 <b>EquityLens Weekly Summary — {s['start'].isoformat()} to {s['end'].isoformat()}</b>", ""]
    paper = f"Paper portfolio: {_money(s['equity'])}"
    if s.get("week_pct") is not None:
        paper += f" ({_signed_pct(s['week_pct'])} this week)"
    lines.append(paper)
    if s.get("nifty_week_pct") is not None:
        lines.append(f"NIFTY 50: {_signed_pct(s['nifty_week_pct'])} this week")
    lines.append(f"Open positions: {s['open']} / {s['max_positions']}")
    lines.append(f"Trades this week: {s['buys']} buys, {s['sells']} sells")
    if s.get("best"):
        lines.append(f"Best: {escape(s['best'][0])} {_signed_pct(s['best'][1])}")
    if s.get("worst"):
        lines.append(f"Worst: {escape(s['worst'][0])} {_signed_pct(s['worst'][1])}")
    if s.get("total_pct") is not None and s.get("nifty_total_pct") is not None:
        lines += ["", f"Since inception: portfolio {_signed_pct(s['total_pct'])} vs NIFTY "
                      f"{_signed_pct(s['nifty_total_pct'])}"]
    lines += ["", "Paper account — no real money."]
    return "\n".join(lines)
