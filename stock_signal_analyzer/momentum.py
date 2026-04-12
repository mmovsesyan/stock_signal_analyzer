"""
Краткосрочный импульс: доходность за 5/10/20 дней, ускорение ROC,
фильтр перерастяжения (mean reversion), согласованность с трендом.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class MomentumScore:
    score: float
    ret_5d: float
    ret_10d: float
    ret_20d: float
    acceleration: float
    details: str


def _roc(close: pd.Series, period: int) -> float:
    if len(close) < period + 1:
        return 0.0
    return float(close.iloc[-1] / close.iloc[-period - 1] - 1.0)


def _roc_acceleration(close: pd.Series) -> float:
    """
    Ускорение: ROC(5) текущий vs ROC(5) 5 дней назад.
    Положительное = импульс нарастает, отрицательное = затухает.
    """
    if len(close) < 12:
        return 0.0
    roc_now = float(close.iloc[-1] / close.iloc[-6] - 1.0)
    roc_prev = float(close.iloc[-6] / close.iloc[-11] - 1.0)
    return roc_now - roc_prev


def analyze_momentum(
    close: pd.Series,
    atr_pct: float | None = None,
    adx14: float = 25.0,
) -> MomentumScore:
    close = close.astype(float).dropna()
    if len(close) < 22:
        return MomentumScore(
            score=0.0, ret_5d=0.0, ret_10d=0.0, ret_20d=0.0,
            acceleration=0.0, details="Мало данных для импульса.",
        )

    r5 = _roc(close, 5)
    r10 = _roc(close, 10)
    r20 = _roc(close, 20)
    accel = _roc_acceleration(close)

    s5 = float(np.tanh(r5 * 15))
    s10 = float(np.tanh(r10 * 10))
    s20 = float(np.tanh(r20 * 8))
    s_accel = float(np.tanh(accel * 20))

    score = float(np.clip(0.35 * s5 + 0.25 * s10 + 0.25 * s20 + 0.15 * s_accel, -1.0, 1.0))

    # Mean-reversion filter
    overextended = False
    if atr_pct is not None and atr_pct > 0:
        threshold = 2.0 * atr_pct / 100.0
        if abs(r5) > threshold and adx14 < 25.0:
            score *= 0.3
            overextended = True

    # Trend alignment: если 5d и 20d в разных направлениях → ослабить
    if r5 * r20 < 0 and abs(r5) > 0.01 and abs(r20) > 0.01:
        score *= 0.7

    parts = [f"5д: {r5*100:.2f}%", f"10д: {r10*100:.2f}%", f"20д: {r20*100:.2f}%"]
    if abs(accel) > 0.005:
        parts.append(f"ускорение: {accel*100:+.2f}%")
    detail = "Доходность " + ", ".join(parts)
    if overextended:
        detail += f" (перерастяжение: |ret5d|>{2*atr_pct:.1f}%, ADX<25 → демпфирование)"
    if r5 * r20 < 0 and abs(r5) > 0.01:
        detail += " (конфликт 5д/20д → ослабление)"

    return MomentumScore(
        score=float(np.clip(score, -1.0, 1.0)),
        ret_5d=r5,
        ret_10d=r10,
        ret_20d=r20,
        acceleration=accel,
        details=detail,
    )
