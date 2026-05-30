"""Макро: экономический календарь (Finnhub) — ставки, инфляция, заседания ЦБ."""

from __future__ import annotations

import os
import re
import threading
import time as _time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

import requests

from .retry_utils import retry_with_backoff

FINNHUB_BASE = "https://finnhub.io/api/v1"


@retry_with_backoff(max_retries=2, initial_delay=0.5, backoff_factor=2.0,
                    retry_on=(requests.RequestException,))
def _finnhub_economic_get(d_from: str, d_to: str, token: str, timeout: float) -> requests.Response:
    """HTTP GET к Finnhub economic calendar с retry."""
    r = requests.get(
        f"{FINNHUB_BASE}/calendar/economic",
        params={"from": d_from, "to": d_to, "token": token},
        timeout=timeout,
    )
    r.raise_for_status()
    return r


CRITICAL_KEYWORDS = (
    "fomc",
    "fed",
    "interest rate",
    "rate decision",
    "ecb",
    "european central bank",
    "boj",
    "boe",
    "bank of england",
    "cpi",
    "consumer price",
    "pce",
    "non-farm",
    "nonfarm",
    "nfp",
    "payrolls",
    "gdp",
    "jobs report",
    "unemployment",
    "powell",
    "press conference",
    "central bank",
    "rate statement",
    "bank of russia",
    "cbr ",
)

IMPACT_RANK = {"high": 3, "medium": 2, "low": 1, "": 0}


def _ev_time(dt: datetime | None) -> str:
    if dt is None:
        return "??:??"
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def _token() -> str | None:
    return os.environ.get("FINNHUB_API_KEY") or os.environ.get("FINNHUB_TOKEN")


@dataclass
class MacroContext:
    summary: str
    dampening: float
    headlines: list[str]


def _parse_time(s: str | None) -> datetime | None:
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            raw = str(s)[:19] if len(str(s)) >= 10 else str(s)
            dt = datetime.strptime(raw, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _is_critical_event(name: str) -> bool:
    low = name.lower()
    return any(k in low for k in CRITICAL_KEYWORDS)


def _strong_macro_name(name: str) -> bool:
    return bool(
        re.search(
            r"fomc|fed interest|ecb interest|rate decision|cpi|nfp|non[\s-]?farm|payroll|gdp\s",
            name,
            re.I,
        )
    )


def fetch_economic_calendar(
    api_key: str | None = None,
    days_back: int = 1,
    days_forward: int = 14,
    timeout: float = 15.0,
) -> list[dict[str, Any]]:
    key = api_key or _token()
    if not key:
        return []
    from .finnhub_live import _rate_wait
    today = date.today()
    d_from = today - timedelta(days=days_back)
    d_to = today + timedelta(days=days_forward)
    _rate_wait()
    r = _finnhub_economic_get(d_from.isoformat(), d_to.isoformat(), key, timeout)
    data = r.json()
    if not isinstance(data, dict):
        return []
    return list(data.get("economicCalendar") or [])


_macro_cache_lock = threading.Lock()
_macro_cache: MacroContext | None = None
_macro_cache_ts: float = 0.0
_MACRO_CACHE_TTL: float = 300.0


# ── Классификация событий по реальному влиянию на рынок ──────────────────────

def _event_severity(name: str, impact: str) -> str:
    """
    Классифицировать событие по реальному влиянию:
    - 'critical': решение по ставке, CPI, NFP, GDP — двигают рынок на 1-3%
    - 'important': пресс-конференция, протоколы, PMI — двигают на 0.3-1%
    - 'minor': речи чиновников, обзоры, отчёты — обычно < 0.3%
    """
    low = name.lower()
    # Критические — реально двигают рынок
    if re.search(r"interest rate|rate decision|funds rate|ставк\w+\s+реш", low):
        return "critical"
    if re.search(r"\bcpi\b|consumer price|inflation rate|инфляци", low):
        # CPI Expectations/Survey — это не сам релиз CPI
        if re.search(r"expect|survey|forecast|прогноз|ожидан", low):
            return "important"
        return "critical"
    if re.search(r"non[\s-]?farm|nfp|\bpayroll|jobs report", low):
        return "critical"
    if re.search(r"\bgdp\b|gross domestic", low):
        return "critical"
    # Важные — могут двинуть рынок
    if re.search(r"press conference|пресс.конф", low):
        return "important"
    if re.search(r"minutes|протокол|statement|заявлен", low):
        return "important"
    if re.search(r"\bpmi\b|manufacturing|services\s+pmi", low):
        return "important"
    if re.search(r"unemployment|безработиц|retail sales|розничн", low):
        return "important"
    # Остальное — речи, обзоры, прогнозы
    if impact == "high":
        return "important"
    return "minor"


def _dampening_for_event(name: str, impact: str, hours_until: float) -> float:
    """Dampening для конкретного события в зависимости от типа и времени до него."""
    severity = _event_severity(name, impact)

    if severity == "critical":
        if hours_until <= 3:
            return 0.48
        if hours_until <= 6:
            return 0.55
        if hours_until <= 24:
            return 0.65
        return 0.75
    elif severity == "important":
        if hours_until <= 3:
            return 0.72
        if hours_until <= 6:
            return 0.80
        if hours_until <= 24:
            return 0.88
        return 0.93
    else:  # minor — речи, обзоры
        if hours_until <= 3:
            return 0.90
        return 0.95


def build_macro_context(
    api_key: str | None = None,
    hours_window: float = 48.0,
) -> MacroContext:
    """
    Ближайшие важные события (ставки, инфляция, занятость, ВВП, заседания ЦБ).
    dampening: 1.0 если спокойно; ~0.55–0.72 если критичное событие в пределах часов_window.
    """
    global _macro_cache, _macro_cache_ts
    with _macro_cache_lock:
        if _macro_cache is not None and (_time.monotonic() - _macro_cache_ts) < _MACRO_CACHE_TTL:
            return _macro_cache

    key = api_key or _token()
    if not key:
        return MacroContext(
            summary="Макро: укажите FINNHUB_API_KEY — тогда подтянется экономический календарь (ставки, CPI, NFP, ЦБ).",
            dampening=1.0,
            headlines=[],
        )

    try:
        events = fetch_economic_calendar(api_key=key)
    except Exception as e:
        return MacroContext(
            summary=f"Макро: не удалось загрузить календарь Finnhub ({e}).",
            dampening=1.0,
            headlines=[],
        )

    now = datetime.now(timezone.utc)
    rows: list[tuple[datetime | None, str, str, str]] = []
    for ev in events:
        name = str(ev.get("event") or "").strip()
        if not name or not _is_critical_event(name):
            continue
        impact = str(ev.get("impact") or "").lower()
        if IMPACT_RANK.get(impact, 0) < 2 and not _strong_macro_name(name):
            continue
        dt = _parse_time(str(ev.get("time") or "") or None)
        country = str(ev.get("country") or "")
        rows.append((dt, country, name, impact))

    rows.sort(key=lambda x: x[0] or datetime(2100, 1, 1, tzinfo=timezone.utc))

    headlines: list[str] = []
    best_damp = 1.0
    critical_near = False

    for dt, country, name, impact in rows[:20]:
        line = f"{_ev_time(dt)} [{country}] {name} (impact={impact})"
        headlines.append(line)
        if dt is None:
            continue
        hours = (dt - now).total_seconds() / 3600.0
        if -2.0 <= hours <= hours_window:
            critical_near = True
            # Разный dampening по типу события
            event_damp = _dampening_for_event(name, impact, hours)
            best_damp = min(best_damp, event_damp)

    if not headlines:
        return MacroContext(
            summary="Макро (Finnhub): в выбранном окне нет отобранных событий (ставки/CPI/NFP и т.п.).",
            dampening=1.0,
            headlines=[],
        )

    damp = best_damp if critical_near else 1.0
    summ = "Макро (Finnhub), ключевые события:\n  " + "\n  ".join(headlines[:8])
    if critical_near:
        summ += f"\n  → Повышенная неопределённость: итоговый балл × {damp:.2f}."
    else:
        summ += "\n  → Ближайших критичных релизов в окне 48ч не обнаружено (коэффициент 1.0)."

    result = MacroContext(summary=summ, dampening=damp, headlines=headlines)
    with _macro_cache_lock:
        _macro_cache = result
        _macro_cache_ts = _time.monotonic()
    return result
