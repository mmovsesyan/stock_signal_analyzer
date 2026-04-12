"""
Фигуры на нескольких свечах: треугольники (восходящий, нисходящий, симметричный).

Эвристики по последнему окну High/Low: наклоны и «плоскость» линий относительно среднего диапазона свечи.
Без гарантии пробоя — только ожидание типичного сценария (часто пробой в сторону сужения для симметричного).
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _slope_norm(y: np.ndarray, avg_range: float) -> float:
    """Линейный наклон ряда, нормированный на типичный дневной диапазон."""
    n = len(y)
    if n < 8:
        return 0.0
    x = np.arange(n, dtype=float)
    m = float(np.polyfit(x, y, 1)[0])
    return m / max(avg_range, 1e-9)


def _cv(x: np.ndarray) -> float:
    mu = float(np.mean(x))
    if abs(mu) < 1e-12:
        return 1.0
    return float(np.std(x) / abs(mu))


def detect_triangle_patterns(df: pd.DataFrame, window: int = 28) -> tuple[float, str]:
    """
    Возвращает (bias −1…+1, описание).

    - Восходящий треугольник: сопротивление ~горизонтально, минимумы растут → чаще ожидают пробой вверх.
    - Нисходящий: поддержка ~горизонтально, максимумы падают → вниз.
    - Симметричный: сходящиеся максимумы и минимумы → нейтрально до пробоя (лёгкий сдвиг по положению цены в диапазоне).
    """
    need = {"High", "Low", "Close"}
    if not need.issubset(df.columns):
        return 0.0, ""
    d = df[["High", "Low", "Close"]].astype(float).dropna()
    w = min(window, len(d))
    if w < 18:
        return 0.0, ""

    seg = d.iloc[-w:]
    highs = seg["High"].values
    lows = seg["Low"].values
    closes = seg["Close"].values
    avg_range = float(np.mean(highs - lows))
    if avg_range <= 0:
        return 0.0, ""

    sh = _slope_norm(highs, avg_range)
    sl = _slope_norm(lows, avg_range)
    cv_h = _cv(highs)
    cv_l = _cv(lows)

    # Восходящий: плоское верхнее сопротивление + восходящая поддержка
    # Пороги по норм. наклону подобраны под дневной масштаб (наклон слабее, чем у минуток).
    flat_top = cv_h < 0.035 and abs(sh) < 0.55
    rising_support = sl > 0.038
    # Верх не должен устойчиво «стекать» вниз — иначе это симметричный треугольник.
    ascending = flat_top and rising_support and sl > abs(sh) * 0.75 and sh > -0.055

    # Нисходящий: плоская поддержка + нисходящие максимумы
    flat_bot = cv_l < 0.035 and abs(sl) < 0.55
    falling_res = sh < -0.038
    descending = flat_bot and falling_res and abs(sh) > abs(sl) * 0.75 and sl < 0.055

    # Симметричный: сходящиеся максимумы и минимумы (наклоны слабее, чем у «чистых» треугольников выше)
    symmetric = sh < -0.018 and sl > 0.018 and not ascending and not descending

    if ascending:
        return 0.36, "Треугольник восходящий (гориз. сопротивление, растущие минимумы; типично ожидают пробой вверх)"

    if descending:
        return -0.36, "Треугольник нисходящий (гориз. поддержка, падающие максимумы; типично ожидают пробой вниз)"

    if symmetric:
        hi_lo = float(highs.max() - lows.min())
        pos = (float(closes[-1]) - float(lows.min())) / max(hi_lo, 1e-9)
        if pos > 0.66:
            bias = 0.1
            note = "ближе к верхней границе"
        elif pos < 0.34:
            bias = -0.1
            note = "ближе к нижней границе"
        else:
            bias = 0.0
            note = "в середине диапазона"
        return (
            bias,
            f"Симметричный треугольник (сужение; {note} — до пробоя нейтрально)",
        )

    return 0.0, ""
