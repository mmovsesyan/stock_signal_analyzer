"""
Ключевые уровни: поддержка и сопротивление.

Методы:
- Pivot Points (classic floor pivots)
- Swing highs/lows за последние N свечей
- Кластеризация горизонтальных уровней
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class KeyLevels:
    nearest_support: float | None
    nearest_resistance: float | None
    pivot: float
    s1: float
    s2: float
    r1: float
    r2: float
    detail: str


def _classic_pivots(high: float, low: float, close: float) -> tuple[float, float, float, float, float]:
    """Classic floor trader pivots: P, S1, S2, R1, R2."""
    p = (high + low + close) / 3.0
    s1 = 2.0 * p - high
    s2 = p - (high - low)
    r1 = 2.0 * p - low
    r2 = p + (high - low)
    return p, s1, s2, r1, r2


def _swing_levels(
    high: pd.Series, low: pd.Series, window: int = 5, lookback: int = 40,
) -> tuple[list[float], list[float]]:
    """Swing highs и lows: точки, где high/low — локальный экстремум."""
    h = high.astype(float).iloc[-lookback:]
    l = low.astype(float).iloc[-lookback:]
    swing_highs: list[float] = []
    swing_lows: list[float] = []

    for i in range(window, len(h) - window):
        segment_h = h.iloc[i - window : i + window + 1]
        if float(h.iloc[i]) == float(segment_h.max()):
            swing_highs.append(float(h.iloc[i]))
        segment_l = l.iloc[i - window : i + window + 1]
        if float(l.iloc[i]) == float(segment_l.min()):
            swing_lows.append(float(l.iloc[i]))

    return swing_highs, swing_lows


def _cluster_levels(values: list[float], tolerance_pct: float = 0.5) -> list[float]:
    """Группировка близких уровней (внутри tolerance_pct друг от друга)."""
    if not values:
        return []
    sorted_vals = sorted(values)
    clusters: list[list[float]] = [[sorted_vals[0]]]
    for v in sorted_vals[1:]:
        if abs(v - clusters[-1][-1]) / max(abs(clusters[-1][-1]), 1e-9) < tolerance_pct / 100.0:
            clusters[-1].append(v)
        else:
            clusters.append([v])
    return [sum(c) / len(c) for c in clusters]


def compute_key_levels(hist: pd.DataFrame) -> KeyLevels:
    """Вычисляет ключевые уровни поддержки/сопротивления."""
    need = {"High", "Low", "Close"}
    if not need.issubset(hist.columns) or len(hist) < 10:
        return KeyLevels(
            nearest_support=None, nearest_resistance=None,
            pivot=0, s1=0, s2=0, r1=0, r2=0,
            detail="Недостаточно данных для уровней.",
        )

    h = hist["High"].astype(float)
    l = hist["Low"].astype(float)
    c = hist["Close"].astype(float)
    last_price = float(c.iloc[-1])

    prev_h = float(h.iloc[-2]) if len(h) >= 2 else float(h.iloc[-1])
    prev_l = float(l.iloc[-2]) if len(l) >= 2 else float(l.iloc[-1])
    prev_c = float(c.iloc[-2]) if len(c) >= 2 else float(c.iloc[-1])

    pivot, s1, s2, r1, r2 = _classic_pivots(prev_h, prev_l, prev_c)

    swing_h, swing_l = _swing_levels(h, l)
    all_supports = _cluster_levels([s1, s2] + swing_l)
    all_resistances = _cluster_levels([r1, r2] + swing_h)

    supports_below = [s for s in all_supports if s < last_price * 0.998]
    resistances_above = [r for r in all_resistances if r > last_price * 1.002]

    nearest_support = max(supports_below) if supports_below else s1
    nearest_resistance = min(resistances_above) if resistances_above else r1

    sup_dist = (nearest_support / last_price - 1.0) * 100.0 if nearest_support else 0
    res_dist = (nearest_resistance / last_price - 1.0) * 100.0 if nearest_resistance else 0

    detail = (
        f"Pivot={pivot:.2f}, "
        f"поддержка: {nearest_support:.2f} ({sup_dist:+.1f}%), "
        f"сопротивление: {nearest_resistance:.2f} ({res_dist:+.1f}%)"
    )

    return KeyLevels(
        nearest_support=nearest_support,
        nearest_resistance=nearest_resistance,
        pivot=pivot,
        s1=s1, s2=s2, r1=r1, r2=r2,
        detail=detail,
    )
