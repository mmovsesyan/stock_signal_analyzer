"""
Контекст риска: ATR%, классификация качества сигнала (A/B/C).

Режим «A» — узкий фильтр (много условий сразу), чтобы повысить долю «чистых» сигналов
в бэктесте; это не гарантия конкретного win rate на будущем рынке.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def atr_percent_14(hist: pd.DataFrame) -> float | None:
    """ATR(14) как % от последней цены — масштаб волатильности для сравнения тикеров."""
    need = {"High", "Low", "Close"}
    if not need.issubset(hist.columns):
        return None
    d = hist[list(need)].astype(float).dropna()
    if len(d) < 16:
        return None
    h = d["High"]
    l = d["Low"]
    c = d["Close"]
    c_prev = c.shift(1)
    tr = pd.concat([h - l, (h - c_prev).abs(), (l - c_prev).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0 / 14.0, adjust=False).mean()
    last_c = float(c.iloc[-1])
    last_atr = float(atr.iloc[-1])
    if last_c <= 0 or np.isnan(last_atr):
        return None
    return float(last_atr / last_c * 100.0)


def _news_aligned(total: float, news_score: float) -> bool:
    if abs(news_score) < 0.16:
        return True
    if abs(total) < 0.05:
        return True
    return int(np.sign(news_score)) == int(np.sign(total))


def classify_signal_tier(
    total: float,
    confidence: float,
    macro_dampening: float,
    adx14: float,
    news_score: float,
    liq_mult: float,
    vol_align_mult: float,
    has_chart_pattern: bool,
    weekly_aligned: bool = True,
    earnings_window: bool = False,
    index_headwind: bool = False,
) -> tuple[str, str]:
    """
    A — строгий набор условий (мало сигналов, выше ожидаемая точность при валидации на истории).
    B — умеренный. C — наблюдение / слабая согласованность.
    Второй элемент — краткое пояснение (почему не A или почему A).
    """
    abs_t = abs(total)
    reasons_a: list[str] = []

    if abs_t < 0.46:
        reasons_a.append(f"|итог|={abs_t:.2f}<0.46")
    if confidence < 0.60:
        reasons_a.append(f"согласованность={confidence:.2f}<0.60")
    if macro_dampening < 0.93:
        reasons_a.append(f"макро×{macro_dampening:.2f}<0.93")
    if liq_mult < 0.99:
        reasons_a.append("тонкий объём (ликвидность)")
    if vol_align_mult < 0.99:
        reasons_a.append("объём не подтверждает направление")
    if adx14 < 20.0 and not has_chart_pattern:
        # Повышен порог: ADX < 20 без паттерна = боковик, не даём A
        reasons_a.append(f"ADX={adx14:.1f}<20 и нет паттерна на графике")
    if adx14 < 18.5:
        # ADX совсем низкий — не A даже с паттерном
        reasons_a.append(f"ADX={adx14:.1f}<18.5 — выраженный боковик")
    if not _news_aligned(total, news_score):
        reasons_a.append("новости против итога")
    if not weekly_aligned:
        reasons_a.append("недельный тренд против направления")
    if earnings_window:
        reasons_a.append("окно отчётности (±2 дня)")
    if index_headwind:
        reasons_a.append("индекс против направления")

    if not reasons_a:
        return (
            "A",
            "Высокое качество по внутренним правилам (согласованность, макро, ликвидность, контекст).",
        )

    # Tier B: confidence >= 0.50 (повышен с 0.42), abs_t >= 0.32, ADX >= 20 (обязателен)
    if abs_t >= 0.32 and confidence >= 0.50 and macro_dampening >= 0.85 and adx14 >= 20.0:
        return (
            "B",
            "Среднее качество. До класса A не хватает: " + "; ".join(reasons_a[:4])
            + ("…" if len(reasons_a) > 4 else ""),
        )

    return (
        "C",
        "Низкая избирательность / много противоречий. До A: " + "; ".join(reasons_a[:5])
        + ("…" if len(reasons_a) > 5 else ""),
    )
