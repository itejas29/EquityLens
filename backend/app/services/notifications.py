"""Push notifications for events a user would otherwise only see by opening
the app — currently just the AI trading loop's daily activity. Best-effort:
a delivery failure here must never affect anything that already committed
(the trades themselves), so every call is wrapped and only logs a warning.

Two things this module has to get right, because a notification that fails is
a notification nobody knows they did not get.

THE BOT TOKEN MUST NOT REACH THE LOGS. requests puts the full request URL in
its exception messages, and the token is IN the URL — Telegram's API is
/bot<token>/sendMessage. Logging that exception with exc_info=True wrote the
bot token into the application log on every failed send, which is the one code
path guaranteed to run when something is wrong. Every logged string goes
through _redact.

MESSAGE TEXT MUST BE ESCAPED. Sending with parse_mode="HTML" means Telegram
parses the body, and an unescaped "&" makes it reject the whole message with
"can't parse entities" — the message is lost and the only trace is a warning.
Five of the 500 active NSE symbols contain "&": ARE&M, GVT&D, J&KBANK, M&M and
M&MFIN. M&M is Mahindra & Mahindra, a large-cap and an entirely plausible
momentum pick, so this was not a theoretical edge case: the first time the AI
account traded one of them, the alert about it would silently vanish.
"""

import html
import logging

import requests

from app.core.config import settings

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"
REQUEST_TIMEOUT_SECONDS = 10

# Telegram rejects a sendMessage body over 4096 UTF-16 code units. A cycle that
# traded many positions can exceed it, and the failure mode is the same as the
# escaping one — the entire message is dropped. Truncated with a marker so a
# clipped alert is obviously clipped rather than quietly short.
MAX_MESSAGE_CHARS = 4096
TRUNCATION_MARKER = "\n… (truncated)"


def escape(value: object) -> str:
    """Escape a value for interpolation into an HTML-parsed message.

    quote=False on purpose: Telegram's HTML mode only cares about & < >, and
    escaping quotes would render &quot; literally in the message body.
    """
    return html.escape(str(value), quote=False)


def _redact(text: str) -> str:
    """Remove the bot token from anything about to be logged."""
    token = settings.telegram_bot_token
    if token:
        text = text.replace(token, "<redacted>")
    return text


def _truncate(text: str) -> str:
    if len(text) <= MAX_MESSAGE_CHARS:
        return text
    return text[: MAX_MESSAGE_CHARS - len(TRUNCATION_MARKER)] + TRUNCATION_MARKER


def send_telegram_message(text: str) -> None:
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return

    try:
        response = requests.post(
            f"{TELEGRAM_API_BASE}/bot{settings.telegram_bot_token}/sendMessage",
            json={"chat_id": settings.telegram_chat_id, "text": _truncate(text), "parse_mode": "HTML"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except Exception as exc:
        # No exc_info: the traceback carries the request URL, and the token is
        # in it. The exception's own message is redacted before it is logged.
        logger.warning(
            "notifications.telegram.send_failed type=%s detail=%s",
            type(exc).__name__, _redact(str(exc)),
        )
