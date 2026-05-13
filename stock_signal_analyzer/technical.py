"""
Технические индикаторы: тренд, RSI, MACD, ADX, Bollinger Band squeeze,
MACD-гистограмма дивергенция, свечные и графические паттерны.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .candlestick_patterns import detect_recent_patterns
from .chart_patterns import detect_triangle_patterns


@dataclass
class TechnicalScore:
    """Значения от -1 (медвежье) до +1 (бычье)."""

    score: float
    rsi14: float
    above_sma50: bool
    macd_bullish: bool
    details: str
    adx14: float
    regime: str
    pattern_summary: str
    bb_squeeze: bool
    macd_divergence: str


def _rsi(series: pd.Series, period: int = 14) -> float:
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.rolling(period, min_periods=period).mean()
    avg_loss = loss.rolling(period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    v = float(rsi.iloc[-1])
    if np.isnan(v):
        return 50.0
    return v


def _macd(close: pd.Series) -> tuple[float, float, float]:
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal = macd_line.ewm(span=9, adjust=False).mean()
    return float(macd_line.iloc[-1]), float(signal.iloc[-1]), float((macd_line - signal).iloc[-1])


def _macd_histogram_divergence(close: pd.Series, lookback: int = 14) -> tuple[float, str]:
    """
    MACD-гистограмма дивергенция: цена делает новый max/min, а гистограмма — нет.
    Бычья: цена ниже, гистограмма выше предыдущего минимума.
    Медвежья: цена выше, гистограмма ниже предыдущего максимума.
    """
    if len(close) < 40:
        return 0.0, ""
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    hist = ema12 - ema26
    sig = hist.ewm(span=9, adjust=False).mean()
    macd_hist = hist - sig

    c = close.iloc[-lookback:]
    mh = macd_hist.iloc[-lookback:]

    price_new_low = float(c.iloc[-1]) <= float(c.min()) * 1.015
    price_new_high = float(c.iloc[-1]) >= float(c.max()) * 0.985

    half = lookback // 2
    if price_new_low and float(mh.iloc[-1]) > float(mh.iloc[:half].min()):
        return 0.18, "бычья дивергенция MACD-гистограммы"
    if price_new_high and float(mh.iloc[-1]) < float(mh.iloc[:half].max()):
        return -0.18, "медвежья дивергенция MACD-гистограммы"

    return 0.0, ""


def _bollinger_squeeze(close: pd.Series, period: int = 20, squeeze_pct: float = 0.04) -> bool:
    """
    Bollinger Band squeeze: ширина полос < squeeze_pct от цены.
    Предвещает резкое движение (пробой), но не говорит о направлении.
    """
    if len(close) < period + 5:
        return False
    sma = close.rolling(period).mean()
    std = close.rolling(period).std()
    bw = (2.0 * std / sma).dropna()
    if len(bw) < 2:
        return False
    last_bw = float(bw.iloc[-1])
    return last_bw < squeeze_pct


def _adx_last(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> float:
    h = high.astype(float)
    l = low.astype(float)
    c = close.astype(float)
    c_prev = c.shift(1)
    tr = pd.concat([h - l, (h - c_prev).abs(), (l - c_prev).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0 / period, adjust=False).mean()
    up_move = h.diff()
    down_move = -l.diff()
    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
        index=close.index,
        dtype=float,
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
        index=close.index,
        dtype=float,
    )
    atr_safe = atr.replace(0, np.nan)
    plus_di = 100.0 * (plus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr_safe)
    minus_di = 100.0 * (minus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr_safe)
    den = (plus_di + minus_di).replace(0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / den
    adx = dx.ewm(alpha=1.0 / period, adjust=False).mean()
    v = float(adx.iloc[-1])
    if np.isnan(v):
        return 25.0
    return float(np.clip(v, 0.0, 100.0))


def _regime_from_adx(adx: float) -> str:
    if adx < 18.0:
        return "боковик / слабый тренд (ADX низкий)"
    if adx > 28.0:
        return "выраженный тренд (ADX высокий)"
    return "умеренный тренд"


def analyze_technical(hist: pd.DataFrame) -> TechnicalScore:
    """hist: колонки Open, High, Low, Close (как в yfinance)."""
    need = {"Close"}
    if not need.issubset(hist.columns):
        raise ValueError("История должна содержать колонку Close.")
    df = hist.dropna(how="all")
    close = df["Close"].astype(float).dropna()
    if len(close) < 55:
        pat_bias_c, pat_str_c = (0.0, "")
        tri_bias, pat_str_t = (0.0, "")
        if all(c in df.columns for c in ("Open", "High", "Low", "Close")) and len(df) >= 3:
            pat_bias_c, pat_str_c = detect_recent_patterns(df)
        if len(df) >= 18:
            tri_bias, pat_str_t = detect_triangle_patterns(df)
        parts_short = [s for s in (pat_str_c, pat_str_t) if s]
        pat_summary = " | ".join(parts_short)
        score_short = (
            float(np.clip(0.2 * pat_bias_c + 0.14 * tri_bias, -0.65, 0.65))
            if parts_short
            else 0.0
        )
        return TechnicalScore(
            score=score_short,
            rsi14=50.0,
            above_sma50=False,
            macd_bullish=False,
            details="Мало свечей для RSI/MACD/ADX; учтены свечные/треугольные паттерны (если есть).",
            adx14=25.0,
            regime="недостаточно данных",
            pattern_summary=pat_summary,
            bb_squeeze=False,
            macd_divergence="",
        )

    if "High" in df.columns and "Low" in df.columns:
        sub = df.loc[close.index, ["High", "Low", "Close"]].astype(float).dropna()
        if len(sub) >= 19:
            adx_val = _adx_last(sub["High"], sub["Low"], sub["Close"], period=14)
        else:
            adx_val = 25.0
    else:
        adx_val = 25.0

    regime = _regime_from_adx(adx_val)

    rsi = _rsi(close, 14)
    sma20 = float(close.rolling(20).mean().iloc[-1])
    sma50 = float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else sma20
    last = float(close.iloc[-1])
    m, s, _hist = _macd(close)
    macd_bull = m > s

    trend = 0.0
    if last > sma20 > sma50:
        trend = 1.0
    elif last < sma20 < sma50:
        trend = -1.0
    elif last > sma50:
        trend = 0.4
    elif last < sma50:
        trend = -0.4

    if rsi < 30:
        if trend <= -0.8:
            rsi_component = -0.15
        else:
            rsi_component = (30 - rsi) / 30 * 0.9
    elif rsi > 70:
        if trend >= 0.8:
            rsi_component = 0.15
        else:
            rsi_component = -(rsi - 70) / 30 * 0.9
    else:
        rsi_component = (rsi - 50) / 40 * 0.3

    macd_c = 0.6 if macd_bull else -0.6

    # MACD histogram divergence
    macd_div_adj, macd_div_note = _macd_histogram_divergence(close)

    # Bollinger squeeze — усиливаем сигнал если есть squeeze + направление
    bb_sq = _bollinger_squeeze(close)
    bb_boost = 0.0
    if bb_sq and adx_val < 20.0:
        # Squeeze в боковике: вероятен пробой, усиливаем направленный сигнал
        bb_boost = 0.08 * np.sign(trend) if abs(trend) > 0.3 else 0.0

    score = float(np.clip(
        0.30 * rsi_component + 0.30 * trend + 0.25 * macd_c + 0.10 * macd_div_adj + 0.05 * bb_boost,
        -1.0, 1.0,
    ))

    if adx_val < 18.0:
        score *= 0.88

    pat_bias_c, pat_str_c = (0.0, "")
    tri_bias, pat_str_t = (0.0, "")
    if all(c in df.columns for c in ("Open", "High", "Low", "Close")) and len(df) >= 3:
        pat_bias_c, pat_str_c = detect_recent_patterns(df)
    if len(df) >= 18:
        tri_bias, pat_str_t = detect_triangle_patterns(df)
    score = float(np.clip(score + 0.16 * pat_bias_c + 0.11 * tri_bias, -1.0, 1.0))
    parts_pat = [s for s in (pat_str_c, pat_str_t) if s]
    pat_summary = " | ".join(parts_pat)

    detail_parts = [
        f"RSI14={rsi:.1f}",
        f"цена vs SMA20/SMA50: {last:.2f}/{sma20:.2f}/{sma50:.2f}",
        f"MACD {'бычий' if macd_bull else 'медвежий'}",
        f"ADX14≈{adx_val:.1f} — {regime}",
    ]
    if bb_sq:
        detail_parts.append("BB squeeze (сужение полос → ожидается пробой)")
    if macd_div_note:
        detail_parts.append(macd_div_note)

    details = ", ".join(detail_parts)
    return TechnicalScore(
        score=score,
        rsi14=rsi,
        above_sma50=last >= sma50,
        macd_bullish=macd_bull,
        details=details,
        adx14=adx_val,
        regime=regime,
        pattern_summary=pat_summary,
        bb_squeeze=bb_sq,
        macd_divergence=macd_div_note,
    )
