"""Admin alerts via Telegram (best-effort, works from any module).

Usage:
    from stock_signal_analyzer.admin_alerts import notify_admin
    notify_admin("Something broke", alert_type="llm_learning")
"""

from __future__ import annotations

import logging
import os
import time

_log = logging.getLogger(__name__)

_ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "").strip()
_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()

_last_alert_ts: dict[str, float] = {}
_MIN_REPEAT_SEC = 300  # 5 minutes between identical alert types


def notify_admin(message: str, alert_type: str = "general") -> None:
    """Send a Telegram message to the admin. Best-effort: never raises.

    Args:
        message: Text to send (plain text, no HTML).
        alert_type: Key for deduplication/rate-limiting.
    """
    now = time.time()
    last = _last_alert_ts.get(alert_type, 0.0)
    if now - last < _MIN_REPEAT_SEC:
        return
    _last_alert_ts[alert_type] = now

    if not _ADMIN_CHAT_ID or not _BOT_TOKEN:
        _log.info("Admin alert (no Telegram config): %s", message)
        return

    try:
        import requests

        url = f"https://api.telegram.org/bot{_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": int(_ADMIN_CHAT_ID),
            "text": f"⚠️ SSA Alert\n{message}",
        }
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code >= 400:
            _log.warning("Admin alert HTTP %s: %s", resp.status_code, resp.text)
    except Exception as exc:
        _log.warning("Failed to send admin alert: %s", exc)
