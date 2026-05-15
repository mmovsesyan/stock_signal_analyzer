"""
Market Regime Detection — bull / bear / sideways / transition.

Uses existing analyzers (technical, quant_models) to classify the directional
market regime and compute a strength score.

Regime classification:
  - bull:        strong upward trend (ma_stack > 0.3, price above key SMAs, ADX >= 18)
  - bear:        strong downward trend (ma_stack < -0.3, price below key SMAs, ADX >= 18)
  - sideways:    no trend (ADX < 18 or |ma_stack| < 0.2)
  - transition:  everything else (mixed signals, moderate ADX)

Strength (0..1) is a normalized confidence in the regime label.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .quant_models import (
    TrendStrengthResult,
    VolRegimeResult,
    analyze_trend_strength,
    analyze_vol_regime,
)
from .technical import TechnicalScore, analyze_technical

_log = logging.getLogger(__name__)


@dataclass
class MarketRegime:
    """Directional market regime."""
    regime: str           # "bull" | "bear" | "sideways" | "transition"
    strength: float       # 0..1 confidence
    direction: int        # +1 (bull), -1 (bear), 0 (sideways/transition)
    adx: float
    ma_stack: float
    trend_score: float
    vol_regime: str
    detail: str


def detect_market_regime(hist: pd.DataFrame) -> MarketRegime:
    """
    Detect directional market regime from price history.

    Reuses existing analyzers to avoid duplicated math.
    """
    if hist is None or len(hist) < 60:
        return MarketRegime(
            regime="transition",
            strength=0.0,
            direction=0,
            adx=0.0,
            ma_stack=0.0,
            trend_score=0.0,
            vol_regime="normal",
            detail="Insufficient history for regime detection",
        )

    # Ensure required columns exist
    required = {"Close"}
    if not required.issubset(hist.columns):
        return MarketRegime(
            regime="transition",
            strength=0.0,
            direction=0,
            adx=0.0,
            ma_stack=0.0,
            trend_score=0.0,
            vol_regime="normal",
            detail="Missing Close column",
        )

    close = hist["Close"]
    high = hist.get("High", close)
    low = hist.get("Low", close)

    # Reuse existing analyzers
    tech: TechnicalScore = analyze_technical(hist)
    trend: TrendStrengthResult = analyze_trend_strength(close, high, low)
    vol: VolRegimeResult = analyze_vol_regime(close)

    adx = float(tech.adx14)
    ma_stack = float(trend.ma_stack)
    trend_score = float(trend.score)
    vol_regime = str(vol.regime)

    # --- Classification ---
    # Bull: strong positive MA stack + confirming price action + ADX confirms trend
    is_bull = (
        ma_stack > 0.30
        and (tech.above_sma50 or tech.macd_bullish)
        and adx >= 18.0
    )
    # Bear: strong negative MA stack + confirming price action + ADX confirms trend
    is_bear = (
        ma_stack < -0.30
        and (not tech.above_sma50 or not tech.macd_bullish)
        and adx >= 18.0
    )
    # Sideways: low ADX or no MA alignment
    is_sideways = adx < 18.0 or abs(ma_stack) < 0.20

    if is_sideways:
        regime = "sideways"
        direction = 0
    elif is_bull:
        regime = "bull"
        direction = 1
    elif is_bear:
        regime = "bear"
        direction = -1
    else:
        regime = "transition"
        direction = 0

    # --- Strength ---
    # Normalized components
    ma_strength = min(1.0, abs(ma_stack) / 0.8)
    adx_strength = min(1.0, max(0.0, (adx - 18.0) / 20.0))
    trend_strength = min(1.0, abs(trend_score))

    if regime == "sideways":
        # Strength of "sideways" = high when ADX is very low and no trend
        adx_sideways = min(1.0, max(0.0, (22.0 - adx) / 14.0))
        ma_flat = min(1.0, max(0.0, (0.30 - abs(ma_stack)) / 0.30))
        strength = float(np.clip(0.5 * adx_sideways + 0.5 * ma_flat, 0.0, 1.0))
    elif regime == "transition":
        strength = float(np.clip(0.4 * ma_strength + 0.3 * adx_strength + 0.3 * trend_strength, 0.0, 1.0))
    else:
        # Bull / bear
        strength = float(np.clip(0.40 * ma_strength + 0.35 * adx_strength + 0.25 * trend_strength, 0.0, 1.0))

    detail = (
        f"{regime} (strength={strength:.2f}) | "
        f"ADX={adx:.1f}, MA_stack={ma_stack:+.2f}, trend={trend_score:+.2f}, vol={vol_regime}"
    )

    return MarketRegime(
        regime=regime,
        strength=strength,
        direction=direction,
        adx=adx,
        ma_stack=ma_stack,
        trend_score=trend_score,
        vol_regime=vol_regime,
        detail=detail,
    )
