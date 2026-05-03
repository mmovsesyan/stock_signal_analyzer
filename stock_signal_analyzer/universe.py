"""Классификация инструментов: голубые фишки / крупные эмитенты / облигации."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# Крупнейшие US (ликвидные «голубые фишки») — топ S&P 500 по капитализации.
US_BLUE_CHIPS = frozenset(
    {
        "AAPL",
        "MSFT",
        "GOOGL",
        "GOOG",
        "AMZN",
        "META",
        "NVDA",
        "TSLA",
        "BRK-B",
        "BRK.B",
        "LLY",
        "JPM",
        "V",
        "UNH",
        "JNJ",
        "XOM",
        "WMT",
        "PG",
        "MA",
        "HD",
        "CVX",
        "MRK",
        "ABBV",
        "PEP",
        "KO",
        "COST",
        "AVGO",
        "TMO",
        "MCD",
        "CSCO",
        "ACN",
        "NFLX",
        "AMD",
        "BAC",
        "MU",
        "GE",
        "ORCL",
        "CRM",
        "IBM",
        "INTC",
        "QCOM",
        "AMAT",
        "HON",
        "LOW",
        "UNP",
        "RTX",
        "SPGI",
        "BLK",
        "GS",
        "MS",
        "SCHW",
        "AXP",
        "CAT",
        "DE",
        "BA",
        "LMT",
        "WFC",
        "NEE",
        "ABT",
        "LIN",
        "DIS",
        "PM",
        "TXN",
        "DHR",
    }
)

# Ликвидные бумаги Мосбиржи (без суффикса .ME).
# Актуально: MOEXBC пересмотр декабрь 2025 — добавлены OZON, VTBR; убраны CHMF, NLMK.
RU_BLUE_CHIPS = frozenset(
    {
        "SBER",
        "GAZP",
        "LKOH",
        "GMKN",
        "NVTK",
        "ROSN",
        "TATN",
        "MOEX",
        "MGNT",
        "YDEX",   # Яндекс (ранее YNDX)
        "PLZL",
        "ALRS",
        "VTBR",
        "SNGS",
        "MTSS",
        "AFLT",
        "PHOR",
        "IRAO",
        "OZON",   # добавлен в MOEXBC дек. 2025
        "TCSG",   # Т-Банк (TCS Group)
        "RUAL",   # Русал
        "MAGN",   # ММК
        "PIKK",   # ПИК
        "POLY",   # Полиметалл
        "FEES",   # ФСК ЕЭС / Россети
    }
)

# Глобальные ETF на облигации (удобные тикеры Yahoo); можно дополнять.
BOND_ETFS_AND_FUNDS = frozenset(
    {
        "TLT",
        "IEF",
        "SHY",
        "AGG",
        "BND",
        "LQD",
        "MUB",
        "HYG",
        "JNK",
        "VCIT",
        "VCSH",
        "BNDX",
        "TIP",
        "SPTL",
        "GOVT",
        "SCHR",
        "SPTS",
        "SPSB",
        "FLOT",
        "EMB",
        "IGSB",
        "SPIB",
    }
)

# Порог капитализации (USD) для «крупной» акции, если есть данные yfinance.
LARGE_CAP_USD = 25_000_000_000


@dataclass(frozen=True)
class InstrumentProfile:
    """Тип инструмента для выбора весов и пояснений."""

    kind: str  # "bond" | "equity"
    is_blue_or_large: bool
    label: str
    market: str  # "US" | "RU" | "OTHER"


def _base_symbol(sym: str) -> str:
    s = sym.strip().upper()
    if s.endswith(".ME"):
        return s[:-3]
    return s


def classify_instrument(symbol: str, info: dict[str, Any] | None = None) -> InstrumentProfile:
    sym = symbol.strip().upper()
    base = _base_symbol(sym)
    info = info or {}
    qt = str(info.get("quoteType") or "").upper()
    long_name = str(info.get("longName") or info.get("shortName") or "").lower()

    cap = info.get("marketCap")
    try:
        cap_f = float(cap) if cap is not None else None
    except (TypeError, ValueError):
        cap_f = None

    is_ru_ofz_style = bool(
        re.match(r"^SU\d{2}[A-Z0-9]+\.ME$", sym)
        or re.match(r"^RU[A-Z0-9]{9,}\.ME$", sym)
    )
    if qt == "BOND" or "BOND" in sym or is_ru_ofz_style or "облигац" in long_name:
        return InstrumentProfile(
            kind="bond",
            is_blue_or_large=True,
            label="Облигации / долговой инструмент (тип из Yahoo или тикер OFZ/корп.)",
            market="RU" if sym.endswith(".ME") else "OTHER",
        )

    if base in BOND_ETFS_AND_FUNDS and not sym.endswith(".ME"):
        return InstrumentProfile(
            kind="bond",
            is_blue_or_large=True,
            label="ETF/фонд облигаций (глобальный список)",
            market="US",
        )

    if sym.endswith(".ME"):
        if base in RU_BLUE_CHIPS:
            return InstrumentProfile(
                kind="equity",
                is_blue_or_large=True,
                label="Голубая фишка (Мосбиржа, список)",
                market="RU",
            )
        if cap_f and cap_f >= LARGE_CAP_USD:
            return InstrumentProfile(
                kind="equity",
                is_blue_or_large=True,
                label="Крупная капитализация (≥ порога)",
                market="RU",
            )
        return InstrumentProfile(
            kind="equity",
            is_blue_or_large=False,
            label="Акция MOEX (вне списка голубых фишек)",
            market="RU",
        )

    if sym in US_BLUE_CHIPS or base in US_BLUE_CHIPS:
        return InstrumentProfile(
            kind="equity",
            is_blue_or_large=True,
            label="Голубая фишка (США, список)",
            market="US",
        )
    if cap_f and cap_f >= LARGE_CAP_USD:
        return InstrumentProfile(
            kind="equity",
            is_blue_or_large=True,
            label="Крупная капитализация (≥ порога)",
            market="US",
        )
    return InstrumentProfile(
        kind="equity",
        is_blue_or_large=False,
        label="Прочая акция",
        market="US" if not sym.endswith(".ME") else "OTHER",
    )


def history_period_for_profile(profile: InstrumentProfile) -> str:
    """Облигации — чуть более длинная история для сглаживания волатильности."""
    return "1y" if profile.kind == "bond" else "6mo"


def select_component_weights(profile: InstrumentProfile, has_intraday: bool) -> tuple[float, float, float, float]:
    """
    Возвращает (w_tech, w_mom, w_news, w_intra) — сумма 1.0 если intraday есть,
    иначе w_intra=0 и первые три нормализуются.
    Акцент на голубых фишках: меньше вес импульса; облигации — ещё меньше импульса, больше техника+новости.
    """
    if profile.kind == "bond":
        if has_intraday:
            return (0.44, 0.06, 0.30, 0.20)
        return (0.50, 0.10, 0.40, 0.0)
    if profile.is_blue_or_large:
        if has_intraday:
            return (0.40, 0.14, 0.31, 0.15)
        return (0.44, 0.16, 0.40, 0.0)
    if has_intraday:
        return (0.36, 0.19, 0.30, 0.15)
    return (0.42, 0.20, 0.38, 0.0)
