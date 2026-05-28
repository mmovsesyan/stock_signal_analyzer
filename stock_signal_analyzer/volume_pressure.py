"""
Объём и «давление» покупок/продаж.

- **CMF (Chaikin Money Flow)** — прокси давления денег по OHLCV.
- **OBV (On-Balance Volume)** — кумулятивный объём по направлению закрытия.
- **OBV-дивергенция** — цена растёт, а OBV падает (или наоборот) → ослабление.
- **Volume spike** — объём > 2x среднего → подтверждение пробоя.
- **MOEX ISS** — VOLTODAY для .ME.
- **Finnhub WebSocket** — tick rule (прокси buy/sell, US).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .finnhub_live import fetch_tape_imbalance_ws
from .moex_iss import MoexQuote, fetch_tqbr_volume_today


@dataclass
class VolumePressureResult:
    score: float
    detail: str
    obv_divergence: str


def _cmf_last(high: pd.Series, low: pd.Series, close: pd.Series, vol: pd.Series, period: int = 20) -> float:
    if len(high) < period or len(low) < period or len(close) < period or len(vol) < period:
        return 0.0
    h = high.astype(float)
    l = low.astype(float)
    c = close.astype(float)
    v = vol.astype(float)
    rng = (h - l).replace(0, np.nan)
    mfm = ((c - l) - (h - c)) / rng
    mfm = mfm.fillna(0.0)
    mfv = mfm * v
    roll_v = v.rolling(period, min_periods=max(5, period // 2)).sum()
    roll_mfv = mfv.rolling(period, min_periods=max(5, period // 2)).sum()
    cmf = roll_mfv / roll_v.replace(0, np.nan)
    last = float(cmf.iloc[-1])
    if math.isnan(last):
        return 0.0
    return float(max(-1.0, min(1.0, last * 2.5)))


def _volume_activity_score(close: pd.Series, vol: pd.Series, period: int = 20) -> float:
    c = close.astype(float)
    v = vol.astype(float)
    if len(c) < max(period, 6) + 1:
        return 0.0
    sma_v = v.rolling(period).mean()
    sma_last = float(sma_v.iloc[-1])
    vol_last = float(v.iloc[-1])
    # Защита от ZeroDivisionError и log(0)
    if sma_last <= 0 or vol_last <= 0:
        return 0.0
    ratio = vol_last / (sma_last + 1e-9)
    # ratio >= 0 гарантировано проверкой vol_last > 0
    c_last = float(c.iloc[-1])
    c_prev = float(c.iloc[-6])
    if c_prev <= 0:
        return 0.0
    ret5 = c_last / c_prev - 1.0
    vol_skew = math.tanh(math.log(max(ratio, 1e-6)) * 1.0)
    direction = math.tanh(ret5 * 25.0)
    return float(max(-1.0, min(1.0, direction * abs(vol_skew))))


def _obv_series(close: pd.Series, vol: pd.Series) -> pd.Series:
    """On-Balance Volume: кумулятивный объём по знаку изменения цены."""
    sign = np.sign(close.diff().fillna(0.0))
    return (sign * vol).cumsum()


def _obv_divergence(close: pd.Series, vol: pd.Series, lookback: int = 20) -> tuple[float, str]:
    """
    Дивергенция OBV vs цена за lookback дней.
    Возвращает (score_adjustment, description).
    Бычья дивергенция: цена падает, OBV растёт → +0.15..+0.25
    Медвежья дивергенция: цена растёт, OBV падает → -0.15..-0.25
    """
    if len(close) < lookback + 5:
        return 0.0, ""

    c = close.astype(float).iloc[-lookback:]
    obv = _obv_series(close.astype(float), vol.astype(float)).iloc[-lookback:]

    price_slope = float(np.polyfit(range(len(c)), c.values, 1)[0])
    obv_slope = float(np.polyfit(range(len(obv)), obv.values, 1)[0])

    avg_price = float(c.mean())
    price_slope_norm = price_slope / max(abs(avg_price), 1e-9)
    avg_obv = float(obv.abs().mean())
    obv_slope_norm = obv_slope / max(avg_obv, 1e-9)

    if price_slope_norm < -0.001 and obv_slope_norm > 0.001:
        strength = min(abs(obv_slope_norm) * 50, 0.25)
        return strength, f"бычья дивергенция OBV (цена↓ OBV↑, сила {strength:.2f})"

    if price_slope_norm > 0.001 and obv_slope_norm < -0.001:
        strength = min(abs(obv_slope_norm) * 50, 0.25)
        return -strength, f"медвежья дивергенция OBV (цена↑ OBV↓, сила {strength:.2f})"

    return 0.0, ""


def _volume_spike(vol: pd.Series, period: int = 20) -> tuple[bool, float]:
    """Объём последней свечи > 2x среднего за period дней."""
    if len(vol) < period + 1:
        return False, 1.0
    avg = float(vol.tail(period + 1).iloc[:-1].mean())
    last = float(vol.iloc[-1])
    if avg <= 0:
        return False, 1.0
    ratio = last / avg
    return ratio > 2.0, ratio


def _moex_volume_score(
    symbol: str,
    hist_volume: pd.Series,
    moex_quote: MoexQuote | None = None,
) -> tuple[float | None, str]:
    if not symbol.upper().endswith(".ME"):
        return None, ""
    try:
        if moex_quote is not None:
            snap = moex_quote
        else:
            snap = fetch_tqbr_volume_today(symbol)
    except Exception as e:
        return None, f"MOEX объём: ошибка ({e})."
    if snap.voltoday is None:
        return None, f"MOEX объём: нет данных ({snap.detail})."

    # Для .ME тикеров: используем историю из MOEX ISS если Yahoo-объём ненадёжный.
    # Yahoo часто отдаёт 0 или мусор для Мосбиржи.
    avg = float(hist_volume.tail(20).mean()) if len(hist_volume) >= 20 else 0.0

    # Если средний объём из истории слишком мал (Yahoo не отдаёт .ME) —
    # используем только абсолютный MOEX voltoday без сравнения.
    if avg <= 0 or (snap.voltoday > 0 and avg > 0 and snap.voltoday / avg > 5.0):
        # Yahoo-объём ненадёжный — оцениваем по абсолютному MOEX voltoday
        # Нейтральный score: есть объём, но нет базы для сравнения
        if snap.voltoday > 10000:
            sc = 0.0  # нейтрально — объём есть, но не знаем много это или мало
        else:
            sc = -0.15  # мало сделок
        detail = f"MOEX: {snap.detail} (нет надёжной базы для сравнения)"
        return sc, detail

    ratio = snap.voltoday / avg
    # Коэффициент 0.5 вместо 0.9 — менее агрессивная реакция на отклонения объёма
    sc = float(max(-1.0, min(1.0, math.tanh(math.log(max(ratio, 1e-6)) * 0.5))))
    detail = (
        f"MOEX: {snap.detail}, отн. к ср. дневн. объёму ≈ {ratio:.2f}x → score={sc:+.3f}"
    )
    return sc, detail


def analyze_volume_pressure(
    hist: pd.DataFrame,
    symbol: str,
    finnhub_api_key: str | None,
    use_tape_ws: bool,
    ws_seconds: float,
    moex_quote: MoexQuote | None = None,
) -> VolumePressureResult:
    need = {"High", "Low", "Close", "Volume"}
    if not need.issubset(set(hist.columns)):
        return VolumePressureResult(0.0, "Нет OHLCV — объём не оценен.", "")

    h = hist["High"]
    l = hist["Low"]
    c = hist["Close"]
    v = hist["Volume"]

    cmf = _cmf_last(h, l, c, v, period=20)
    act = _volume_activity_score(c, v, period=20)

    obv_adj, obv_note = _obv_divergence(c, v, lookback=20)
    spike, spike_ratio = _volume_spike(v, period=20)

    moex_sc, moex_d = _moex_volume_score(symbol, v, moex_quote=moex_quote)
    if moex_sc is not None:
        weights = [0.40, 0.20, 0.15, 0.25]
        scores = [cmf, act, obv_adj, moex_sc]
    else:
        weights = [0.50, 0.25, 0.25]
        scores = [cmf, act, obv_adj]

    wsum = sum(weights)
    base = sum(w * s for w, s in zip(weights, scores)) / wsum

    if spike:
        if base > 0.05:
            base = min(base * 1.25, 1.0)
        elif base < -0.05:
            base = max(base * 1.25, -1.0)
        # Volume spike в направлении сигнала = сильное подтверждение
        if abs(base) > 0.15:
            base = float(np.clip(base * 1.15, -1.0, 1.0))

    parts = [
        f"CMF(20)≈{cmf:+.3f} (давление денег)",
        f"объём×направление≈{act:+.3f}",
    ]
    if obv_note:
        parts.append(obv_note)
    if spike:
        parts.append(f"⚡ volume spike {spike_ratio:.1f}x")
    if moex_d:
        parts.append(moex_d)

    tape_sc: float | None = None
    tape_d = ""
    if use_tape_ws and finnhub_api_key and not symbol.upper().endswith(".ME"):
        tape_sc, tape_d = fetch_tape_imbalance_ws(symbol, api_key=finnhub_api_key, duration_sec=ws_seconds)
        if tape_sc is not None:
            base = float(max(-1.0, min(1.0, 0.62 * base + 0.38 * tape_sc)))
            parts.append(tape_d)

    detail = " | ".join(parts)
    return VolumePressureResult(
        score=float(max(-1.0, min(1.0, base))),
        detail=detail,
        obv_divergence=obv_note,
    )
