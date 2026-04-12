"""
Совместимость: котировки Т-Инвестиции реализованы в ``tbank_invest``.

Историческое имя модуля сохранено для ``intraday`` и старых импортов.
Документация Т-Банка: https://developer.tbank.ru/invest/sdk/python_sdk/faq_python/
"""

from __future__ import annotations

from .tbank_invest import TbankQuote as TinkoffQuote
from .tbank_invest import fetch_last_price_tbank as fetch_last_price
from .tbank_invest import tbank_sdk_available as tinkoff_available

__all__ = ["TinkoffQuote", "fetch_last_price", "tinkoff_available"]
