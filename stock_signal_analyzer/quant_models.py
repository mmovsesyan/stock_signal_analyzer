"""
Количественные модели уровня институциональных фондов.

1. Multi-Timeframe Momentum (AQR / Man AHL)
   - 1M, 3M, 6M, 12M time-series momentum с skip-month
   - Взвешенный по волатильности (vol-targeting)

2. Mean-Reversion Z-Score (DE Shaw / Renaissance Technologies)
   - Стандартизированное отклонение от скользящих средних
   - Bollinger Band context

3. Volatility Regime (Bridgewater / Man AHL)
   - Realized vol vs historical median → risk scaling
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


# ── 1. Multi-Timeframe Momentum ──────────────────────────────────────────────

@dataclass
class MtfMomentumResult:
    score: float          # -1..+1
    mom_1m: float
    mom_3m: float
    mom_6m: float
    mom_12m: float
    consistency: float    # 0..1 — какая доля горизонтов согласована
    detail: str


def _skip_month_return(close: pd.Series, months: int) -> float | None:
    """
    Time-series momentum с skip-month: доходность за (months - 1) месяцев,
    начиная с 21 дня назад. Пропуск последнего месяца убирает шум
    краткосрочного mean-reversion (метод AQR/Moskowitz).
    """
    skip_days = 21
    lookback_days = months * 21
    total_needed = lookback_days + skip_days
    if len(close) < total_needed:
        return None
    end_price = float(close.iloc[-skip_days])
    start_price = float(close.iloc[-total_needed])
    if start_price <= 0:
        return None
    return end_price / start_price - 1.0


def _raw_return(close: pd.Series, days: int) -> float | None:
    if len(close) < days + 1:
        return None
    start = float(close.iloc[-days - 1])
    if start <= 0:
        return None
    return float(close.iloc[-1]) / start - 1.0


def analyze_mtf_momentum(close: pd.Series) -> MtfMomentumResult:
    """
    Мультитаймфреймовый импульс по методу AQR / Man AHL.
    Горизонты: 1M (21д raw), 3M, 6M, 12M (со skip-month).
    Веса: 12M=0.35, 6M=0.25, 3M=0.25, 1M=0.15 (длинные горизонты надёжнее).
    """
    close = close.astype(float).dropna()

    m1 = _raw_return(close, 21)
    m3 = _skip_month_return(close, 3)
    m6 = _skip_month_return(close, 6)
    m12 = _skip_month_return(close, 12)

    horizons = {"1M": m1, "3M": m3, "6M": m6, "12M": m12}
    available = {k: v for k, v in horizons.items() if v is not None}

    if not available:
        return MtfMomentumResult(
            score=0.0, mom_1m=0.0, mom_3m=0.0, mom_6m=0.0, mom_12m=0.0,
            consistency=0.0, detail="Мало данных для MTF momentum.",
        )

    weights = {"1M": 0.15, "3M": 0.25, "6M": 0.25, "12M": 0.35}
    normalized: dict[str, float] = {}
    for k, v in available.items():
        scale = {"1M": 12, "3M": 5, "6M": 3, "12M": 2}[k]
        normalized[k] = float(np.tanh(v * scale))

    w_sum = sum(weights[k] for k in available)
    score = sum(normalized[k] * weights[k] / w_sum for k in available)

    signs = [np.sign(v) for v in available.values() if abs(v) > 0.005]
    if signs:
        dominant = np.sign(sum(signs))
        consistency = sum(1 for s in signs if s == dominant) / len(signs)
    else:
        consistency = 0.0

    if consistency >= 0.75:
        score *= 1.15
    elif consistency <= 0.5:
        score *= 0.7

    score = float(np.clip(score, -1.0, 1.0))

    parts = []
    for k in ("1M", "3M", "6M", "12M"):
        v = horizons[k]
        if v is not None:
            parts.append(f"{k}: {v*100:+.1f}%")
    detail = f"MTF momentum: {', '.join(parts)} (согл.={consistency:.0%})"

    return MtfMomentumResult(
        score=score,
        mom_1m=m1 or 0.0,
        mom_3m=m3 or 0.0,
        mom_6m=m6 or 0.0,
        mom_12m=m12 or 0.0,
        consistency=consistency,
        detail=detail,
    )


# ── 2. Mean-Reversion Z-Score ────────────────────────────────────────────────

@dataclass
class ZScoreResult:
    z_20: float           # z-score от SMA20
    z_50: float           # z-score от SMA50
    composite: float      # -1..+1 mean-reversion signal
    extreme: bool         # |z| > 2 на каком-то горизонте
    detail: str


def _z_score(close: pd.Series, period: int) -> float:
    if len(close) < period + 5:
        return 0.0
    sma = close.rolling(period).mean()
    std = close.rolling(period).std()
    last_std = float(std.iloc[-1])
    if last_std < 1e-9:
        return 0.0
    return float((close.iloc[-1] - sma.iloc[-1]) / last_std)


def analyze_zscore(close: pd.Series) -> ZScoreResult:
    """
    Mean-reversion z-score (DE Shaw / RenTech style).
    Отклонение цены от SMA через стандартизацию.
    Z > +2: перекуплен → ожидаем возврат вниз (отрицательный сигнал).
    Z < -2: перепродан → ожидаем возврат вверх (положительный сигнал).
    """
    close = close.astype(float).dropna()
    z20 = _z_score(close, 20)
    z50 = _z_score(close, 50)

    rev_20 = float(np.tanh(-z20 * 0.35))
    rev_50 = float(np.tanh(-z50 * 0.25))
    composite = float(np.clip(0.6 * rev_20 + 0.4 * rev_50, -1.0, 1.0))

    extreme = abs(z20) > 2.0 or abs(z50) > 2.0

    parts = [f"Z(20)={z20:+.2f}", f"Z(50)={z50:+.2f}"]
    if extreme:
        parts.append("ЭКСТРЕМУМ")
    detail = "Z-score: " + ", ".join(parts)

    return ZScoreResult(
        z_20=z20,
        z_50=z50,
        composite=composite,
        extreme=extreme,
        detail=detail,
    )


# ── 3. Volatility Regime ─────────────────────────────────────────────────────

@dataclass
class VolRegimeResult:
    current_vol: float    # текущая реализованная vol (annualized)
    median_vol: float     # медианная vol за lookback
    vol_ratio: float      # current / median
    regime: str           # "low" | "normal" | "high" | "crisis"
    risk_scalar: float    # множитель позиции (risk parity)
    detail: str


def analyze_vol_regime(
    close: pd.Series,
    short_window: int = 20,
    long_window: int = 252,
) -> VolRegimeResult:
    """
    Режим волатильности (Bridgewater / Man AHL).
    Сравниваем реализованную vol за short_window с медианой за long_window.
    Используется для:
      - Масштабирования позиций (risk parity / vol targeting)
      - Переключения стратегий (momentum vs mean-reversion)
    """
    close = close.astype(float).dropna()
    if len(close) < long_window + 10:
        return VolRegimeResult(
            current_vol=0.0, median_vol=0.0, vol_ratio=1.0,
            regime="unknown", risk_scalar=1.0,
            detail="Мало данных для vol regime.",
        )

    log_ret = np.log(close / close.shift(1)).dropna()

    current = float(log_ret.iloc[-short_window:].std() * np.sqrt(252))
    rolling = log_ret.rolling(short_window).std() * np.sqrt(252)
    median = float(rolling.iloc[-long_window:].median())

    if median < 1e-9:
        return VolRegimeResult(
            current_vol=current, median_vol=0.0, vol_ratio=1.0,
            regime="unknown", risk_scalar=1.0,
            detail=f"Vol: {current*100:.1f}% ann (медиана ~0)",
        )

    ratio = current / median

    if ratio < 0.7:
        regime = "low"
    elif ratio < 1.3:
        regime = "normal"
    elif ratio < 2.0:
        regime = "high"
    else:
        regime = "crisis"

    target_vol = median
    risk_scalar = float(np.clip(target_vol / max(current, 1e-6), 0.25, 2.0))

    detail = (
        f"Vol regime: {current*100:.1f}% vs медиана {median*100:.1f}% "
        f"(ratio={ratio:.2f}, {regime}), risk×{risk_scalar:.2f}"
    )

    return VolRegimeResult(
        current_vol=current,
        median_vol=median,
        vol_ratio=ratio,
        regime=regime,
        risk_scalar=risk_scalar,
        detail=detail,
    )


# ── 4. Trend Strength (Turtle / CTA style) ───────────────────────────────────

@dataclass
class TrendStrengthResult:
    score: float          # -1..+1
    breakout_20: bool     # 20-day breakout (Donchian)
    breakout_55: bool     # 55-day breakout
    ma_stack: float       # SMA alignment: +1 = all bullish stacked, -1 = all bearish
    detail: str


def analyze_trend_strength(close: pd.Series, high: pd.Series, low: pd.Series) -> TrendStrengthResult:
    """
    Trend-following по методу Turtle Traders / CTA фондов.
    - Breakout: цена выше/ниже 20-day и 55-day high/low (Donchian channel)
    - MA stack: SMA10 > SMA20 > SMA50 > SMA100 = полный бычий стек
    """
    close = close.astype(float).dropna()
    high = high.astype(float).dropna()
    low = low.astype(float).dropna()

    n = min(len(close), len(high), len(low))
    if n < 60:
        return TrendStrengthResult(
            score=0.0, breakout_20=False, breakout_55=False,
            ma_stack=0.0, detail="Мало данных для trend strength.",
        )

    last = float(close.iloc[-1])

    h20 = float(high.iloc[-20:].max())
    l20 = float(low.iloc[-20:].min())
    h55 = float(high.iloc[-55:].max())
    l55 = float(low.iloc[-55:].min())

    b20_long = last >= h20 * 0.998
    b20_short = last <= l20 * 1.002
    b55_long = last >= h55 * 0.998
    b55_short = last <= l55 * 1.002

    sma10 = float(close.rolling(10).mean().iloc[-1])
    sma20 = float(close.rolling(20).mean().iloc[-1])
    sma50 = float(close.rolling(50).mean().iloc[-1])

    bull_count = sum([sma10 > sma20, sma20 > sma50, last > sma10])
    bear_count = sum([sma10 < sma20, sma20 < sma50, last < sma10])
    ma_stack = (bull_count - bear_count) / 3.0

    breakout_score = 0.0
    if b55_long:
        breakout_score = 0.5
    elif b55_short:
        breakout_score = -0.5
    elif b20_long:
        breakout_score = 0.3
    elif b20_short:
        breakout_score = -0.3

    score = float(np.clip(0.5 * ma_stack + 0.5 * breakout_score, -1.0, 1.0))

    parts = [f"MA stack={ma_stack:+.2f}"]
    if b55_long:
        parts.append("55д breakout ↑")
    elif b55_short:
        parts.append("55д breakout ↓")
    if b20_long:
        parts.append("20д breakout ↑")
    elif b20_short:
        parts.append("20д breakout ↓")

    return TrendStrengthResult(
        score=score,
        breakout_20=b20_long or b20_short,
        breakout_55=b55_long or b55_short,
        ma_stack=ma_stack,
        detail="Trend: " + ", ".join(parts),
    )
