"""Макро: экономический календарь (Investing.com + Gist fallback) — ставки, инфляция, заседания ЦБ."""

from __future__ import annotations

import json
import os
import random
import re
import threading
import time as _time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

# Investing.com internal endpoint
_INVESTING_CALENDAR_URL = (
    "https://www.investing.com/economic-calendar/Service/getCalendarFilteredData"
)
# Fallback: GitHub Gist mirror of ForexFactory calendar
_GIST_CALENDAR_URL = (
    "https://gist.githubusercontent.com/Thomas-Caufriez/"
    "2074909e9bd9b4bc27ab9884059c2d1b/raw/ff_calendar.json"
)

CRITICAL_KEYWORDS = (
    "fomc", "fed", "interest rate", "rate decision", "ecb",
    "european central bank", "boj", "boe", "bank of england",
    "cpi", "consumer price", "pce", "non-farm", "nonfarm", "nfp",
    "payrolls", "gdp", "jobs report", "unemployment", "powell",
    "press conference", "central bank", "rate statement",
    "bank of russia", "cbr ",
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

    def to_dict(self) -> dict[str, Any]:
        return {"summary": self.summary, "dampening": self.dampening, "headlines": self.headlines}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MacroContext":
        return cls(summary=d["summary"], dampening=d["dampening"], headlines=list(d.get("headlines", [])))


def _parse_time(s: str | None) -> datetime | None:
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S"):
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
            name, re.I,
        )
    )


# ── Investing.com parser ─────────────────────────────────────────────────────

def _parse_investing_html(html: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    rows = re.findall(
        r'<tr[^>]*class="js-event-item[^"]*"[^>]*event_attr_ID="(\d+)"[^>]*data-event-datetime="([^"]+)"',
        html,
    )
    for ev_id, dt_str in rows:
        idx = html.find(f'event_attr_ID="{ev_id}"')
        snippet = html[idx: idx + 1200]
        country_match = re.search(r'data-img_key="([^"]+)"', snippet)
        country = country_match.group(1) if country_match else ""
        name_match = re.search(r'<td[^>]*class="left event"[^>]*>(.*?)</td>', snippet, re.DOTALL)
        if name_match:
            name = re.sub(r"<[^>]+>", "", name_match.group(1)).strip()
        else:
            name = ""
        impact_match = re.search(r'sentiment="([^"]+)"', snippet)
        impact = impact_match.group(1).lower() if impact_match else "low"
        if not impact:
            impact = "low"
        if name:
            events.append({"event": name, "country": country, "time": dt_str, "impact": impact})
    return events


def _fetch_investing() -> list[dict[str, Any]]:
    r = requests.post(
        _INVESTING_CALENDAR_URL,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": "https://www.investing.com/economic-calendar/",
        },
        data="country[]=5&country[]=4&country[]=72&timeZone=18&timeFilter=timeRemain&currentTab=today&limit_from=0",
        timeout=15.0,
    )
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict):
        return []
    return _parse_investing_html(data.get("data", ""))


# ── Gist/ForexFactory fallback ─────────────────────────────────────────────

def _fetch_gist() -> list[dict[str, Any]]:
    r = requests.get(_GIST_CALENDAR_URL, timeout=15.0, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    raw = r.json()
    if not isinstance(raw, list):
        return []
    events: list[dict[str, Any]] = []
    for ev in raw:
        title = str(ev.get("title") or "").strip()
        country = str(ev.get("country") or "")
        impact = str(ev.get("impact") or "").lower()
        if not impact:
            impact = "low"
        date_str = str(ev.get("date") or "")
        if title:
            events.append({"event": title, "country": country, "time": date_str, "impact": impact})
    return events


def fetch_economic_calendar(
    api_key: str | None = None,
    days_back: int = 1,
    days_forward: int = 14,
    timeout: float = 15.0,
) -> list[dict[str, Any]]:
    """Загружает календарь. Сначала Investing.com, при любой ошибке — Gist fallback."""
    try:
        return _fetch_investing()
    except Exception:
        pass
    return _fetch_gist()


# ── Shared file-based cache ──────────────────────────────────────────────────

_MACRO_CACHE_PATH = Path("/tmp/stock_signal_macro_cache.json")
_MACRO_CACHE_TTL: float = 300.0


def _load_shared_cache() -> MacroContext | None:
    try:
        if not _MACRO_CACHE_PATH.exists():
            return None
        mtime = _MACRO_CACHE_PATH.stat().st_mtime
        if _time.monotonic() - mtime > _MACRO_CACHE_TTL:
            return None
        with _MACRO_CACHE_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return MacroContext.from_dict(data)
    except Exception:
        return None


def _save_shared_cache(ctx: MacroContext) -> None:
    try:
        tmp = Path(str(_MACRO_CACHE_PATH) + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(ctx.to_dict(), f, ensure_ascii=False)
        tmp.replace(_MACRO_CACHE_PATH)
    except Exception:
        pass


_macro_cache_lock = threading.Lock()
_macro_cache: MacroContext | None = None
_macro_cache_ts: float = 0.0


# ── Event classification ─────────────────────────────────────────────────────

def _event_severity(name: str, impact: str) -> str:
    low = name.lower()
    if re.search(r"interest rate|rate decision|funds rate|ставк\w+\s+реш", low):
        return "critical"
    if re.search(r"\bcpi\b|consumer price|inflation rate|инфляци", low):
        if re.search(r"expect|survey|forecast|прогноз|ожидан", low):
            return "important"
        return "critical"
    if re.search(r"non[\s-]?farm|nfp|\bpayroll|jobs report", low):
        return "critical"
    if re.search(r"\bgdp\b|gross domestic", low):
        return "critical"
    if re.search(r"press conference|пресс.конф", low):
        return "important"
    if re.search(r"minutes|протокол|statement|заявлен", low):
        return "important"
    if re.search(r"\bpmi\b|manufacturing|services\s+pmi", low):
        return "important"
    if re.search(r"unemployment|безработиц|retail sales|розничн", low):
        return "important"
    if impact == "high":
        return "important"
    return "minor"


def _dampening_for_event(name: str, impact: str, hours_until: float) -> float:
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
    else:
        if hours_until <= 3:
            return 0.90
        return 0.95


def build_macro_context(
    api_key: str | None = None,
    hours_window: float = 48.0,
) -> MacroContext:
    # 1. Shared file cache
    cached = _load_shared_cache()
    if cached is not None:
        with _macro_cache_lock:
            global _macro_cache, _macro_cache_ts
            _macro_cache = cached
            _macro_cache_ts = _time.monotonic()
        return cached

    # 2. Per-process memory cache
    with _macro_cache_lock:
        if _macro_cache is not None and (_time.monotonic() - _macro_cache_ts) < _MACRO_CACHE_TTL:
            return _macro_cache

    try:
        events = fetch_economic_calendar()
    except Exception as e:
        return MacroContext(
            summary=f"Макро: не удалось загрузить календарь ({e}).",
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
            event_damp = _dampening_for_event(name, impact, hours)
            best_damp = min(best_damp, event_damp)

    if not headlines:
        result = MacroContext(
            summary="Макро: в выбранном окне нет отобранных событий (ставки/CPI/NFP и т.п.).",
            dampening=1.0,
            headlines=[],
        )
    else:
        damp = best_damp if critical_near else 1.0
        summ = "Макро (Investing.com/Gist), ключевые события:\n  " + "\n  ".join(headlines[:8])
        if critical_near:
            summ += f"\n  → Повышенная неопределённость: итоговый балл × {damp:.2f}."
        else:
            summ += "\n  → Ближайших критичных релизов в окне 48ч не обнаружено (коэффициент 1.0)."
        result = MacroContext(summary=summ, dampening=damp, headlines=headlines)

    _save_shared_cache(result)
    with _macro_cache_lock:
        _macro_cache = result
        _macro_cache_ts = _time.monotonic()
    return result
