"""Интеграция с Tinkoff Invest API (T-Bank).

Получение данных по российским акциям через официальный SDK.
Поддерживает оба пакета: t-tech-investments (новый) и tinkoff-investments (старый).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

_log = logging.getLogger(__name__)

# Пробуем оба SDK
_Client = None
_CandleInterval = None
_TINKOFF_AVAILABLE = False
_SDK_SOURCE = ""

try:
    from t_tech.invest import Client as _C, CandleInterval as _CI
    _Client = _C
    _CandleInterval = _CI
    _TINKOFF_AVAILABLE = True
    _SDK_SOURCE = "t_tech.invest"
except ImportError:
    try:
        from tinkoff.invest import Client as _C, CandleInterval as _CI
        _Client = _C
        _CandleInterval = _CI
        _TINKOFF_AVAILABLE = True
        _SDK_SOURCE = "tinkoff.invest"
    except ImportError:
        pass

# Для обратной совместимости
TINKOFF_AVAILABLE = _TINKOFF_AVAILABLE


def _quotation_to_float(q: Any) -> float:
    """Конвертировать Quotation в float."""
    if q is None:
        return 0.0
    # Пробуем утилиту SDK
    try:
        if _SDK_SOURCE == "t_tech.invest":
            from t_tech.invest.utils import quotation_to_decimal
            return float(quotation_to_decimal(q))
        elif _SDK_SOURCE == "tinkoff.invest":
            from tinkoff.invest.utils import quotation_to_decimal
            return float(quotation_to_decimal(q))
    except (ImportError, Exception):
        pass
    # Fallback
    if hasattr(q, 'units') and hasattr(q, 'nano'):
        return float(q.units) + float(q.nano) / 1e9
    return float(q)


def get_tinkoff_token() -> str | None:
    """Получить токен Tinkoff из переменных окружения."""
    return (
        os.environ.get("TINKOFF_INVEST_TOKEN")
        or os.environ.get("TINKOFF_TOKEN")
        or os.environ.get("TBANK_TOKEN")
    )


def is_tinkoff_available() -> bool:
    """Проверить, доступен ли Tinkoff API."""
    if not _TINKOFF_AVAILABLE:
        return False
    return get_tinkoff_token() is not None


def fetch_tinkoff_price(ticker: str) -> dict[str, Any] | None:
    """Получить текущую цену акции через Tinkoff/T-Bank API."""
    if not _TINKOFF_AVAILABLE or _Client is None:
        return None

    token = get_tinkoff_token()
    if not token:
        return None

    try:
        with _Client(token) as client:
            instruments = client.instruments.find_instrument(query=ticker)
            if not instruments.instruments:
                return None

            # Фильтруем по акциям (share) и предпочтительно TQBR
            shares = [
                i for i in instruments.instruments
                if getattr(i, "instrument_type", "") == "share"
            ]
            if not shares:
                # Fallback для индексов (например IMOEX.ME)
                shares = [
                    i for i in instruments.instruments
                    if getattr(i, "instrument_type", "") == "index"
                ]
            if not shares:
                return None
            instrument = next(
                (i for i in shares if getattr(i, "class_code", "") == "TQBR"),
                shares[0]
            )
            uid = getattr(instrument, "uid", None) or ""
            figi = getattr(instrument, "figi", "") or ""

            if uid:
                last_prices = client.market_data.get_last_prices(instrument_id=[uid])
            elif figi:
                last_prices = client.market_data.get_last_prices(figi=[figi])
            else:
                return None

            if not last_prices.last_prices:
                return None

            price = _quotation_to_float(last_prices.last_prices[0].price)

            volume = 0
            if _CandleInterval is not None:
                try:
                    kwargs = {
                        "from_": datetime.now(timezone.utc) - timedelta(days=1),
                        "to": datetime.now(timezone.utc),
                        "interval": _CandleInterval.CANDLE_INTERVAL_DAY,
                    }
                    if uid:
                        kwargs["instrument_id"] = uid
                    else:
                        kwargs["figi"] = figi
                    candles = client.market_data.get_candles(**kwargs)
                    if candles.candles:
                        volume = candles.candles[-1].volume
                except Exception:
                    pass

            return {
                "price": price,
                "currency": getattr(instrument, "currency", "rub"),
                "volume": volume,
                "figi": figi,
                "name": getattr(instrument, "name", ticker),
                "ticker": getattr(instrument, "ticker", ticker),
            }

    except Exception as e:
        _log.warning("Tinkoff API error for %s: %s", ticker, type(e).__name__)
        return None


def fetch_tinkoff_candles(ticker: str, days: int = 30) -> list[dict[str, Any]] | None:
    """Получить исторические свечи через Tinkoff/T-Bank API."""
    if not _TINKOFF_AVAILABLE or _Client is None or _CandleInterval is None:
        return None

    token = get_tinkoff_token()
    if not token:
        return None

    try:
        with _Client(token) as client:
            instruments = client.instruments.find_instrument(query=ticker)
            if not instruments.instruments:
                return None

            # Фильтруем по акциям (share) и предпочтительно TQBR
            shares = [
                i for i in instruments.instruments
                if getattr(i, "instrument_type", "") == "share"
            ]
            if not shares:
                # Fallback для индексов (например IMOEX.ME)
                shares = [
                    i for i in instruments.instruments
                    if getattr(i, "instrument_type", "") == "index"
                ]
            if not shares:
                return None
            instrument = next(
                (i for i in shares if getattr(i, "class_code", "") == "TQBR"),
                shares[0]
            )
            uid = getattr(instrument, "uid", None) or ""
            figi = getattr(instrument, "figi", "") or ""

            kwargs = {
                "from_": datetime.now(timezone.utc) - timedelta(days=days),
                "to": datetime.now(timezone.utc),
                "interval": _CandleInterval.CANDLE_INTERVAL_DAY,
            }
            if uid:
                kwargs["instrument_id"] = uid
            else:
                kwargs["figi"] = figi

            candles = client.market_data.get_candles(**kwargs)

            result = []
            for candle in candles.candles:
                result.append({
                    "date": candle.time,
                    "open": _quotation_to_float(candle.open),
                    "high": _quotation_to_float(candle.high),
                    "low": _quotation_to_float(candle.low),
                    "close": _quotation_to_float(candle.close),
                    "volume": candle.volume,
                })

            return result

    except Exception as e:
        _log.warning("Tinkoff candles error for %s: %s", ticker, type(e).__name__)
        return None


def get_tinkoff_portfolio() -> dict[str, Any] | None:
    """Получить портфель пользователя."""
    if not _TINKOFF_AVAILABLE or _Client is None:
        return None

    token = get_tinkoff_token()
    if not token:
        return None

    try:
        with _Client(token) as client:
            accounts = client.users.get_accounts()
            if not accounts.accounts:
                return None

            account_id = accounts.accounts[0].id
            portfolio = client.operations.get_portfolio(account_id=account_id)

            positions = []
            for pos in portfolio.positions:
                positions.append({
                    "figi": pos.figi,
                    "quantity": _quotation_to_float(pos.quantity),
                    "average_price": _quotation_to_float(pos.average_position_price),
                    "current_price": _quotation_to_float(pos.current_price),
                })

            return {
                "positions": positions,
                "total_value": _quotation_to_float(portfolio.total_amount_portfolio),
            }

    except Exception as e:
        _log.warning("Tinkoff portfolio error: %s", type(e).__name__)
        return None
