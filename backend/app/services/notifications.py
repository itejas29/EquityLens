"""Push notifications for events a user would otherwise only see by opening
the app — currently just the AI trading loop's daily activity. Best-effort:
a delivery failure here must never affect anything that already committed
(the trades themselves), so every call is wrapped and only logs a warning.
"""

import logging

import requests

from app.core.config import settings

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"
REQUEST_TIMEOUT_SECONDS = 10


def send_telegram_message(text: str) -> None:
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return

    try:
        response = requests.post(
            f"{TELEGRAM_API_BASE}/bot{settings.telegram_bot_token}/sendMessage",
            json={"chat_id": settings.telegram_chat_id, "text": text, "parse_mode": "HTML"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except Exception:
        logger.warning("notifications.telegram.send_failed", exc_info=True)
