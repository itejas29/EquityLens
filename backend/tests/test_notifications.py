"""A notification that fails is one nobody knows they did not get.

Both defects here are of that shape: the message is silently dropped, and the
only trace is a log line — which, in one case, also contained the bot token.
"""

import logging
from datetime import date
from decimal import Decimal

import pytest
import requests

from app.core.scheduler import _ai_trading_notification_text
from app.services import notifications
from app.services.ai_trading import AITradingCycleResult

# Every active NSE symbol containing an HTML-special character, from the live
# universe on 2026-09-10. M&M is Mahindra & Mahindra — a large-cap, and an
# entirely plausible momentum pick.
AMPERSAND_SYMBOLS = ["ARE&M", "GVT&D", "J&KBANK", "M&M", "M&MFIN"]


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(notifications.settings, "telegram_bot_token", "12345:SECRET-TOKEN-VALUE")
    monkeypatch.setattr(notifications.settings, "telegram_chat_id", "999")


# --------------------------------------------------------------- escaping --

@pytest.mark.parametrize("symbol", AMPERSAND_SYMBOLS)
def test_an_ampersand_symbol_is_escaped_in_the_message(symbol):
    """Sent with parse_mode="HTML", a raw & makes Telegram reject the whole
    message with "can't parse entities" — so the first time the AI account
    traded M&M, the alert about it would have vanished."""
    result = AITradingCycleResult(
        as_of=date(2026, 9, 10), regime="bull", rebalanced=True,
        bought=[{"symbol": symbol, "quantity": 10, "price": Decimal("100.00")}],
    )
    text = _ai_trading_notification_text(result, Decimal("1000000.00"))

    assert symbol.replace("&", "&amp;") in text
    # The only bare & left must be the ones inside escape sequences.
    assert text.count("&") == text.count("&amp;")


def test_escaped_symbols_appear_in_sells_and_warnings_too():
    result = AITradingCycleResult(
        as_of=date(2026, 9, 10), regime="bull",
        sold=[{"symbol": "M&M", "reason": "stop", "pnl": Decimal("-500.00")}],
        unpriced=["J&KBANK"], stale_marked=["ARE&M"],
    )
    text = _ai_trading_notification_text(result, None)

    for raw in ("M&M", "J&KBANK", "ARE&M"):
        assert raw.replace("&", "&amp;") in text
    assert text.count("&") == text.count("&amp;")


def test_the_bold_header_is_still_real_markup():
    """Escaping the values must not escape the markup around them."""
    result = AITradingCycleResult(as_of=date(2026, 9, 10), regime="bull")
    assert "<b>AI Trading" in _ai_trading_notification_text(result, None)


def test_escape_leaves_quotes_alone():
    """Telegram's HTML mode only parses & < >. Escaping quotes would render
    &quot; literally in the message body."""
    assert notifications.escape('a "b" & <c>') == 'a "b" &amp; &lt;c&gt;'


# ------------------------------------------------------------ token safety --

def test_a_failed_send_does_not_log_the_bot_token(configured, monkeypatch, caplog):
    """requests puts the request URL in its exception message, and the token
    is IN the URL — Telegram's API is /bot<token>/sendMessage. The old code
    logged this with exc_info=True, i.e. wrote the token to the application log
    on exactly the code path that runs when something is wrong.
    """
    url = f"{notifications.TELEGRAM_API_BASE}/bot12345:SECRET-TOKEN-VALUE/sendMessage"

    def boom(*args, **kwargs):
        raise requests.exceptions.HTTPError(f"401 Client Error: Unauthorized for url: {url}")

    monkeypatch.setattr(notifications.requests, "post", boom)
    with caplog.at_level(logging.WARNING):
        notifications.send_telegram_message("hello")

    logged = caplog.text
    assert "send_failed" in logged
    assert "SECRET-TOKEN-VALUE" not in logged, "the bot token was written to the log"
    assert "<redacted>" in logged


def test_a_delivery_failure_never_raises(configured, monkeypatch):
    """The trades are already committed. A notification must not undo them or
    take down the caller."""
    def boom(*args, **kwargs):
        raise requests.exceptions.ConnectionError("network gone")

    monkeypatch.setattr(notifications.requests, "post", boom)
    notifications.send_telegram_message("hello")  # must not raise


# ----------------------------------------------------------------- limits --

def test_an_over_long_message_is_truncated_not_rejected(configured, monkeypatch):
    """Telegram rejects a body over 4096 characters outright, which drops the
    whole alert. A busy rebalance can produce one."""
    sent = {}
    monkeypatch.setattr(notifications.requests, "post",
                        lambda *a, **k: sent.update(k["json"]) or _ok())

    notifications.send_telegram_message("x" * 9000)

    assert len(sent["text"]) == notifications.MAX_MESSAGE_CHARS
    assert sent["text"].endswith(notifications.TRUNCATION_MARKER)


def test_a_normal_message_is_sent_unchanged(configured, monkeypatch):
    sent = {}
    monkeypatch.setattr(notifications.requests, "post",
                        lambda *a, **k: sent.update(k["json"]) or _ok())

    notifications.send_telegram_message("short and fine")
    assert sent["text"] == "short and fine"


def test_it_is_opt_in_and_silent_when_unconfigured(monkeypatch):
    """No credentials means no call at all — a deployment that has not set
    these must never have its trading loop affected."""
    monkeypatch.setattr(notifications.settings, "telegram_bot_token", None)
    monkeypatch.setattr(notifications.settings, "telegram_chat_id", None)

    def fail(*args, **kwargs):
        raise AssertionError("attempted a request with no credentials configured")

    monkeypatch.setattr(notifications.requests, "post", fail)
    notifications.send_telegram_message("hello")


class _Resp:
    def raise_for_status(self):
        return None


def _ok():
    return _Resp()
