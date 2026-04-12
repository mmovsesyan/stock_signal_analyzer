"""
Своевременность и контекст: недельный тренд, окно отчётности (Yahoo calendar), фон индекса (SPY / IMOEX).

Цель — снизить сигналы «против старшего тренда» и перед гэпами отчётности, не гарантируя идеальные входы.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd
import yfinance as yf


def _parse_earnings_date(cal: dict[str, Any]) -> date | None:
    raw = cal.get("Earnings Date")
    if raw is None:
        return None
    if isinstance(raw, (list, tuple)) and len(raw) > 0:
        d = raw[0]
    else:
        d = raw
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    return None


def _earnings_from_calendar(cal: Any) -> tuple[float, str, bool]:
    """Вычисляет (mult, note, earn_bad) из уже полученного calendar-объекта."""
    if not isinstance(cal, dict):
        return 1.0, "", False
    ed = _parse_earnings_date(cal)
    if ed is None:
        return 1.0, "отчётность: дата неизвестна", False
    today = date.today()
    days = (ed - today).days
    if abs(days) <= 2:
        return 0.87, f"⚠ окно отчётности ({ed.isoformat()}, Δ={days:+d} дн.)", True
    if abs(days) <= 6:
        return 0.93, f"близко отчётность {ed.isoformat()} (Δ={days:+d} дн.)", False
    return 1.0, f"след. отчётность ~{ed.isoformat()}", False


def _weekly_from_history(w: pd.DataFrame | None) -> tuple[str, str]:
    """Вычисляет (regime, note) из уже полученного history DataFrame."""
    if w is None or (hasattr(w, "empty") and w.empty) or len(w) < 22:
        return "unknown", "недельный график: мало данных"
    c = w["Close"].astype(float)
    sma = float(c.rolling(20).mean().iloc[-1])
    last = float(c.iloc[-1])
    if sma <= 0 or np.isnan(sma):
        return "unknown", "недельный график: SMA н/д"
    dev = (last / sma - 1.0) * 100.0
    if last > sma * 1.012:
        return "up", f"неделя: выше SMA20 (~{dev:+.1f}% к SMA)"
    if last < sma * 0.988:
        return "down", f"неделя: ниже SMA20 (~{dev:+.1f}% к SMA)"
    return "flat", f"неделя: у SMA20 (~{dev:+.1f}%)"


def build_timing_context(symbol: str) -> tuple[float, str, bool, str, str]:
    """
    Возвращает (earnings_mult, earnings_note, earn_bad, weekly_regime, weekly_note).
    Один yf.Ticker на оба запроса.
    """
    t = yf.Ticker(symbol)

    # --- earnings ---
    try:
        cal = t.calendar
        earnings_mult, earnings_note, earn_bad = _earnings_from_calendar(cal)
    except Exception:
        earnings_mult, earnings_note, earn_bad = 1.0, "отчётность: нет данных", False

    # --- weekly regime ---
    try:
        w = t.history(period="18mo", interval="1wk", auto_adjust=True)
        weekly_regime, weekly_note = _weekly_from_history(w)
    except Exception as e:
        weekly_regime, weekly_note = "unknown", f"неделя: ошибка ({e})"

    return earnings_mult, earnings_note, earn_bad, weekly_regime, weekly_note


def earnings_dampening(symbol: str) -> tuple[float, str, bool]:
    """
    Множитель к итоговому баллу около отчётности, текст, флаг «опасное окно» (±2 календарных дня).
    """
    try:
        t = yf.Ticker(symbol)
        cal = t.calendar
        return _earnings_from_calendar(cal)
    except Exception:
        return 1.0, "отчётность: нет данных", False


def weekly_trend_regime(symbol: str) -> tuple[str, str]:
    """
    Режим по недельным свечам: цена vs SMA20 недель.
    up / down / flat / unknown
    """
    try:
        w = yf.Ticker(symbol).history(period="18mo", interval="1wk", auto_adjust=True)
        return _weekly_from_history(w)
    except Exception as e:
        return "unknown", f"неделя: ошибка ({e})"


def weekly_aligns_direction(total: float, regime: str) -> bool:
    """Согласованность дневного знака с недельным контекстом."""
    if abs(total) < 0.12:
        return True
    if regime == "unknown":
        return True
    if total >= 0.14:
        return regime in ("up", "flat")
    if total <= -0.14:
        return regime in ("down", "flat")
    return True


def index_tailwind_mult(symbol: str, total: float) -> tuple[float, str, bool]:
    """
    Если рынок в целом против направления сильного сигнала — лёгкое снижение балла.
    Возвращает (mult, note, is_headwind).
    """
    if abs(total) < 0.14:
        return 1.0, "", False
    idx = "IMOEX.ME" if symbol.upper().endswith(".ME") else "SPY"
    try:
        h = yf.Ticker(idx).history(period="4mo", interval="1d", auto_adjust=True)
        if h is None or h.empty or len(h) < 25:
            return 1.0, f"{idx}: нет истории", False
        c = h["Close"].astype(float)
        r20 = float(c.iloc[-1] / c.iloc[-21] - 1.0)
        label = f"{idx} ~20д: {r20*100:+.1f}%"
        if total >= 0.16 and r20 < -0.045:
            return 0.90, label + " (слабый фон для long)", True
        if total <= -0.16 and r20 > 0.045:
            return 0.90, label + " (сильный фон для контртрендового short)", True
        return 1.0, label, False
    except Exception as e:
        return 1.0, f"{idx}: {e}", False


def stop_hint_atr_multiple(atr_pct: float | None, mult: float = 1.5) -> float | None:
    """Ориентир расстояния стопа в % от цены (не совет, а масштаб риска)."""
    if atr_pct is None or atr_pct <= 0:
        return None
    return float(round(atr_pct * mult, 3))
