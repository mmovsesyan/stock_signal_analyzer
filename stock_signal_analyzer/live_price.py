"""Единая функция получения актуальной цены из real-time источников.

Приоритет для РФ (.ME):
  1. T-Bank (Т-Инвестиции)
  2. MOEX ISS (бесплатно, без токена)

Приоритет для US:
  1. Finnhub (если задан FINNHUB_API_KEY)
  2. Polygon.io (если задан POLYGON_API_KEY)

Используется в telegram_bot.py, api/main.py, engine.py — один источник истины.
"""
from __future__ import annotations

import os


def fetch_live_price(symbol: str) -> float | None:
    """Получить актуальную цену из real-time источников.

    Returns:
        Цена или None, если ни один источник не ответил.
    """
    sym = symbol.strip().upper()

    if sym.endswith(".ME"):
        return _fetch_ru_price(sym)

    return _fetch_us_price(sym)


def _fetch_ru_price(sym: str) -> float | None:
    """Цена для РФ-тикером: T-Bank → MOEX ISS."""
    # 1. T-Bank
    try:
        from .tbank_invest import fetch_last_price_tbank
        q = fetch_last_price_tbank(sym)
        if q and q.last_price > 0:
            return q.last_price
    except Exception:
        pass
    # 2. MOEX ISS
    try:
        from .moex_iss import fetch_tqbr_quote
        mq = fetch_tqbr_quote(sym)
        if mq.last is not None and mq.last > 0:
            return mq.last
    except Exception:
        pass
    return None


def _fetch_us_price(sym: str) -> float | None:
    """Цена для US-тикером: Finnhub → Polygon."""
    key = os.environ.get("FINNHUB_API_KEY") or os.environ.get("FINNHUB_TOKEN")
    if key:
        try:
            from .finnhub_live import fetch_quote
            fq = fetch_quote(sym, api_key=key)
            if fq.current is not None and fq.current > 0:
                return fq.current
        except Exception:
            pass
    try:
        from .polygon_data import polygon_available, fetch_snapshot as pg_snap
        if polygon_available():
            pq = pg_snap(sym)
            if pq.last_price is not None and pq.last_price > 0:
                return pq.last_price
    except Exception:
        pass
    return None
