"""
Сентимент заголовков: VADER + финансовый лексикон.

VADER (соцсети) плохо понимает финансовый язык: "Fed cuts rates" для него
нейтрален, а для рынка это бычий сигнал. Финансовый буст корректирует
compound-оценку на основе ключевых слов, типичных для биржевых новостей.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass

import numpy as np
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

# Optional FinBERT ensemble (heavyweight ML, lazy-loaded)
try:
    from .finbert_sentiment import (
        finbert_available,
        score_headlines_finbert,
        ensemble_score,
    )
except Exception:
    finbert_available = lambda: False
    score_headlines_finbert = lambda _: []
    ensemble_score = lambda v, f, **kw: v

from .news_feeds import NewsItem


# Потокобезопасный хранитель: каждый поток получает свой экземпляр анализатора.
_local = threading.local()


def _get_analyzer() -> SentimentIntensityAnalyzer:
    if not hasattr(_local, "analyzer"):
        _local.analyzer = SentimentIntensityAnalyzer()
    return _local.analyzer

# ── Финансовый лексикон ──────────────────────────────────────────────────────
# (regex pattern, sentiment boost)  — буст суммируется с VADER compound.
# Диапазон boost ±0.35; итог клиппится до [-1, 1].

_BULLISH_PATTERNS: list[tuple[re.Pattern, float]] = [
    (re.compile(r"\b(beat|beats|topped|surpass)\w*\s+(estimates?|expectations?|forecast)", re.I), 0.30),
    (re.compile(r"\b(record|all[- ]time)\s+(high|revenue|profit|earnings)", re.I), 0.28),
    (re.compile(r"\b(rate\s+cut|cuts?\s+rates?|dovish)", re.I), 0.25),
    (re.compile(r"\b(upgrade[sd]?|buy\s+rating|outperform)", re.I), 0.22),
    (re.compile(r"\b(raises?\s+guidance|raises?\s+forecast|raises?\s+outlook)", re.I), 0.25),
    (re.compile(r"\b(strong\s+growth|revenue\s+(up|surge|jump))", re.I), 0.20),
    (re.compile(r"\b(buyback|share\s+repurchase|dividend\s+(hike|increase|raise))", re.I), 0.18),
    (re.compile(r"\b(FDA\s+approv|breakthrough|new\s+contract|major\s+deal)", re.I), 0.22),
    (re.compile(r"\b(bull(ish)?|rally|rallies|breakout|soar|surge[sd]?)\b", re.I), 0.15),
    (re.compile(r"\b(инфляция\s+(снижа|упала)|ставк\w+\s+сниж)", re.I), 0.22),
    (re.compile(r"\b(рекордн\w+\s+(выручк|прибыл|доход)|дивиденд\w+\s+(повыш|увелич))", re.I), 0.22),
]

_BEARISH_PATTERNS: list[tuple[re.Pattern, float]] = [
    (re.compile(r"\b(miss(es|ed)?|fell\s+short|disappoint)\w*\s*(estimates?|expectations?|forecast)?", re.I), -0.30),
    (re.compile(r"\b(rate\s+hike|raises?\s+rates?|hawkish)", re.I), -0.25),
    (re.compile(r"\b(downgrade[sd]?|sell\s+rating|underperform)", re.I), -0.22),
    (re.compile(r"\b(cuts?\s+guidance|lowers?\s+(guidance|forecast|outlook))", re.I), -0.28),
    (re.compile(r"\b(recession|bankruptcy|default|layoff|fraud|investigation|SEC\s+probe)", re.I), -0.25),
    (re.compile(r"\b(crash|plunge|plummet|tank|tumbl|bear(ish)?|selloff|sell[- ]off)\b", re.I), -0.18),
    (re.compile(r"\b(tariff|sanction|trade\s+war|embargo)\b", re.I), -0.15),
    (re.compile(r"\b(debt\s+ceiling|shutdown|geopolit)", re.I), -0.12),
    (re.compile(r"\b(ставк\w+\s+(повыш|подня)|санкци\w+|дефолт|банкротств)", re.I), -0.22),
    (re.compile(r"\b(рецесси|обвал|падени\w+\s+(рынк|индекс|акци))", re.I), -0.20),
]


def _financial_boost(text: str) -> float:
    """Суммарный финансовый буст для одного заголовка."""
    boost = 0.0
    for pat, val in _BULLISH_PATTERNS:
        if pat.search(text):
            boost += val
    for pat, val in _BEARISH_PATTERNS:
        if pat.search(text):
            boost += val
    return float(np.clip(boost, -0.5, 0.5))


@dataclass
class SentimentResult:
    compound: float
    label: str
    headlines_used: int
    fin_boost_avg: float


def score_headlines(items: list[NewsItem], weights: list[float] | None = None) -> SentimentResult:
    if not items:
        return SentimentResult(compound=0.0, label="нет новостей", headlines_used=0, fin_boost_avg=0.0)
    if weights is None or len(weights) != len(items):
        weights = [1.0] * len(items)
    w_arr = np.array(weights, dtype=float)
    wsum = float(np.sum(w_arr))
    if wsum <= 0:
        w_arr = np.ones(len(items))
        wsum = float(len(items))
    w_arr = w_arr / wsum

    compound = 0.0
    total_boost = 0.0
    analyzer = _get_analyzer()

    # FinBERT ensemble (optional)
    vader_scores: list[float] = []
    finbert_scores: list[float] = []
    if finbert_available():
        try:
            finbert_scores = score_headlines_finbert(items)
        except Exception:
            finbert_scores = []

    for it, w in zip(items, w_arr):
        vader = analyzer.polarity_scores(it.title)["compound"]
        fb = _financial_boost(it.title)
        vader_scores.append(float(np.clip(vader + fb, -1.0, 1.0)))
        total_boost += fb * w

    # Ensemble: FinBERT 0.65 + VADER 0.35 (per SentimentIQ research)
    if finbert_scores:
        combined_scores = ensemble_score(vader_scores, finbert_scores, vader_weight=0.35, finbert_weight=0.65)
    else:
        combined_scores = vader_scores

    for cs, w in zip(combined_scores, w_arr):
        compound += cs * w

    compound = float(np.clip(compound, -1.0, 1.0))
    avg_boost = float(total_boost)

    if compound >= 0.15:
        label = "позитив"
    elif compound <= -0.15:
        label = "негатив"
    else:
        label = "нейтрально"
    return SentimentResult(
        compound=compound,
        label=label,
        headlines_used=len(items),
        fin_boost_avg=round(avg_boost, 3),
    )
