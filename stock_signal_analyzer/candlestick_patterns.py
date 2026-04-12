"""
Свечные паттерны (последние 1–3 дневные свечи).

Классические определения в духе TA-Lib / литературы; без внешних зависимостей.
Паттерны: engulfing, hammer, shooting star, morning/evening star,
doji, piercing line, dark cloud cover, three white soldiers, three black crows.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _row_ohlc(df: pd.DataFrame, i: int) -> tuple[float, float, float, float]:
    o = float(df["Open"].iloc[i])
    h = float(df["High"].iloc[i])
    l = float(df["Low"].iloc[i])
    c = float(df["Close"].iloc[i])
    return o, h, l, c


def _trend_bias(close: pd.Series) -> float:
    if len(close) < 6:
        return 0.0
    r = float(close.iloc[-1] / close.iloc[-6] - 1.0)
    if r < -0.015:
        return -1.0
    if r > 0.015:
        return 1.0
    return 0.0


def _body(o: float, c: float) -> float:
    return abs(c - o)


def _range(h: float, l: float) -> float:
    return max(h - l, 1e-9)


# ── Двухсвечные паттерны ─────────────────────────────────────────────────────

def _bullish_engulfing(prev: tuple, cur: tuple) -> bool:
    o1, h1, l1, c1 = prev
    o2, h2, l2, c2 = cur
    bear1 = c1 < o1
    bull2 = c2 > o2
    if not (bear1 and bull2):
        return False
    return o2 <= c1 and c2 >= o1 and (c2 - o2) > (o1 - c1) * 0.9


def _bearish_engulfing(prev: tuple, cur: tuple) -> bool:
    o1, h1, l1, c1 = prev
    o2, h2, l2, c2 = cur
    bull1 = c1 > o1
    bear2 = c2 < o2
    if not (bull1 and bear2):
        return False
    return o2 >= c1 and c2 <= o1 and (o2 - c2) > (c1 - o1) * 0.9


def _piercing_line(prev: tuple, cur: tuple) -> bool:
    """Бычий: после медвежьей свечи, открытие ниже минимума, закрытие выше середины тела."""
    o1, h1, l1, c1 = prev
    o2, h2, l2, c2 = cur
    if c1 >= o1:
        return False
    if c2 <= o2:
        return False
    mid1 = (o1 + c1) / 2.0
    return o2 < l1 and c2 > mid1 and c2 < o1


def _dark_cloud_cover(prev: tuple, cur: tuple) -> bool:
    """Медвежий: после бычьей свечи, открытие выше максимума, закрытие ниже середины тела."""
    o1, h1, l1, c1 = prev
    o2, h2, l2, c2 = cur
    if c1 <= o1:
        return False
    if c2 >= o2:
        return False
    mid1 = (o1 + c1) / 2.0
    return o2 > h1 and c2 < mid1 and c2 > o1


# ── Односвечные паттерны ─────────────────────────────────────────────────────

def _hammer(o: float, h: float, l: float, c: float) -> bool:
    body = _body(o, c)
    rng = _range(h, l)
    if body / rng > 0.38:
        return False
    lower = min(o, c) - l
    upper = h - max(o, c)
    return lower >= 2.0 * max(body, rng * 0.02) and upper <= max(body, rng * 0.03) * 1.2


def _shooting_star(o: float, h: float, l: float, c: float) -> bool:
    body = _body(o, c)
    rng = _range(h, l)
    if body / rng > 0.35:
        return False
    upper = h - max(o, c)
    lower = min(o, c) - l
    return upper >= 2.0 * max(body, rng * 0.02) and lower <= max(body, rng * 0.03) * 1.2


def _doji(o: float, h: float, l: float, c: float) -> bool:
    """Doji: тело < 8% диапазона (неопределённость)."""
    rng = _range(h, l)
    return _body(o, c) / rng < 0.08


# ── Трёхсвечные паттерны ─────────────────────────────────────────────────────

def _morning_star(r1: tuple, r2: tuple, r3: tuple) -> bool:
    o1, h1, l1, c1 = r1
    o2, h2, l2, c2 = r2
    o3, h3, l3, c3 = r3
    body1 = _body(c1, o1)
    rng1 = _range(h1, l1)
    body2 = _body(c2, o2)
    rng2 = _range(h2, l2)
    body3 = _body(c3, o3)
    rng3 = _range(h3, l3)
    bear1 = c1 < o1 and body1 > rng1 * 0.55
    small2 = body2 <= rng2 * 0.35
    bull3 = c3 > o3 and body3 > rng3 * 0.45
    if not (bear1 and small2 and bull3):
        return False
    mid1 = (o1 + c1) / 2.0
    return c3 >= mid1 - abs(o1 - c1) * 0.05


def _evening_star(r1: tuple, r2: tuple, r3: tuple) -> bool:
    o1, h1, l1, c1 = r1
    o2, h2, l2, c2 = r2
    o3, h3, l3, c3 = r3
    body1 = _body(c1, o1)
    rng1 = _range(h1, l1)
    body2 = _body(c2, o2)
    rng2 = _range(h2, l2)
    body3 = _body(c3, o3)
    rng3 = _range(h3, l3)
    bull1 = c1 > o1 and body1 > rng1 * 0.55
    small2 = body2 <= rng2 * 0.35
    bear3 = c3 < o3 and body3 > rng3 * 0.45
    if not (bull1 and small2 and bear3):
        return False
    mid1 = (o1 + c1) / 2.0
    return c3 <= mid1 + abs(o1 - c1) * 0.05


def _three_white_soldiers(r1: tuple, r2: tuple, r3: tuple) -> bool:
    """Три белых солдата: три последовательных бычьих свечи с растущими телами."""
    for (o, h, l, c) in (r1, r2, r3):
        if c <= o:
            return False
        if _body(o, c) < _range(h, l) * 0.45:
            return False
    _, _, _, c1 = r1
    o2, _, _, c2 = r2
    o3, _, _, c3 = r3
    return c2 > c1 and c3 > c2 and o2 > r1[0] and o3 > o2


def _three_black_crows(r1: tuple, r2: tuple, r3: tuple) -> bool:
    """Три чёрных вороны: три последовательных медвежьих свечи."""
    for (o, h, l, c) in (r1, r2, r3):
        if c >= o:
            return False
        if _body(o, c) < _range(h, l) * 0.45:
            return False
    _, _, _, c1 = r1
    o2, _, _, c2 = r2
    o3, _, _, c3 = r3
    return c2 < c1 and c3 < c2 and o2 < r1[0] and o3 < o2


# ── Публичная функция ────────────────────────────────────────────────────────

def detect_recent_patterns(df: pd.DataFrame) -> tuple[float, str]:
    need = {"Open", "High", "Low", "Close"}
    if not need.issubset(df.columns):
        return 0.0, ""
    d = df[list(need)].astype(float).dropna()
    if len(d) < 3:
        return 0.0, ""
    close = d["Close"]
    n = len(d)
    hits: list[tuple[str, float]] = []

    if n >= 2:
        prev = _row_ohlc(d, -2)
        cur = _row_ohlc(d, -1)
        if _bullish_engulfing(prev, cur):
            hits.append(("бычье поглощение", 0.52))
        elif _bearish_engulfing(prev, cur):
            hits.append(("медвежье поглощение", -0.52))
        if _piercing_line(prev, cur):
            hits.append(("просвет в облаках", 0.40))
        elif _dark_cloud_cover(prev, cur):
            hits.append(("завеса из тёмных облаков", -0.40))

    if n >= 3:
        r1 = _row_ohlc(d, -3)
        r2 = _row_ohlc(d, -2)
        r3 = _row_ohlc(d, -1)
        if _morning_star(r1, r2, r3):
            hits.append(("утренняя звезда", 0.58))
        elif _evening_star(r1, r2, r3):
            hits.append(("вечерняя звезда", -0.58))
        if _three_white_soldiers(r1, r2, r3):
            hits.append(("три белых солдата", 0.55))
        elif _three_black_crows(r1, r2, r3):
            hits.append(("три чёрных вороны", -0.55))

    o, h, l, c = _row_ohlc(d, -1)
    tr = _trend_bias(close)

    if _doji(o, h, l, c):
        if tr >= 0.5:
            hits.append(("дожи (неопределённость на вершине)", -0.25))
        elif tr <= -0.5:
            hits.append(("дожи (неопределённость на дне)", 0.20))

    if _hammer(o, h, l, c) and tr <= 0.0:
        hits.append(("молот (разворот внизу)", 0.42))
    if _shooting_star(o, h, l, c) and tr >= 0.0:
        hits.append(("звёздочка (разворот вверху)", -0.42))

    if not hits:
        return 0.0, ""

    names = [h[0] for h in hits]
    signs = {1 if h[1] > 0 else (-1 if h[1] < 0 else 0) for h in hits}
    strongest = max(hits, key=lambda x: abs(x[1]))
    if len(signs) > 1:
        bias = strongest[1]
    elif len(hits) == 1:
        bias = hits[0][1]
    else:
        bias = float(np.clip(sum(h[1] for h in hits) / len(hits), -0.62, 0.62))

    detail = "Паттерны: " + ", ".join(names) + f" (вклад {bias:+.2f})"
    return float(np.clip(bias, -1.0, 1.0)), detail
