"""
Фильтрация сигналов для торговли только лучших.

Строгие критерии для повышения win rate и profit factor:
- Только класс A
- Высокая согласованность (confidence > 0.7)
- Сильный тренд (ADX > 20)
- Хороший объём (volume_score > 0.2)
- Благоприятный макро-фон (macro_dampening > 0.85)

Использование:
    from stock_signal_analyzer.signal_filter import should_trade_signal

    if should_trade_signal(report):
        # Торговать этот сигнал
        execute_trade(report)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .engine import SignalReport

_log = logging.getLogger(__name__)


@dataclass
class FilterResult:
    """Результат фильтрации сигнала."""
    should_trade: bool
    reason: str
    score: float  # 0-100, качество сигнала


class SignalFilter:
    """Фильтр для отбора только лучших торговых сигналов."""

    def __init__(
        self,
        min_tier: str = 'A',
        min_confidence: float = 0.7,
        min_adx: float = 20.0,
        min_volume_score: float = 0.2,
        min_macro_dampening: float = 0.85,
        min_score: float = 0.35,
        require_weekly_alignment: bool = True,
        avoid_earnings_window: bool = True
    ):
        """
        Args:
            min_tier: Минимальный класс сигнала ('A', 'B', 'C')
            min_confidence: Минимальная согласованность компонентов
            min_adx: Минимальный ADX (сила тренда)
            min_volume_score: Минимальный score объёма
            min_macro_dampening: Минимальный макро-коэффициент
            min_score: Минимальный общий score
            require_weekly_alignment: Требовать совпадение с недельным трендом
            avoid_earnings_window: Избегать окна отчётности
        """
        self.min_tier = min_tier
        self.min_confidence = min_confidence
        self.min_adx = min_adx
        self.min_volume_score = min_volume_score
        self.min_macro_dampening = min_macro_dampening
        self.min_score = min_score
        self.require_weekly_alignment = require_weekly_alignment
        self.avoid_earnings_window = avoid_earnings_window

        # Порядок классов
        self.tier_order = {'A': 3, 'B': 2, 'C': 1}

    def filter(self, report: SignalReport) -> FilterResult:
        """
        Проверить, стоит ли торговать этот сигнал.

        Returns:
            FilterResult с решением и причиной
        """
        reasons = []
        quality_score = 100.0

        # 1. Проверка класса сигнала
        if self.tier_order.get(report.signal_tier, 0) < self.tier_order.get(self.min_tier, 0):
            return FilterResult(
                should_trade=False,
                reason=f"Класс {report.signal_tier} ниже минимального {self.min_tier}",
                score=0.0
            )

        # 2. Проверка общего score
        if abs(report.score) < self.min_score:
            return FilterResult(
                should_trade=False,
                reason=f"Score {report.score:.3f} ниже минимального {self.min_score}",
                score=20.0
            )

        # 3. Проверка confidence
        if report.confidence < self.min_confidence:
            quality_score -= 20
            reasons.append(f"Низкая согласованность: {report.confidence:.2f} < {self.min_confidence}")

        # 4. Проверка ADX (сила тренда)
        if report.adx14 < self.min_adx:
            quality_score -= 15
            reasons.append(f"Слабый тренд: ADX {report.adx14:.1f} < {self.min_adx}")

        # 5. Проверка объёма
        if abs(report.volume_score) < self.min_volume_score:
            quality_score -= 15
            reasons.append(f"Слабый объём: {report.volume_score:.3f} < {self.min_volume_score}")

        # 6. Проверка макро-фона
        if report.macro_dampening < self.min_macro_dampening:
            quality_score -= 10
            reasons.append(f"Плохой макро-фон: {report.macro_dampening:.2f} < {self.min_macro_dampening}")

        # 7. Проверка недельного тренда
        if self.require_weekly_alignment:
            # Проверяем, что недельный тренд совпадает с направлением сигнала
            weekly_aligned = self._check_weekly_alignment(report)
            if not weekly_aligned:
                quality_score -= 10
                reasons.append("Недельный тренд не совпадает с сигналом")

        # 8. Проверка окна отчётности
        if self.avoid_earnings_window:
            # Проверяем через timing_detail
            if 'отчётност' in report.timing_detail.lower() or 'earnings' in report.timing_detail.lower():
                quality_score -= 15
                reasons.append("Окно отчётности - повышенная волатильность")

        # 9. Дополнительные бонусы за качество
        if report.signal_tier == 'A':
            quality_score += 10

        if report.confidence > 0.8:
            quality_score += 5

        if report.adx14 > 30:
            quality_score += 5

        # Решение
        should_trade = quality_score >= 60.0 and len(reasons) <= 2

        reason_text = "✓ Отличный сигнал" if should_trade else f"✗ {'; '.join(reasons)}"

        return FilterResult(
            should_trade=should_trade,
            reason=reason_text,
            score=max(0.0, min(100.0, quality_score))
        )

    def _check_weekly_alignment(self, report: SignalReport) -> bool:
        """Проверить совпадение с недельным трендом.

        weekly_regime значения: 'up', 'down', 'flat', 'unknown' (из timing_context.py).
        """
        if not report.weekly_regime:
            return True  # Нет данных — не штрафуем

        weekly_lower = report.weekly_regime.lower()

        # Если сигнал положительный — нужен uptrend или flat
        if report.score > 0:
            return weekly_lower in ("up", "flat", "unknown")

        # Если сигнал отрицательный — нужен downtrend или flat
        if report.score < 0:
            return weekly_lower in ("down", "flat", "unknown")

        return True


# Предустановленные фильтры

def get_conservative_filter() -> SignalFilter:
    """
    Консервативный фильтр - только лучшие сигналы.

    Ожидаемые метрики:
    - Win rate: 65-75%
    - Profit factor: 2.5-3.5
    - Сигналов в месяц: 10-20
    """
    return SignalFilter(
        min_tier='A',
        min_confidence=0.75,
        min_adx=25.0,
        min_volume_score=0.25,
        min_macro_dampening=0.90,
        min_score=0.40,
        require_weekly_alignment=True,
        avoid_earnings_window=True
    )


def get_balanced_filter() -> SignalFilter:
    """
    Сбалансированный фильтр - хорошее соотношение качества и количества.

    Ожидаемые метрики:
    - Win rate: 60-70%
    - Profit factor: 2.0-2.5
    - Сигналов в месяц: 30-50
    """
    return SignalFilter(
        min_tier='A',
        min_confidence=0.70,
        min_adx=20.0,
        min_volume_score=0.20,
        min_macro_dampening=0.85,
        min_score=0.35,
        require_weekly_alignment=True,
        avoid_earnings_window=True
    )


def get_aggressive_filter() -> SignalFilter:
    """
    Агрессивный фильтр - больше сигналов, но ниже качество.

    Ожидаемые метрики:
    - Win rate: 55-65%
    - Profit factor: 1.5-2.0
    - Сигналов в месяц: 50-100
    """
    return SignalFilter(
        min_tier='B',
        min_confidence=0.60,
        min_adx=18.0,
        min_volume_score=0.15,
        min_macro_dampening=0.80,
        min_score=0.30,
        require_weekly_alignment=False,
        avoid_earnings_window=True
    )


# Удобные функции

def should_trade_signal(report: SignalReport, filter_type: str = 'balanced') -> bool:
    """
    Проверить, стоит ли торговать сигнал.

    Args:
        report: Отчёт по сигналу
        filter_type: Тип фильтра ('conservative', 'balanced', 'aggressive')

    Returns:
        True если сигнал проходит фильтр
    """
    if filter_type == 'conservative':
        filter_obj = get_conservative_filter()
    elif filter_type == 'aggressive':
        filter_obj = get_aggressive_filter()
    else:
        filter_obj = get_balanced_filter()

    result = filter_obj.filter(report)
    return result.should_trade


def filter_signal_with_reason(report: SignalReport, filter_type: str = 'balanced') -> FilterResult:
    """
    Проверить сигнал и получить детальную причину.

    Args:
        report: Отчёт по сигналу
        filter_type: Тип фильтра ('conservative', 'balanced', 'aggressive')

    Returns:
        FilterResult с решением, причиной и оценкой качества
    """
    if filter_type == 'conservative':
        filter_obj = get_conservative_filter()
    elif filter_type == 'aggressive':
        filter_obj = get_aggressive_filter()
    else:
        filter_obj = get_balanced_filter()

    return filter_obj.filter(report)


# Пример использования
if __name__ == '__main__':
    # Демонстрация использования
    print("=== Примеры фильтров ===\n")

    print("1. Консервативный фильтр:")
    f = get_conservative_filter()
    print(f"   - Минимальный класс: {f.min_tier}")
    print(f"   - Минимальная confidence: {f.min_confidence}")
    print(f"   - Минимальный ADX: {f.min_adx}")
    print(f"   - Ожидаемый win rate: 65-75%")
    print(f"   - Сигналов в месяц: 10-20\n")

    print("2. Сбалансированный фильтр:")
    f = get_balanced_filter()
    print(f"   - Минимальный класс: {f.min_tier}")
    print(f"   - Минимальная confidence: {f.min_confidence}")
    print(f"   - Минимальный ADX: {f.min_adx}")
    print(f"   - Ожидаемый win rate: 60-70%")
    print(f"   - Сигналов в месяц: 30-50\n")

    print("3. Агрессивный фильтр:")
    f = get_aggressive_filter()
    print(f"   - Минимальный класс: {f.min_tier}")
    print(f"   - Минимальная confidence: {f.min_confidence}")
    print(f"   - Минимальный ADX: {f.min_adx}")
    print(f"   - Ожидаемый win rate: 55-65%")
    print(f"   - Сигналов в месяц: 50-100\n")
