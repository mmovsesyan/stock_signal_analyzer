"""
Режимы рынка и межрыночные сигналы (Bridgewater / Man AHL / AQR).

1. Cross-Asset Regime — VIX, DXY, US Treasury yields → risk-on / risk-off
2. Markov-like regime scoring — объединение vol regime + cross-asset → state
3. Strategy selector — momentum vs mean-reversion по режиму
"""

from __future__ import annotations

import logging
import threading
import time as _time
from dataclasses import dataclass

import numpy as np
import yfinance as yf

_log = logging.getLogger(__name__)


@dataclass
class CrossAssetRegime:
    """Межрыночный режим."""
    vix_level: float
    vix_regime: str          # "complacent" | "normal" | "fear" | "panic"
    vix_trend: float         # изменение VIX за 5 дней
    dxy_trend: float         # изменение DXY за 20 дней
    yield_10y: float         # последний US 10Y yield
    yield_trend: float       # изменение yield за 20 дней
    risk_regime: str         # "risk-on" | "neutral" | "risk-off" | "crisis"
    strategy_bias: str       # "momentum" | "mean-reversion" | "neutral"
    risk_multiplier: float   # множитель для позиций (0.3..1.2)
    detail: str


_REGIME_CACHE_TTL = 600.0
_regime_lock = threading.Lock()
_regime_cache: CrossAssetRegime | None = None
_regime_cache_ts: float = 0.0


def _safe_fetch_close(ticker: str, period: str = "6mo") -> tuple[float, float, float]:
    """Возвращает (last_close, ret_5d, ret_20d)."""
    try:
        h = yf.Ticker(ticker).history(period=period, interval="1d", auto_adjust=True)
        if h is None or h.empty or len(h) < 25:
            return 0.0, 0.0, 0.0
        c = h["Close"].astype(float)
        last = float(c.iloc[-1])
        r5 = float(c.iloc[-1] / c.iloc[-6] - 1.0) if len(c) >= 6 else 0.0
        r20 = float(c.iloc[-1] / c.iloc[-21] - 1.0) if len(c) >= 21 else 0.0
        return last, r5, r20
    except Exception as e:
        _log.debug("Failed to fetch %s: %s", ticker, e)
        return 0.0, 0.0, 0.0


def _vix_regime(vix: float) -> str:
    if vix <= 0:
        return "unknown"
    if vix < 14:
        return "complacent"
    if vix < 20:
        return "normal"
    if vix < 30:
        return "fear"
    return "panic"


def _classify_risk_regime(
    vix: float,
    vix_trend: float,
    dxy_trend: float,
    yield_trend: float,
) -> tuple[str, str, float]:
    """
    Composite risk regime.
    Factors (Bridgewater All Weather logic):
      - VIX high + rising → risk-off
      - DXY rising sharply → flight to safety (risk-off for EM & equities)
      - Yields rising fast → tightening (negative for growth)
    Returns (regime, strategy_bias, risk_multiplier).
    """
    risk_score = 0.0

    if vix > 30:
        risk_score -= 2.0
    elif vix > 25:
        risk_score -= 1.0
    elif vix > 20:
        risk_score -= 0.5
    elif vix < 14:
        risk_score += 0.5

    if vix_trend > 0.15:
        risk_score -= 1.5
    elif vix_trend > 0.08:
        risk_score -= 0.7
    elif vix_trend < -0.10:
        risk_score += 0.5

    if abs(dxy_trend) > 0.03:
        risk_score -= 0.3 * np.sign(dxy_trend)

    if yield_trend > 0.15:
        risk_score -= 0.5
    elif yield_trend < -0.10:
        risk_score += 0.3

    if risk_score >= 0.5:
        regime = "risk-on"
        bias = "momentum"
        mult = 1.1
    elif risk_score >= -0.5:
        regime = "neutral"
        bias = "neutral"
        mult = 1.0
    elif risk_score >= -1.5:
        regime = "risk-off"
        bias = "mean-reversion"
        mult = 0.7
    else:
        regime = "crisis"
        bias = "mean-reversion"
        mult = 0.4

    return regime, bias, float(np.clip(mult, 0.3, 1.2))


def build_cross_asset_regime() -> CrossAssetRegime:
    """
    Анализирует VIX, DXY, US 10Y Treasury → классификация рыночного режима.
    Кэшируется на 10 минут.
    """
    global _regime_cache, _regime_cache_ts
    with _regime_lock:
        if _regime_cache is not None and (_time.monotonic() - _regime_cache_ts) < _REGIME_CACHE_TTL:
            return _regime_cache

    vix_last, vix_r5, _ = _safe_fetch_close("^VIX", "3mo")
    dxy_last, _, dxy_r20 = _safe_fetch_close("DX-Y.NYB", "3mo")
    tny_last, _, tny_r20 = _safe_fetch_close("^TNX", "3mo")

    vr = _vix_regime(vix_last)
    regime, bias, mult = _classify_risk_regime(vix_last, vix_r5, dxy_r20, tny_r20)

    parts = [
        f"VIX={vix_last:.1f} ({vr}, Δ5д={vix_r5*100:+.1f}%)",
    ]
    if dxy_last > 0:
        parts.append(f"DXY Δ20д={dxy_r20*100:+.1f}%")
    if tny_last > 0:
        parts.append(f"US10Y={tny_last:.2f}% Δ20д={tny_r20*100:+.1f}%")
    parts.append(f"→ {regime}, стратегия: {bias}, risk×{mult:.2f}")

    detail = "Cross-asset: " + ", ".join(parts)

    result = CrossAssetRegime(
        vix_level=vix_last,
        vix_regime=vr,
        vix_trend=vix_r5,
        dxy_trend=dxy_r20,
        yield_10y=tny_last,
        yield_trend=tny_r20,
        risk_regime=regime,
        strategy_bias=bias,
        risk_multiplier=mult,
        detail=detail,
    )

    with _regime_lock:
        _regime_cache = result
        _regime_cache_ts = _time.monotonic()

    return result
