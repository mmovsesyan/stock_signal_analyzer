"""Загрузка исторических котировок через Yahoo Finance (yfinance) с fallback на T-Bank API и Polygon.io."""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import pandas as pd
import yfinance as yf

from .circuit_breaker import CircuitBreaker, CircuitOpenError
from .universe import InstrumentProfile, classify_instrument, history_period_for_profile

_log = logging.getLogger(__name__)

# Circuit breaker for Yahoo Finance (primary data source)
# Tuned for batch scans: higher threshold + longer recovery to survive
# rate-limit spikes during scheduler collection of ~30 tickers.
_yf_circuit = CircuitBreaker(
    name="yahoo_finance",
    failure_threshold=15,
    recovery_timeout=180.0,
    expected_exception=(Exception,),
)

# Global YF request throttle to avoid rate-limiting during batch scans
_YF_LAST_CALL: float = 0.0
_YF_MIN_DELAY: float = 0.25  # 250 ms between YF requests
_yf_rate_lock = threading.Lock()


def _is_yf_data_error(exc: Exception) -> bool:
    """Return True if exception is a 'no data' error (not a service failure)."""
    msg = str(exc).lower()
    data_phrases = (
        "delisted", "no price data", "no timezone",
        "not found", "no data found", "symbol may be delisted",
        "404", "no data",
    )
    return any(p in msg for p in data_phrases)


@_yf_circuit
def _fetch_yf_data(ysym: str, period: str) -> tuple[dict[str, Any], pd.DataFrame]:
    """Fetch Yahoo Finance info and history with circuit breaker protection.

    Data errors (delisted, no price data) are caught and returned as empty
    DataFrames so they do NOT trip the circuit breaker. Only service-level
    failures (network, timeout, HTTP 5xx/429) propagate to the breaker.
    """
    global _YF_LAST_CALL
    with _yf_rate_lock:
        elapsed = time.time() - _YF_LAST_CALL
        if elapsed < _YF_MIN_DELAY:
            time.sleep(_YF_MIN_DELAY - elapsed)
        _YF_LAST_CALL = time.time()

    t = yf.Ticker(ysym)
    info: dict[str, Any] = {}
    try:
        info = t.info or {}
    except Exception:
        pass

    try:
        hist = t.history(period=period, interval="1d", auto_adjust=True)
    except Exception as exc:
        if _is_yf_data_error(exc):
            _log.debug("YF: no data for %s (%s) — treated as empty", ysym, exc)
            return info, pd.DataFrame()
        _log.warning("YF: service error for %s: %s", ysym, exc)
        raise
    return info, hist


_CACHE_MAX_SIZE = 200
_CACHE: dict[str, tuple[tuple[Any, Any, Any], float]] = {}
_CACHE_TTL = 300  # 5 минут
_cache_lock = threading.Lock()  # защищает _CACHE от concurrent mutation


def _polygon_available() -> bool:
    """Проверить доступность Polygon.io API."""
    try:
        from .polygon_data import polygon_available
        return polygon_available()
    except Exception:
        return False


def _try_polygon_history(sym: str, days: int = 400) -> tuple[pd.DataFrame | None, str, str]:
    """Fallback: дневные свечи из Polygon.io для US-тикеров."""
    try:
        from .polygon_data import fetch_daily_bars, fetch_ticker_details
        df = fetch_daily_bars(sym, days=days)
        if df is None or df.empty:
            return None, "", ""
        name = sym
        currency = "USD"
        details = fetch_ticker_details(sym)
        if details:
            name = details.name
            currency = details.currency.upper() if details.currency else "USD"
        _log.info("Polygon.io: загружено %d дневных свечей для %s", len(df), sym)
        return df, name, currency
    except Exception as e:
        _log.warning("Polygon fallback failed for %s: %s", sym, e)
        return None, "", ""

def _evict_expired_cache() -> None:
    """Удалить просроченные записи из кэша. Caller должен держать _cache_lock."""
    now = time.time()
    expired = [sym for sym, (_, ts) in _CACHE.items() if now - ts >= _CACHE_TTL]
    for sym in expired:
        del _CACHE[sym]


def _ensure_cache_capacity() -> None:
    """Если кэш превысил maxsize, удалить самые старые записи. Caller должен держать _cache_lock."""
    if len(_CACHE) <= _CACHE_MAX_SIZE:
        return
    sorted_items = sorted(_CACHE.items(), key=lambda x: x[1][1])
    to_remove = len(_CACHE) - _CACHE_MAX_SIZE
    for sym, _ in sorted_items[:to_remove]:
        del _CACHE[sym]


def _cache_get(sym: str) -> tuple[Any, Any, Any] | None:
    """Thread-safe чтение из кэша. Возвращает None если нет или просрочен."""
    with _cache_lock:
        _evict_expired_cache()
        _ensure_cache_capacity()
        if sym in _CACHE:
            cached_data, timestamp = _CACHE[sym]
            if time.time() - timestamp < _CACHE_TTL:
                return cached_data
        return None


def _cache_set(sym: str, result: tuple[Any, Any, Any]) -> None:
    """Thread-safe запись в кэш."""
    with _cache_lock:
        _evict_expired_cache()
        _ensure_cache_capacity()
        _CACHE[sym] = (result, time.time())


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


# Имена РФ компаний (fallback если T-Bank недоступен)
_RU_COMPANY_NAMES: dict[str, str] = {
    "SBER": "Сбербанк", "GAZP": "Газпром", "LKOH": "ЛУКОЙЛ",
    "GMKN": "Норникель", "NVTK": "НОВАТЭК", "ROSN": "Роснефть",
    "TATN": "Татнефть", "MOEX": "Мосбиржа", "MGNT": "Магнит",
    "YDEX": "Яндекс", "PLZL": "Полюс Золото", "ALRS": "АЛРОСА",
    "VTBR": "ВТБ", "SNGS": "Сургутнефтегаз", "MTSS": "МТС",
    "AFLT": "Аэрофлот", "PHOR": "ФосАгро", "IRAO": "Интер РАО",
    "OZON": "Ozon", "TCSG": "Т-Банк", "RUAL": "Русал",
    "MAGN": "ММК", "PIKK": "ПИК", "POLY": "Полиметалл",
    "FEES": "Россети", "CHMF": "Северсталь", "NLMK": "НЛМК",
    "RTKM": "Ростелеком", "HYDR": "РусГидро", "TRNFP": "Транснефть",
    "FIVE": "X5 Group", "SGZH": "Сегежа", "VKCO": "VK",
}


def _resolve_ru_company_name(sym: str) -> str:
    """Получить имя РФ компании: T-Bank → словарь → тикер."""
    base = sym.replace(".ME", "").strip().upper()
    # Попробовать T-Bank (быстрый запрос)
    try:
        from .tbank_invest import fetch_last_price_tbank
        q = fetch_last_price_tbank(sym)
        if q and q.name and q.name != base:
            return q.name
    except Exception:
        pass
    # Словарь
    return _RU_COMPANY_NAMES.get(base, base)


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

    # Проверка кэша (thread-safe)
    if not force_refresh:
        cached = _cache_get(sym)
        if cached is not None:
            _log.debug("Используем кэшированные данные для %s", sym)
            return cached

    ysym = _symbol_for_yahoo(sym)
    info: dict[str, Any] = {}

    # Для РФ-тикеров при доступном SDK сразу идём в T-Bank: это быстрее и без шума Yahoo.
    # Минимум 30 свечей нужно для RSI/MACD/ADX. Если T-Bank отдаёт мало — fallback на MOEX ISS.
    _MIN_CANDLES = 30
    if sym.endswith(".ME") and _tbank_available():
        tb_hist, tb_name, tb_currency = _try_tbank_history(sym)
        if tb_hist is not None and len(tb_hist) >= _MIN_CANDLES:
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
            _cache_set(sym, result)
            return result

    info: dict[str, Any] = {}
    hist = pd.DataFrame()
    try:
        info, hist = _fetch_yf_data(ysym, "1y")
    except CircuitOpenError:
        _log.warning("Yahoo Finance circuit breaker OPEN for %s — skipping YF.", sym)
        from .admin_alerts import notify_admin
        notify_admin(
            f"Yahoo Finance circuit breaker OPEN for {sym}. Data source degraded.",
            alert_type="yf_circuit_open",
        )

    profile = classify_instrument(sym, info)
    period = history_period_for_profile(profile)
    if hist.empty or period != "1y":
        try:
            info, hist = _fetch_yf_data(ysym, period)
        except CircuitOpenError:
            _log.warning("Yahoo Finance circuit breaker OPEN for %s (period=%s) — skipping YF.", sym, period)
            info, hist = {}, pd.DataFrame()

    # Fallback на T-Bank API для РФ-тикеров
    if (hist is None or hist.empty or len(hist) < _MIN_CANDLES) and sym.endswith(".ME"):
        _log.info("Yahoo Finance не отдал данные по %s, пробуем T-Bank API…", sym)
        tb_hist, tb_name, tb_currency = _try_tbank_history(sym)
        if tb_hist is not None and len(tb_hist) >= _MIN_CANDLES:
            last = float(tb_hist["Close"].iloc[-1])
            snap = TickerSnapshot(
                symbol=sym,
                last_close=last,
                currency=tb_currency or "RUB",
                company_name=tb_name or sym,
                history=tb_hist,
            )
            result = (snap, info, profile)
            _cache_set(sym, result)
            return result

    # Fallback на MOEX ISS (бесплатно, без токена) для РФ-тикеров
    if (hist is None or hist.empty or len(hist) < _MIN_CANDLES) and sym.endswith(".ME"):
        _log.info("T-Bank недоступен для %s, пробуем MOEX ISS…", sym)
        try:
            from .moex_iss import fetch_moex_history
            moex_hist = fetch_moex_history(sym, days=400)
            if moex_hist is not None and not moex_hist.empty:
                last = float(moex_hist["Close"].iloc[-1])
                # Попробовать получить имя из T-Bank (быстрый запрос)
                company = _resolve_ru_company_name(sym)
                snap = TickerSnapshot(
                    symbol=sym,
                    last_close=last,
                    currency="RUB",
                    company_name=company,
                    history=moex_hist,
                )
                result = (snap, info, profile)
                _cache_set(sym, result)
                _log.info("MOEX ISS: загружено %d свечей для %s", len(moex_hist), sym)
                return result
        except Exception as e:
            _log.warning("MOEX ISS fallback failed for %s: %s", sym, e)

    # Fallback на Yahoo Finance без суффикса .ME для РФ-тикеров (TCSG и др.)
    if (hist is None or hist.empty or len(hist) < _MIN_CANDLES) and sym.endswith(".ME"):
        plain_sym = sym.replace(".ME", "")
        _log.info("MOEX ISS недоступен для %s, пробуем Yahoo Finance (%s)…", sym, plain_sym)
        try:
            info_yf, hist_yf = _fetch_yf_data(plain_sym, period)
            if hist_yf is not None and not hist_yf.empty and len(hist_yf) >= _MIN_CANDLES:
                last = float(hist_yf["Close"].iloc[-1])
                currency = str(info_yf.get("currency") or "RUB")
                name = str(info_yf.get("longName") or info_yf.get("shortName") or sym)
                snap = TickerSnapshot(
                    symbol=sym,
                    last_close=last,
                    currency=currency,
                    company_name=name,
                    history=hist_yf,
                )
                result = (snap, info_yf, profile)
                _cache_set(sym, result)
                _log.info("Yahoo Finance (plain): загружено %d свечей для %s", len(hist_yf), sym)
                return result
        except CircuitOpenError:
            _log.warning("Yahoo Finance circuit breaker OPEN for %s — skipping plain fallback.", plain_sym)
        except Exception as e:
            _log.warning("Yahoo Finance plain fallback failed for %s: %s", sym, e)

    # Fallback на Polygon.io для US-тикеров
    if (hist is None or hist.empty or len(hist) < _MIN_CANDLES) and not sym.endswith(".ME"):
        if _polygon_available():
            _log.info("Yahoo Finance не отдал данные по %s, пробуем Polygon.io…", sym)
            pg_hist, pg_name, pg_currency = _try_polygon_history(sym)
            if pg_hist is not None and len(pg_hist) >= _MIN_CANDLES:
                last = float(pg_hist["Close"].iloc[-1])
                snap = TickerSnapshot(
                    symbol=sym,
                    last_close=last,
                    currency=pg_currency or "USD",
                    company_name=pg_name or sym,
                    history=pg_hist,
                )
                result = (snap, info, profile)
                _cache_set(sym, result)
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
    _cache_set(sym, result)
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

    info: dict[str, Any] = {}
    hist = pd.DataFrame()
    try:
        info, hist = _fetch_yf_data(ysym, period)
    except CircuitOpenError:
        _log.warning("Yahoo Finance circuit breaker OPEN for %s — skipping YF.", sym)

    if hist is None or hist.empty:
        raise ValueError(
            f"Нет данных по тикеру {sym}. Проверьте биржу/суффикс (например SBER.ME для Мосбиржи)."
        )
    last = float(hist["Close"].iloc[-1])
    currency = str(info.get("currency") or "USD")
    name = str(info.get("longName") or info.get("shortName") or sym)
    return TickerSnapshot(
        symbol=sym,
        last_close=last,
        currency=currency,
        company_name=name,
        history=hist,
    )
