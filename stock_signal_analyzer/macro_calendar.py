"""Макро: экономический календарь (Investing.com) — ставки, инфляция, заседания ЦБ."""

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

# Investing.com internal endpoint used by their SPA
_INVESTING_CALENDAR_URL = (
    "https://www.investing.com/economic-calendar/Service/getCalendarFilteredData"
)

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


@dataclass
class MacroContext:
    summary: str
    dampening: float
    headlines: list[str]


def _parse_time(s: str | None) -> datetime | None:
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S"):
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


def _parse_investing_events(html: str) -> list[dict[str, Any]]:
    """Парсим HTML таблицу Investing.com → список событий."""
    events: list[dict[str, Any]] = []
    # Строки событий
    rows = re.findall(
        r'<tr[^>]*class="js-event-item[^"]*"[^>]*event_attr_ID="(\d+)"[^>]*data-event-datetime="([^"]+)"',
        html,
    )
    for ev_id, dt_str in rows:
        idx = html.find(f'event_attr_ID="{ev_id}"')
        snippet = html[idx : idx + 1200]

        # Страна
        country_match = re.search(r'data-img_key="([^"]+)"', snippet)
        country = country_match.group(1) if country_match else ""

        # Название события
        name_match = re.search(
            r'<td[^>]*class="left event"[^>]*>(.*?)</td>', snippet, re.DOTALL
        )
        if name_match:
            name_html = name_match.group(1)
            name = re.sub(r"<[^>]+>", "", name_html).strip()
        else:
            name = ""

        # Impact (bull/bear icons; если не найден — low по умолчанию)
        impact_match = re.search(r'sentiment="([^"]+)"', snippet)
        impact = impact_match.group(1).lower() if impact_match else "low"
        if not impact:
            impact = "low"

        if name:
            events.append(
                {
                    "event": name,
                    "country": country,
                    "time": dt_str,
                    "impact": impact,
                }
            )
    return events


@retry_with_backoff(
    max_retries=2,
    initial_delay=0.5,
    backoff_factor=2.0,
    retry_on=(requests.RequestException,),
)
def _investing_calendar_get(timeout: float) -> requests.Response:
    """POST к Investing.com economic calendar с retry."""
    r = requests.post(
        _INVESTING_CALENDAR_URL,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": "https://www.investing.com/economic-calendar/",
        },
        data="country[]=5&country[]=4&country[]=72&timeZone=18&timeFilter=timeRemain&currentTab=today&limit_from=0",
        timeout=timeout,
    )
    r.raise_for_status()
    return r


def fetch_economic_calendar(
    api_key: str | None = None,
    days_back: int = 1,
    days_forward: int = 14,
    timeout: float = 15.0,
) -> list[dict[str, Any]]:
    """Загружает экономический календарь с Investing.com.

    Параметры days_back/days_forward сохранены для совместимости,
    но Investing.com возвращает события текущего дня.
    """
    r = _investing_calendar_get(timeout)
    data = r.json()
    if not isinstance(data, dict):
        return []
    html = data.get("data", "")
    return _parse_investing_events(html)


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

    try:
        events = fetch_economic_calendar()
    except Exception as e:
        return MacroContext(
            summary=f"Макро: не удалось загрузить календарь Investing.com ({e}).",
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
            summary="Макро (Investing.com): в выбранном окне нет отобранных событий (ставки/CPI/NFP и т.п.).",
            dampening=1.0,
            headlines=[],
        )

    damp = best_damp if critical_near else 1.0
    summ = "Макро (Investing.com), ключевые события:\n  " + "\n  ".join(headlines[:8])
    if critical_near:
        summ += f"\n  → Повышенная неопределённость: итоговый балл × {damp:.2f}."
    else:
        summ += "\n  → Ближайших критичных релизов в окне 48ч не обнаружено (коэффициент 1.0)."

    result = MacroContext(summary=summ, dampening=damp, headlines=headlines)
    with _macro_cache_lock:
        _macro_cache = result
        _macro_cache_ts = _time.monotonic()
    return result
