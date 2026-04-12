"""Сегменты рынка: РФ-голубые, иностранные голубые, дивидендные бумаги."""

from __future__ import annotations

from .universe import RU_BLUE_CHIPS, US_BLUE_CHIPS

# Дивидендные (типичные ликвидные эмитенты; список можно расширять).
RU_DIVIDEND = frozenset(
    {
        "SBER.ME",
        "GAZP.ME",
        "LKOH.ME",
        "GMKN.ME",
        "NVTK.ME",
        "ROSN.ME",
        "TATN.ME",
        "MOEX.ME",
        "MGNT.ME",
        "MTSS.ME",
        "AFLT.ME",
        "CHMF.ME",
        "NLMK.ME",
        "PLZL.ME",
        "PHOR.ME",
        "IRAO.ME",
    }
)

US_DIVIDEND = frozenset(
    {
        "T",
        "VZ",
        "JNJ",
        "KO",
        "PG",
        "PEP",
        "XOM",
        "CVX",
        "MRK",
        "ABBV",
        "PFE",
        "BMY",
        "WFC",
        "CSCO",
        "IBM",
        "PM",
        "MO",
        "O",
        "SPG",
        "STAG",
    }
)

DIVIDEND_UNIVERSE = RU_DIVIDEND | US_DIVIDEND


def _base(sym: str) -> str:
    s = sym.strip().upper()
    return s[:-3] if s.endswith(".ME") else s


def tags_for_symbol(sym: str) -> set[str]:
    """Какие роли у тикера (может быть несколько)."""
    s = sym.strip().upper()
    base = _base(s)
    tags: set[str] = set()
    if s.endswith(".ME") and base in RU_BLUE_CHIPS:
        tags.add("ru_blue")
    if (not s.endswith(".ME")) and (s in US_BLUE_CHIPS or base in US_BLUE_CHIPS):
        tags.add("foreign_blue")
    if s in DIVIDEND_UNIVERSE:
        tags.add("dividend")
    if not tags:
        tags.add("other")
    return tags


def primary_bucket(sym: str) -> str:
    """Один раздел для группировки: дивиденд → РФ → иностр. → прочее."""
    t = tags_for_symbol(sym)
    if "dividend" in t:
        return "dividend"
    if "ru_blue" in t:
        return "ru_blue"
    if "foreign_blue" in t:
        return "foreign_blue"
    return "other"


SECTION_TITLES = {
    "ru_blue": "🇷🇺 Голубые фишки (Россия)",
    "foreign_blue": "🌐 Голубые фишки (иностранные)",
    "dividend": "💰 Дивидендные акции",
    "other": "📎 Прочие",
}


def format_tags_ru(sym: str) -> str:
    """Человекочитаемые метки сегментов (бумага может быть в нескольких)."""
    tags = tags_for_symbol(sym)
    parts: list[str] = []
    if "ru_blue" in tags:
        parts.append("РФ голубая фишка")
    if "foreign_blue" in tags:
        parts.append("иностранная голубая фишка")
    if "dividend" in tags:
        parts.append("дивидендная")
    if "other" in tags and len(tags) == 1:
        parts.append("прочее")
    return ", ".join(parts) if parts else "прочее"


def full_scan_universe(max_total: int = 120) -> list[str]:
    """Пул для поиска сильных сигналов вне списка пользователя (ограничен по размеру)."""
    ru = [f"{x}.ME" for x in sorted(RU_BLUE_CHIPS)]
    us = sorted(US_BLUE_CHIPS)
    div = sorted(DIVIDEND_UNIVERSE)
    merged: list[str] = []
    seen: set[str] = set()
    for block in (div, ru, us):
        for x in block:
            if x not in seen:
                seen.add(x)
                merged.append(x)
            if len(merged) >= max_total:
                return merged
    return merged
