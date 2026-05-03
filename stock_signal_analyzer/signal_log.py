"""Дозапись сигналов в JSONL для последующей проверки исходов (walk-forward, бэктест)."""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .engine import SignalReport

_lock = threading.Lock()
_log = logging.getLogger(__name__)


def append_signal_record(path: str | None, record: dict[str, Any]) -> None:
    if not path:
        return
    line = json.dumps(record, ensure_ascii=False) + "\n"
    try:
        with _lock:
            parent = os.path.dirname(os.path.abspath(path))
            if parent:
                os.makedirs(parent, exist_ok=True)
            # Открываем с ограниченными правами (owner-only read/write)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(fd, line.encode("utf-8"))
            finally:
                os.close(fd)
    except OSError as e:
        _log.warning("SSA_SIGNAL_LOG: не удалось записать в %s: %s", path, e)


def log_path_from_env() -> str | None:
    return os.environ.get("SSA_SIGNAL_LOG") or os.environ.get("SIGNAL_LOG_JSONL")


def build_record_from_report(
    report: "SignalReport",
    ref_price: float,
    currency: str,
) -> dict[str, Any]:
    """Снимок для офлайн-разметки: через N дней сравнить с ценой."""
    return {
        "ts_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "symbol": report.symbol,
        "ref_price": float(ref_price),
        "currency": currency,
        "score": report.score,
        "score_before_macro": report.score_before_macro,
        "confidence": report.confidence,
        "signal_tier": report.signal_tier,
        "tier_rationale": report.tier_rationale,
        "direction": "long" if report.score > 0.05 else ("short" if report.score < -0.05 else "neutral"),
        "technical_score": report.technical_score,
        "momentum_score": report.momentum_score,
        "news_score": report.news_score,
        "volume_score": report.volume_score,
        "intraday_score": report.intraday_score,
        "adx14": report.adx14,
        "regime": report.regime_label,
        "atr_pct": report.atr_pct,
        "macro_dampening": report.macro_dampening,
        "timing_detail": report.timing_detail,
        "weekly_regime": report.weekly_regime,
        "stop_hint_pct": report.stop_hint_pct,
        "verdict": report.verdict,
    }
