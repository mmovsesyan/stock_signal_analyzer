"""Загрузка исторических котировок через Yahoo Finance (yfinance) с fallback на T-Bank API."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import pandas as pd
import yfinance as yf

from .universe import InstrumentProfile, classify_instrument, history_period_for_profile

_log = logging.getLogger(__name__)

# ── Кэш для котировок ────────────────────────────────────────────────────────
_CACHE: dict[str, tuple[tuple[TickerSnapshot, dict[str, Any], InstrumentProfile], float]] = {}
_CACHE_TTL = 300  # 5 минут


@dataclass
class TickerSnapshot:
    symbol: str
    last_close: float
    currency: str
    company_name: str
    history: pd.DataFrame


def _symbol_for_yahoo(sym: str) -> str:
    """
    Нормализация тикера под формат Yahoo:
    - BRK.B -> BRK-B
    - другие US тикеры с точкой -> через дефис
    - MOEX (.ME) не трогаем
    """
    if sym.endswith(".ME"):
        return sym
    if "." in sym:
        return sym.replace(".", "-")
    return sym


def _tbank_available() -> bool:
    try:
        from .tbank_invest import tbank_sdk_available

        return bool(tbank_sdk_available())
    except Exception:
        return False


def _tbank_hint(sym: str) -> str:
    if not sym.endswith(".ME"):
        return ""
    tok = (os.environ.get("TINKOFF_INVEST_TOKEN") or os.environ.get("TINKOFF_TOKEN") or "").strip()
    if not tok:
        return " T-Bank токен не найден: задайте TINKOFF_INVEST_TOKEN (или TINKOFF_TOKEN)."
    if not _tbank_available():
        return " T-Bank SDK не готов: установите requirements-tbank.txt и проверьте токен."
    return " T-Bank API не вернул историю по тикеру (проверьте права токена/доступность инструмента)."


def _try_tbank_history(sym: str) -> tuple[pd.DataFrame | None, str, str]:
    """Fallback: дневные свечи из T-Bank API для РФ-тикеров."""
    try:
        from .tbank_invest import fetch_daily_history, tbank_sdk_available
        if not tbank_sdk_available():
            _log.warning("T-Bank недоступен для %s: токен/SDK не готовы", sym)
            return None, "", ""
        df = fetch_daily_history(sym, days=400)
        if df is None or df.empty:
            _log.warning("T-Bank не вернул дневную историю для %s", sym)
            return None, "", ""
        name = df.attrs.get("company_name", sym)
        currency = df.attrs.get("currency", "RUB")
        _log.info("T-Bank API: загружено %d дневных свечей для %s", len(df), sym)
        return df, name, currency
    except Exception as e:
        _log.warning("T-Bank fallback failed for %s: %s", sym, e)
        return None, "", ""


def fetch_snapshot_with_meta(symbol: str, force_refresh: bool = False) -> tuple[TickerSnapshot, dict[str, Any], InstrumentProfile]:
    """
    Загрузка с учётом типа инструмента: для облигаций — более длинный период истории.
    Для .ME тикеров: если Yahoo не отдаёт данные, пробуем T-Bank API.

    Args:
        symbol: Тикер для загрузки
        force_refresh: Если True, игнорировать кэш и загрузить свежие данные
    """
    sym = symbol.strip().upper()

    # Проверка кэша
    if not force_refresh and sym in _CACHE:
        cached_data, timestamp = _CACHE[sym]
        age = time.time() - timestamp
        if age < _CACHE_TTL:
            _log.debug("Используем кэшированные данные для %s (возраст: %.1fs)", sym, age)
            return cached_data

    ysym = _symbol_for_yahoo(sym)
    info: dict[str, Any] = {}

    # Для РФ-тикеров при доступном SDK сразу идём в T-Bank: это быстрее и без шума Yahoo.
    if sym.endswith(".ME") and _tbank_available():
        tb_hist, tb_name, tb_currency = _try_tbank_history(sym)
        if tb_hist is not None and not tb_hist.empty:
            profile = classify_instrument(sym, {})
            last = float(tb_hist["Close"].iloc[-1])
            snap = TickerSnapshot(
                symbol=sym,
                last_close=last,
                currency=tb_currency or "RUB",
                company_name=tb_name or sym,
                history=tb_hist,
            )
            result = (snap, info, profile)
            _CACHE[sym] = (result, time.time())
            return result

    t = yf.Ticker(ysym)
    try:
        info = t.info or {}
    except Exception:
        pass
    profile = classify_instrument(sym, info)
    period = history_period_for_profile(profile)
    hist = t.history(period=period, interval="1d", auto_adjust=True)

    # Fallback на T-Bank API для РФ-тикеров
    if (hist is None or hist.empty) and sym.endswith(".ME"):
        _log.info("Yahoo Finance не отдал данные по %s, пробуем T-Bank API…", sym)
        tb_hist, tb_name, tb_currency = _try_tbank_history(sym)
        if tb_hist is not None and not tb_hist.empty:
            last = float(tb_hist["Close"].iloc[-1])
            snap = TickerSnapshot(
                symbol=sym,
                last_close=last,
                currency=tb_currency or "RUB",
                company_name=tb_name or sym,
                history=tb_hist,
            )
            result = (snap, info, profile)
            _CACHE[sym] = (result, time.time())
            return result

    if hist is None or hist.empty:
        hint = _tbank_hint(sym)
        raise ValueError(
            f"Нет данных по тикеру {sym}. Проверьте биржу/суффикс "
            f"(например SBER.ME для Мосбиржи, TLT для облигаций US).{hint}"
        )
    last = float(hist["Close"].iloc[-1])
    currency = str(info.get("currency") or "USD")
    name = str(info.get("longName") or info.get("shortName") or sym)
    snap = TickerSnapshot(
        symbol=sym,
        last_close=last,
        currency=currency,
        company_name=name,
        history=hist,
    )
    result = (snap, info, profile)
    _CACHE[sym] = (result, time.time())
    return result


def fetch_history(symbol: str, period: str = "6mo", interval: str = "1d") -> TickerSnapshot:
    """Обратная совместимость: без классификации по типу бумаги."""
    sym = symbol.strip().upper()
    ysym = _symbol_for_yahoo(sym)

    if sym.endswith(".ME") and _tbank_available():
        tb_hist, tb_name, tb_currency = _try_tbank_history(sym)
        if tb_hist is not None and not tb_hist.empty:
            last = float(tb_hist["Close"].iloc[-1])
            return TickerSnapshot(
                symbol=sym,
                last_close=last,
                currency=tb_currency or "RUB",
                company_name=tb_name or sym,
                history=tb_hist,
            )

    t = yf.Ticker(ysym)
    hist = t.history(period=period, interval=interval, auto_adjust=True)
    if hist is None or hist.empty:
        raise ValueError(
            f"Нет данных по тикеру {sym}. Проверьте биржу/суффикс (например SBER.ME для Мосбиржи)."
        )
    last = float(hist["Close"].iloc[-1])
    currency = "USD"
    name = sym
    try:
        info = t.info or {}
        currency = str(info.get("currency") or currency)
        name = str(info.get("longName") or info.get("shortName") or name)
    except Exception:
        pass
    return TickerSnapshot(
        symbol=sym,
        last_close=last,
        currency=currency,
        company_name=name,
        history=hist,
    )
