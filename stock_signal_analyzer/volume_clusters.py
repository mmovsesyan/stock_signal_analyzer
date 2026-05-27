"""
Volume cluster analysis — Market Profile / Volume-at-Price.

Находит:
- POC (Point of Control) — уровень с максимальным объёмом
- Value Area (VA) — диапазон 70% объёма
- High Volume Nodes (HVN) — кластеры высокого объёма
- Low Volume Nodes (LVN) — зоны низкого объёма (слабые уровни)

Использование:
    from stock_signal_analyzer.volume_clusters import analyze_volume_clusters
    result = analyze_volume_clusters(hist, n_bins=50)
    print(result.poc, result.value_area_low, result.value_area_high)
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

_log = logging.getLogger(__name__)

# Доля объёма для Value Area (классический Market Profile = 70%)
_VA_PCT = 0.70
# Множитель для определения HVN/LVN относительно среднего объёма на бин
_HVN_MULT = 1.5
_LVN_MULT = 0.5


@dataclass
class PriceBin:
    price_low: float
    price_high: float
    volume: float
    trades: int = 0


@dataclass
class VolumeClusterResult:
    """Результат анализа кластеров объёма."""
    poc: float | None                     # Point of Control
    poc_volume: float                     # объём на POC
    value_area_low: float | None          # нижняя граница Value Area
    value_area_high: float | None         # верхняя граница Value Area
    hvn_levels: list[tuple[float, float]]  # (цена, объём) высокообъёмных узлов
    lvn_levels: list[tuple[float, float]]  # (цена, объём) низкообъёмных узлов
    total_volume: float
    detail: str = ""


def _volume_profile(hist: pd.DataFrame, n_bins: int = 50) -> list[PriceBin]:
    """Построить гистограмму объёма по ценовым уровням (TPV — typical price volume)."""
    need = {"High", "Low", "Close", "Volume"}
    if not need.issubset(set(hist.columns)):
        return []

    h = hist["High"].astype(float)
    l = hist["Low"].astype(float)
    c = hist["Close"].astype(float)
    v = hist["Volume"].astype(float)

    # Typical price per candle
    tp = (h + l + c) / 3.0

    price_min = float(tp.min())
    price_max = float(tp.max())
    if price_max <= price_min or math.isnan(price_min) or math.isnan(price_max):
        return []

    bin_edges = np.linspace(price_min, price_max, n_bins + 1)
    bins: list[PriceBin] = []
    for i in range(n_bins):
        low = float(bin_edges[i])
        high = float(bin_edges[i + 1])
        # Найти свечи, чей typical price попадает в бин
        # Последний бин включает правую границу, остальные — нет,
        # чтобы свеча с tp точно на границе не попадала в два бина.
        if i == n_bins - 1:
            mask = (tp >= low) & (tp <= high)
        else:
            mask = (tp >= low) & (tp < high)
        vol = float(v[mask].sum())
        bins.append(PriceBin(price_low=low, price_high=high, volume=vol, trades=int(mask.sum())))

    return bins


def _find_poc(bins: list[PriceBin]) -> tuple[float | None, float]:
    """Найти POC и его объём."""
    if not bins:
        return None, 0.0
    max_vol = max(b.volume for b in bins)
    if max_vol <= 0:
        return None, 0.0
    # Если несколько бинов с одинаковым max — берём средний ценовой центр
    candidates = [b for b in bins if abs(b.volume - max_vol) < 1e-9]
    center = sum((b.price_low + b.price_high) / 2.0 for b in candidates) / len(candidates)
    return center, max_vol


def _value_area(bins: list[PriceBin], poc_idx: int, target_pct: float = _VA_PCT) -> tuple[float | None, float | None]:
    """Найти Value Area вокруг POC, набирая target_pct объёма."""
    total = sum(b.volume for b in bins)
    if total <= 0 or not (0 <= poc_idx < len(bins)):
        return None, None

    target = total * target_pct
    accumulated = bins[poc_idx].volume
    low_idx = poc_idx
    high_idx = poc_idx

    while accumulated < target and (low_idx > 0 or high_idx < len(bins) - 1):
        vol_below = bins[low_idx - 1].volume if low_idx > 0 else -1.0
        vol_above = bins[high_idx + 1].volume if high_idx < len(bins) - 1 else -1.0

        if vol_below >= vol_above and low_idx > 0:
            low_idx -= 1
            accumulated += bins[low_idx].volume
        elif high_idx < len(bins) - 1:
            high_idx += 1
            accumulated += bins[high_idx].volume
        else:
            break

    va_low = bins[low_idx].price_low
    va_high = bins[high_idx].price_high
    return va_low, va_high


def _hvn_lvn(bins: list[PriceBin]) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """Определить High/Low Volume Nodes относительно среднего объёма на бин."""
    if not bins:
        return [], []
    avg_vol = sum(b.volume for b in bins) / len(bins)
    if avg_vol <= 0:
        return [], []

    hvn: list[tuple[float, float]] = []
    lvn: list[tuple[float, float]] = []
    for b in bins:
        center = (b.price_low + b.price_high) / 2.0
        if b.volume >= avg_vol * _HVN_MULT:
            hvn.append((round(center, 4), round(b.volume, 2)))
        elif b.volume <= avg_vol * _LVN_MULT:
            lvn.append((round(center, 4), round(b.volume, 2)))
    return hvn, lvn


def analyze_volume_clusters(hist: pd.DataFrame, n_bins: int = 50) -> VolumeClusterResult:
    """Проанализировать кластеры объёма и вернуть POC, VA, HVN, LVN."""
    bins = _volume_profile(hist, n_bins=n_bins)
    if not bins:
        return VolumeClusterResult(
            poc=None, poc_volume=0.0,
            value_area_low=None, value_area_high=None,
            hvn_levels=[], lvn_levels=[],
            total_volume=0.0,
            detail="Недостаточно данных для кластерного анализа объёма.",
        )

    poc, poc_vol = _find_poc(bins)
    total_vol = sum(b.volume for b in bins)

    # Индекс POC
    poc_idx = 0
    if poc is not None:
        centers = [abs((b.price_low + b.price_high) / 2.0 - poc) for b in bins]
        poc_idx = int(np.argmin(centers))

    va_low, va_high = _value_area(bins, poc_idx)
    hvn, lvn = _hvn_lvn(bins)

    parts: list[str] = []
    if poc is not None:
        parts.append(f"POC={poc:.2f} (vol={poc_vol:.0f})")
    if va_low is not None and va_high is not None:
        parts.append(f"VA=[{va_low:.2f}; {va_high:.2f}]")
    if hvn:
        prices = ", ".join(f"{p:.2f}" for p, _ in hvn[:3])
        parts.append(f"HVN({len(hvn)}): {prices}")
    if lvn:
        prices = ", ".join(f"{p:.2f}" for p, _ in lvn[:3])
        parts.append(f"LVN({len(lvn)}): {prices}")

    detail = " | ".join(parts) if parts else "Кластерный анализ: нет значимых уровней."

    return VolumeClusterResult(
        poc=poc,
        poc_volume=poc_vol,
        value_area_low=va_low,
        value_area_high=va_high,
        hvn_levels=hvn,
        lvn_levels=lvn,
        total_volume=total_vol,
        detail=detail,
    )
