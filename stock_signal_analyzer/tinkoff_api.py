"""Интеграция с Tinkoff Invest API (T-Bank).

Получение данных по российским акциям через официальный SDK.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any

try:
    from tinkoff.invest import Client, RequestError, CandleInterval
    from tinkoff.invest.schemas import Quotation
    TINKOFF_AVAILABLE = True
except ImportError:
    TINKOFF_AVAILABLE = False


def _quotation_to_float(q: Any) -> float:
    """Конвертировать Quotation в float."""
    if hasattr(q, 'units') and hasattr(q, 'nano'):
        return float(q.units) + float(q.nano) / 1e9
    return float(q)


def get_tinkoff_token() -> str | None:
    """Получить токен Tinkoff из переменных окружения."""
    return os.environ.get("TINKOFF_TOKEN") or os.environ.get("TBANK_TOKEN")


def fetch_tinkoff_price(ticker: str) -> dict[str, Any] | None:
    """
    Получить текущую цену акции через Tinkoff API.

    Args:
        ticker: Тикер акции (например, "SBER", "GAZP")

    Returns:
        dict с полями: price, currency, volume, или None если ошибка
    """
    if not TINKOFF_AVAILABLE:
        return None

    token = get_tinkoff_token()
    if not token:
        return None

    try:
        with Client(token) as client:
            # Поиск инструмента по тикеру
            instruments = client.instruments.find_instrument(query=ticker)
            if not instruments.instruments:
                return None

            instrument = instruments.instruments[0]
            figi = instrument.figi

            # Получить последнюю цену
            last_prices = client.market_data.get_last_prices(figi=[figi])
            if not last_prices.last_prices:
                return None

            last_price = last_prices.last_prices[0]
            price = _quotation_to_float(last_price.price)

            # Получить объём торгов
            candles = client.market_data.get_candles(
                figi=figi,
                from_=datetime.now() - timedelta(days=1),
                to=datetime.now(),
                interval=CandleInterval.CANDLE_INTERVAL_DAY
            )

            volume = 0
            if candles.candles:
                volume = candles.candles[-1].volume

            return {
                "price": price,
                "currency": instrument.currency,
                "volume": volume,
                "figi": figi,
                "name": instrument.name,
                "ticker": instrument.ticker,
            }

    except RequestError as e:
        print(f"Tinkoff API error for {ticker}: {e}")
        return None
    except Exception as e:
        print(f"Tinkoff error for {ticker}: {e}")
        return None


def fetch_tinkoff_candles(ticker: str, days: int = 30) -> list[dict[str, Any]] | None:
    """
    Получить исторические свечи через Tinkoff API.

    Args:
        ticker: Тикер акции
        days: Количество дней истории

    Returns:
        Список свечей с полями: date, open, high, low, close, volume
    """
    if not TINKOFF_AVAILABLE:
        return None

    token = get_tinkoff_token()
    if not token:
        return None

    try:
        with Client(token) as client:
            # Поиск инструмента
            instruments = client.instruments.find_instrument(query=ticker)
            if not instruments.instruments:
                return None

            figi = instruments.instruments[0].figi

            # Получить свечи
            candles = client.market_data.get_candles(
                figi=figi,
                from_=datetime.now() - timedelta(days=days),
                to=datetime.now(),
                interval=CandleInterval.CANDLE_INTERVAL_DAY
            )

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
        print(f"Tinkoff candles error for {ticker}: {e}")
        return None


def is_tinkoff_available() -> bool:
    """Проверить, доступен ли Tinkoff API."""
    if not TINKOFF_AVAILABLE:
        return False
    token = get_tinkoff_token()
    return token is not None


def get_tinkoff_portfolio() -> dict[str, Any] | None:
    """
    Получить портфель пользователя.

    Returns:
        dict с позициями и балансом
    """
    if not TINKOFF_AVAILABLE:
        return None

    token = get_tinkoff_token()
    if not token:
        return None

    try:
        with Client(token) as client:
            # Получить список счетов
            accounts = client.users.get_accounts()
            if not accounts.accounts:
                return None

            account_id = accounts.accounts[0].id

            # Получить портфель
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
        print(f"Tinkoff portfolio error: {e}")
        return None
