"""
Контекст риска: ATR%, классификация качества сигнала (A/B/C).

Режим «A» — узкий фильтр (много условий сразу), чтобы повысить долю «чистых» сигналов
в бэктесте; это не гарантия конкретного win rate на будущем рынке.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .market_regime import MarketRegime


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
    has_chart_pattern: bool,
    weekly_aligned: bool = True,
    earnings_window: bool = False,
    index_headwind: bool = False,
    market_regime: str = "neutral",
    directional_regime: MarketRegime | None = None,
) -> tuple[str, str]:
    """
    A — строгий набор условий: сильный score, высокая confidence, тренд, нет конфликтов.
    B — умеренный. C — наблюдение / много противоречий.
    """
    abs_t = abs(total)
    reasons_a: list[str] = []

    # Dynamic thresholds based on directional regime
    # Neutral defaults: calibrated for typical score distribution
    score_thr = 0.40
    conf_thr = 0.55
    adx_thr = 20.0

    if directional_regime is not None:
        if directional_regime.regime == "bull":
            if total > 0:
                score_thr = 0.35  # aligned long
                conf_thr = 0.50
                adx_thr = 16.0
            else:
                score_thr = 0.42  # counter-trend short
                conf_thr = 0.55
                adx_thr = 20.0
        elif directional_regime.regime == "bear":
            if total < 0:
                score_thr = 0.35  # aligned short
                conf_thr = 0.50
                adx_thr = 16.0
            else:
                score_thr = 0.42  # counter-trend long
                conf_thr = 0.55
                adx_thr = 20.0
        elif directional_regime.regime == "sideways":
            score_thr = 0.38
            conf_thr = 0.55
            adx_thr = 20.0

    if abs_t < score_thr:
        reasons_a.append(f"|итог|={abs_t:.2f}<{score_thr:.2f}")
    if confidence < conf_thr:
        reasons_a.append(f"согласованность={confidence:.2f}<{conf_thr:.2f}")
    if macro_dampening < 0.90:
        reasons_a.append(f"макро×{macro_dampening:.2f}<0.90")
    if adx14 < adx_thr and not has_chart_pattern:
        reasons_a.append("ADX низкий и нет паттерна на графике")
    if not _news_aligned(total, news_score):
        reasons_a.append("новости против итога")
    if not weekly_aligned:
        reasons_a.append("недельный тренд против направления")
    if earnings_window:
        reasons_a.append("окно отчётности (±2 дня)")
    if index_headwind:
        reasons_a.append("индекс против направления")
    if market_regime == "crisis":
        reasons_a.append("рынок в кризисном режиме")

    if not reasons_a:
        return (
            "A",
            "Высокое качество: сильный сигнал, согласованность компонентов, благоприятный контекст.",
        )

    # Tier B: динамические пороги ADX (строже в боковике/нейтрале, мягче в тренде)
    tier_b_score_thr = 0.26
    tier_b_conf_thr = 0.45
    tier_b_adx_thr = 20.0
    if directional_regime is not None and directional_regime.regime in ("bull", "bear"):
        aligned = (
            (directional_regime.regime == "bull" and total > 0)
            or (directional_regime.regime == "bear" and total < 0)
        )
        if aligned:
            tier_b_score_thr = 0.24
            tier_b_conf_thr = 0.40
            tier_b_adx_thr = 18.0
    if (
        abs_t >= tier_b_score_thr
        and confidence >= tier_b_conf_thr
        and macro_dampening >= 0.85
        and adx14 >= tier_b_adx_thr
    ):
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
