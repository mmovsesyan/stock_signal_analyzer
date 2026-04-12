"""Загрузка исторических котировок через Yahoo Finance (yfinance) с fallback на T-Bank API."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import pandas as pd
import yfinance as yf

from .universe import InstrumentProfile, classify_instrument, history_period_for_profile

_log = logging.getLogger(__name__)


@dataclass
class TickerSnapshot:
    symbol: str
    last_close: float
    currency: str
    company_name: str
    history: pd.DataFrame


def _try_tbank_history(sym: str) -> tuple[pd.DataFrame | None, str, str]:
    """Fallback: дневные свечи из T-Bank API для РФ-тикеров."""
    try:
        from .tbank_invest import fetch_daily_history, tbank_sdk_available
        if not tbank_sdk_available():
            return None, "", ""
        df = fetch_daily_history(sym, days=400)
        if df is None or df.empty:
            return None, "", ""
        name = df.attrs.get("company_name", sym)
        currency = df.attrs.get("currency", "RUB")
        _log.info("T-Bank API: загружено %d дневных свечей для %s", len(df), sym)
        return df, name, currency
    except Exception as e:
        _log.debug("T-Bank fallback failed for %s: %s", sym, e)
        return None, "", ""


def fetch_snapshot_with_meta(symbol: str) -> tuple[TickerSnapshot, dict[str, Any], InstrumentProfile]:
    """
    Загрузка с учётом типа инструмента: для облигаций — более длинный период истории.
    Для .ME тикеров: если Yahoo не отдаёт данные, пробуем T-Bank API.
    """
    sym = symbol.strip().upper()
    t = yf.Ticker(sym)
    info: dict[str, Any] = {}
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
            return snap, info, profile

    if hist is None or hist.empty:
        hint = ""
        if sym.endswith(".ME"):
            hint = " Задайте TINKOFF_INVEST_TOKEN для доступа к РФ-акциям через T-Bank API."
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
    return snap, info, profile


def fetch_history(symbol: str, period: str = "6mo", interval: str = "1d") -> TickerSnapshot:
    """Обратная совместимость: без классификации по типу бумаги."""
    sym = symbol.strip().upper()
    t = yf.Ticker(sym)
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
