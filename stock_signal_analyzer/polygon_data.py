"""Massive (ex-Polygon.io): котировки, исторические свечи, новости и real-time данные (REST API v2/v3).

Переменные окружения:
  POLYGON_API_KEY — ключ Massive/Polygon (обязательно для этого модуля)

Free tier: 5 запросов/мин, задержка 15 мин для котировок.
Paid tier: без задержки, больше лимитов.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import pandas as pd
import requests

from .news_feeds import NewsItem
from .retry_utils import RateLimiter, retry_with_backoff

_log = logging.getLogger(__name__)

POLYGON_BASE_V2 = "https://api.polygon.io/v2"
POLYGON_BASE_V3 = "https://api.polygon.io/v3"

# Fallback если polygon.io не отвечает
_MASSIVE_BASE_V2 = "https://api.massive.com/v2"
_MASSIVE_BASE_V3 = "https://api.massive.com/v3"

# Free tier: 5 req/min. Оставляем запас.
_polygon_limiter = RateLimiter(max_calls=4, period=60.0)


def _api_key() -> str | None:
    return (os.environ.get("POLYGON_API_KEY") or "").strip() or None


def polygon_available() -> bool:
    """Проверить, настроен ли ключ Polygon."""
    return _api_key() is not None


def _headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


def _request_with_fallback(
    method: str,
    path_v2: str | None = None,
    path_v3: str | None = None,
    api_key: str | None = None,
    params: dict | None = None,
    timeout: float = 10.0,
) -> requests.Response:
    """
    Запрос с fallback: сначала polygon.io, если не отвечает — massive.com.
    """
    key = api_key or _api_key()
    if not key:
        raise ValueError("No API key")

    _polygon_limiter.wait_if_needed()
    headers = _headers(key)

    # Определяем URL
    if path_v3:
        primary_url = f"{POLYGON_BASE_V3}{path_v3}"
        fallback_url = f"{_MASSIVE_BASE_V3}{path_v3}"
    elif path_v2:
        primary_url = f"{POLYGON_BASE_V2}{path_v2}"
        fallback_url = f"{_MASSIVE_BASE_V2}{path_v2}"
    else:
        raise ValueError("path_v2 or path_v3 required")

    # Попытка 1: polygon.io
    try:
        r = requests.get(primary_url, headers=headers, params=params, timeout=timeout)
        if r.status_code < 500:  # 4xx — ошибка клиента, не пробуем fallback
            return r
    except requests.RequestException:
        pass

    # Попытка 2: massive.com (fallback)
    r = requests.get(fallback_url, headers=headers, params=params, timeout=timeout)
    return r


# ── Котировки ────────────────────────────────────────────────────────────────


@dataclass
class PolygonQuote:
    """Текущая/последняя котировка из Polygon."""
    symbol: str
    last_price: float | None
    prev_close: float | None
    change_pct: float | None
    volume: int | None
    detail: str


@retry_with_backoff(max_retries=2, initial_delay=2.0, retry_on=(requests.RequestException,))
def fetch_prev_close(symbol: str, api_key: str | None = None, timeout: float = 10.0) -> PolygonQuote:
    """
    Получить предыдущее закрытие через /v2/aggs/ticker/{ticker}/prev.
    Работает на free tier без задержки (данные за предыдущий торговый день).
    """
    key = api_key or _api_key()
    if not key:
        return PolygonQuote(symbol=symbol, last_price=None, prev_close=None,
                           change_pct=None, volume=None, detail="Нет POLYGON_API_KEY.")
    sym = symbol.strip().upper()
    r = _request_with_fallback(
        method="GET",
        path_v2=f"/aggs/ticker/{sym}/prev",
        api_key=key,
        params={"adjusted": "true"},
        timeout=timeout,
    )
    r.raise_for_status()
    data = r.json()
    results = data.get("results") or []
    if not results:
        return PolygonQuote(symbol=sym, last_price=None, prev_close=None,
                           change_pct=None, volume=None, detail=f"Polygon: нет данных по {sym}")
    bar = results[0]
    close = bar.get("c")
    open_ = bar.get("o")
    volume = bar.get("v")
    chg_pct = None
    if close and open_ and open_ != 0:
        chg_pct = (close / open_ - 1.0) * 100.0
    detail = f"Polygon prev close: {close}, open: {open_}, vol: {volume}"
    return PolygonQuote(
        symbol=sym,
        last_price=float(close) if close else None,
        prev_close=float(open_) if open_ else None,
        change_pct=chg_pct,
        volume=int(volume) if volume else None,
        detail=detail,
    )


@retry_with_backoff(max_retries=2, initial_delay=2.0, retry_on=(requests.RequestException,))
def fetch_snapshot(symbol: str, api_key: str | None = None, timeout: float = 10.0) -> PolygonQuote:
    """
    Snapshot тикера. Требует paid plan для real-time. На free tier — fallback на prev_close.
    """
    key = api_key or _api_key()
    if not key:
        return PolygonQuote(symbol=symbol, last_price=None, prev_close=None,
                           change_pct=None, volume=None, detail="Нет POLYGON_API_KEY.")
    sym = symbol.strip().upper()
    try:
        r = _request_with_fallback(
            method="GET",
            path_v2=f"/snapshot/locale/us/markets/stocks/tickers/{sym}",
            api_key=key,
            timeout=timeout,
        )
        if r.status_code == 403:
            _log.debug("Snapshot 403 for %s, falling back to prev_close", sym)
            return fetch_prev_close(sym, api_key=key)
        r.raise_for_status()
        data = r.json()
    except requests.RequestException:
        return fetch_prev_close(sym, api_key=key)

    ticker_data = data.get("ticker") or {}
    day = ticker_data.get("day") or {}
    prev_day = ticker_data.get("prevDay") or {}
    last_price = day.get("c") or prev_day.get("c")
    prev_close = prev_day.get("c")
    volume = day.get("v")
    chg_pct = None
    if last_price and prev_close and prev_close != 0:
        chg_pct = (last_price / prev_close - 1.0) * 100.0
    detail = f"Polygon snapshot: last={last_price}, prev_close={prev_close}, vol={volume}"
    return PolygonQuote(
        symbol=sym,
        last_price=float(last_price) if last_price else None,
        prev_close=float(prev_close) if prev_close else None,
        change_pct=chg_pct,
        volume=int(volume) if volume else None,
        detail=detail,
    )


# ── Исторические свечи ───────────────────────────────────────────────────────


@retry_with_backoff(max_retries=2, initial_delay=2.0, retry_on=(requests.RequestException,))
def fetch_daily_bars(
    symbol: str,
    days: int = 365,
    api_key: str | None = None,
    timeout: float = 15.0,
) -> pd.DataFrame | None:
    """
    Дневные свечи через /v2/aggs/ticker/{ticker}/range/1/day/{from}/{to}.
    Возвращает DataFrame с колонками Open, High, Low, Close, Volume (как yfinance).
    """
    key = api_key or _api_key()
    if not key:
        return None
    sym = symbol.strip().upper()
    to_d = date.today()
    from_d = to_d - timedelta(days=days)
    r = _request_with_fallback(
        method="GET",
        path_v2=f"/aggs/ticker/{sym}/range/1/day/{from_d.isoformat()}/{to_d.isoformat()}",
        api_key=key,
        params={"adjusted": "true", "sort": "asc", "limit": "5000"},
        timeout=timeout,
    )
    r.raise_for_status()
    data = r.json()
    results = data.get("results")
    if not results:
        _log.warning("Polygon: нет дневных баров для %s", sym)
        return None
    rows = []
    for bar in results:
        rows.append({
            "Open": bar.get("o"),
            "High": bar.get("h"),
            "Low": bar.get("l"),
            "Close": bar.get("c"),
            "Volume": bar.get("v", 0),
            "Timestamp": bar.get("t"),
        })
    df = pd.DataFrame(rows)
    # Конвертируем timestamp (ms) в DatetimeIndex
    if "Timestamp" in df.columns and not df.empty:
        df.index = pd.to_datetime(df["Timestamp"], unit="ms", utc=True)
        df.index.name = "Date"
        df.drop(columns=["Timestamp"], inplace=True)
    _log.info("Polygon: загружено %d дневных баров для %s", len(df), sym)
    return df


@retry_with_backoff(max_retries=2, initial_delay=2.0, retry_on=(requests.RequestException,))
def fetch_intraday_bars(
    symbol: str,
    multiplier: int = 5,
    timespan: str = "minute",
    days_back: int = 1,
    api_key: str | None = None,
    timeout: float = 15.0,
) -> pd.DataFrame | None:
    """
    Внутридневные свечи (5-мин по умолчанию).
    Free tier: данные с задержкой 15 мин.
    """
    key = api_key or _api_key()
    if not key:
        return None
    sym = symbol.strip().upper()
    to_d = date.today()
    from_d = to_d - timedelta(days=days_back)
    r = _request_with_fallback(
        method="GET",
        path_v2=f"/aggs/ticker/{sym}/range/{multiplier}/{timespan}/{from_d.isoformat()}/{to_d.isoformat()}",
        api_key=key,
        params={"adjusted": "true", "sort": "asc", "limit": "5000"},
        timeout=timeout,
    )
    r.raise_for_status()
    data = r.json()
    results = data.get("results")
    if not results:
        return None
    rows = []
    for bar in results:
        rows.append({
            "Open": bar.get("o"),
            "High": bar.get("h"),
            "Low": bar.get("l"),
            "Close": bar.get("c"),
            "Volume": bar.get("v", 0),
            "VWAP": bar.get("vw"),
            "Transactions": bar.get("n"),
            "Timestamp": bar.get("t"),
        })
    df = pd.DataFrame(rows)
    if "Timestamp" in df.columns and not df.empty:
        df.index = pd.to_datetime(df["Timestamp"], unit="ms", utc=True)
        df.index.name = "Datetime"
        df.drop(columns=["Timestamp"], inplace=True)
    return df


# ── Новости ──────────────────────────────────────────────────────────────────


@retry_with_backoff(max_retries=2, initial_delay=2.0, retry_on=(requests.RequestException,))
def fetch_ticker_news(
    symbol: str,
    limit: int = 25,
    api_key: str | None = None,
    timeout: float = 10.0,
) -> list[NewsItem]:
    """
    Новости по тикеру через /v2/reference/news.
    """
    key = api_key or _api_key()
    if not key:
        return []
    sym = symbol.strip().upper()
    if sym.endswith(".ME"):
        return []
    r = _request_with_fallback(
        method="GET",
        path_v2="/reference/news",
        api_key=key,
        params={
            "ticker": sym,
            "limit": str(min(limit, 50)),
            "order": "desc",
            "sort": "published_utc",
        },
        timeout=timeout,
    )
    r.raise_for_status()
    data = r.json()
    results = data.get("results") or []
    out: list[NewsItem] = []
    for item in results:
        title = (item.get("title") or "").strip()
        if not title:
            continue
        link = item.get("article_url") or ""
        source = item.get("publisher", {}).get("name", "Polygon")
        pub_ts: float | None = None
        pub_utc = item.get("published_utc")
        if pub_utc:
            try:
                from datetime import datetime, timezone
                dt = datetime.fromisoformat(pub_utc.replace("Z", "+00:00"))
                pub_ts = dt.timestamp()
            except (ValueError, TypeError):
                pass
        out.append(NewsItem(title=title[:500], link=str(link), source=str(source), published_ts=pub_ts))
    return out


# ── Детали тикера ────────────────────────────────────────────────────────────


@dataclass
class PolygonTickerDetails:
    """Информация о компании из Polygon."""
    name: str
    market_cap: float | None
    description: str
    sic_description: str
    locale: str
    currency: str


@retry_with_backoff(max_retries=2, initial_delay=2.0, retry_on=(requests.RequestException,))
def fetch_ticker_details(
    symbol: str,
    api_key: str | None = None,
    timeout: float = 10.0,
) -> PolygonTickerDetails | None:
    """Детали тикера через /v3/reference/tickers/{ticker}."""
    key = api_key or _api_key()
    if not key:
        return None
    sym = symbol.strip().upper()
    if sym.endswith(".ME"):
        return None
    try:
        r = _request_with_fallback(
            method="GET",
            path_v3=f"/reference/tickers/{sym}",
            api_key=key,
            timeout=timeout,
        )
        r.raise_for_status()
        data = r.json()
    except requests.RequestException:
        return None
    res = data.get("results") or {}
    if not res:
        return None
    return PolygonTickerDetails(
        name=res.get("name") or sym,
        market_cap=res.get("market_cap"),
        description=(res.get("description") or "")[:500],
        sic_description=res.get("sic_description") or "",
        locale=res.get("locale") or "us",
        currency=res.get("currency_name") or "usd",
    )
