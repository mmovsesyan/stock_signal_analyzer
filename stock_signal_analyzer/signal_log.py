"""Дозапись сигналов в JSONL для последующей проверки исходов (walk-forward, бэктест)."""

from __future__ import annotations

import hashlib
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
                data = line.encode("utf-8")
                offset = 0
                total = len(data)
                while offset < total:
                    written = os.write(fd, data[offset:])
                    offset += written
            finally:
                os.close(fd)
    except OSError as e:
        _log.warning("SSA_SIGNAL_LOG: не удалось записать в %s: %s", path, e)


def log_path_from_env() -> str | None:
    return os.environ.get("SSA_SIGNAL_LOG") or os.environ.get("SIGNAL_LOG_JSONL")


def make_signal_id(symbol: str, ts_utc: str) -> str:
    """Детерминированный ID сигнала: sha1(symbol+ts_utc)[:12]."""
    raw = f"{symbol.upper()}|{ts_utc}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def recent_signal_exists(path: str | None, symbol: str, days: int = 7) -> bool:
    """Проверить, был ли сигнал на этот тикер за последние N дней."""
    if not path or not os.path.exists(path):
        return False
    try:
        cutoff = datetime.now(timezone.utc) - __import__("datetime").timedelta(days=days)
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if rec.get("symbol", "").upper() == symbol.upper():
                        ts_str = rec.get("ts_utc", "")
                        if ts_str:
                            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                            if ts >= cutoff:
                                return True
                except (json.JSONDecodeError, ValueError):
                    continue
    except OSError:
        pass
    return False


def build_record_from_report(
    report: "SignalReport",
    ref_price: float,
    currency: str,
) -> dict[str, Any]:
    """Снимок для офлайн-разметки: через N дней сравнить с ценой."""
    ts_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    signal_id = make_signal_id(report.symbol, ts_utc)
    return {
        "signal_id": signal_id,
        "ts_utc": ts_utc,
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
