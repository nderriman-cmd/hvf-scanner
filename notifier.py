"""Telegram notifications (simple requests-based, no async dependency)."""

import logging
import requests

logger = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str) -> None:
        self._token = token
        self._chat_id = chat_id
        self._base = f"https://api.telegram.org/bot{token}"

    def send(self, text: str) -> bool:
        """Send a message. Returns True on success."""
        if not self._token or not self._chat_id:
            logger.warning("Telegram not configured — skipping notification.")
            return False
        try:
            resp = requests.post(
                f"{self._base}/sendMessage",
                json={"chat_id": self._chat_id, "text": text, "parse_mode": "HTML"},
                timeout=10,
            )
            resp.raise_for_status()
            return True
        except Exception as exc:
            logger.error("Telegram send failed: %s", exc)
            return False
