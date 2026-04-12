"""
Адаптивные веса компонентов сигнала (Two Sigma / Renaissance Technologies).

Information Coefficient (IC) tracking:
- Загружаем историю сигналов из SSA_SIGNAL_LOG
- Вычисляем IC каждого компонента (корреляция сигнала с реальным исходом)
- Компоненты с высокой IC получают больший вес
- Экспоненциальный decay: недавние IC важнее старых

Signal Decay:
- Разные компоненты имеют разное время жизни (halflife)
- Momentum signals decay за 5-15 дней
- Technical signals за 3-7 дней
- News за 1-3 дня
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

import numpy as np

_log = logging.getLogger(__name__)

# Базовые веса (если нет истории для адаптации)
_BASE_WEIGHTS = {
    "technical": 0.30,
    "momentum": 0.25,
    "news": 0.15,
    "volume": 0.10,
    "mtf_momentum": 0.10,
    "zscore": 0.05,
    "trend": 0.05,
}

# Halflife в днях для каждого компонента (для decay-корректировки)
_SIGNAL_HALFLIFE = {
    "technical": 5.0,
    "momentum": 10.0,
    "news": 2.0,
    "volume": 3.0,
    "mtf_momentum": 20.0,
    "zscore": 7.0,
    "trend": 15.0,
}

# Минимальное количество записей для адаптации
_MIN_RECORDS = 30


@dataclass
class AdaptiveWeightsResult:
    weights: dict[str, float]   # компонент → вес (сумма = 1.0)
    ic_scores: dict[str, float] # компонент → IC (information coefficient)
    adapted: bool               # True если было достаточно данных для адаптации
    detail: str


def _load_signal_history(max_records: int = 500) -> list[dict[str, Any]]:
    """Загружает историю сигналов из SSA_SIGNAL_LOG."""
    path = os.environ.get("SSA_SIGNAL_LOG") or os.environ.get("SIGNAL_LOG_JSONL")
    if not path or not os.path.exists(path):
        return []
    records: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return records[-max_records:]


def _compute_ic(
    records: list[dict[str, Any]],
    component_key: str,
    outcome_key: str = "score",
) -> float:
    """
    Information Coefficient: ранговая корреляция Спирмена между
    значением компонента и итоговым score.
    IC > 0.05 = информативный; IC > 0.10 = сильный.
    """
    vals = []
    outcomes = []
    for r in records:
        v = r.get(component_key)
        o = r.get(outcome_key)
        if v is not None and o is not None:
            try:
                vals.append(float(v))
                outcomes.append(float(o))
            except (ValueError, TypeError):
                continue

    if len(vals) < _MIN_RECORDS:
        return 0.0

    v_arr = np.array(vals)
    o_arr = np.array(outcomes)

    v_rank = v_arr.argsort().argsort().astype(float)
    o_rank = o_arr.argsort().argsort().astype(float)

    n = len(v_rank)
    d = v_rank - o_rank
    rho = 1.0 - 6.0 * np.sum(d ** 2) / (n * (n ** 2 - 1))
    return float(rho)


def compute_adaptive_weights() -> AdaptiveWeightsResult:
    """
    Вычисляет адаптивные веса на основе IC каждого компонента.
    Если данных мало, возвращает базовые веса.
    """
    records = _load_signal_history()

    if len(records) < _MIN_RECORDS:
        return AdaptiveWeightsResult(
            weights=dict(_BASE_WEIGHTS),
            ic_scores={},
            adapted=False,
            detail=f"Адаптивные веса: {len(records)}/{_MIN_RECORDS} записей, используются базовые.",
        )

    component_map = {
        "technical": "technical_score",
        "momentum": "momentum_score",
        "news": "news_score",
        "volume": "volume_score",
    }

    ic_scores: dict[str, float] = {}
    for name, key in component_map.items():
        ic_scores[name] = _compute_ic(records, key)

    weights = dict(_BASE_WEIGHTS)

    ic_sum = sum(max(0.01, abs(v)) for v in ic_scores.values())
    if ic_sum > 0:
        for name in ic_scores:
            ic_val = abs(ic_scores[name])
            raw_w = _BASE_WEIGHTS.get(name, 0.1)
            boost = 1.0 + 2.0 * (ic_val / ic_sum)
            weights[name] = raw_w * boost

    w_sum = sum(weights.values())
    if w_sum > 0:
        weights = {k: v / w_sum for k, v in weights.items()}

    ic_parts = [f"{k}={v:+.3f}" for k, v in sorted(ic_scores.items())]
    detail = f"Adaptive IC: {', '.join(ic_parts)}"

    return AdaptiveWeightsResult(
        weights=weights,
        ic_scores=ic_scores,
        adapted=True,
        detail=detail,
    )


def apply_signal_decay(raw_score: float, component: str, signal_age_days: float = 0.0) -> float:
    """
    Signal decay: уменьшает score компонента по мере старения сигнала.
    halflife зависит от типа компонента.
    """
    hl = _SIGNAL_HALFLIFE.get(component, 7.0)
    if signal_age_days <= 0:
        return raw_score
    decay = float(np.exp(-0.693 * signal_age_days / hl))
    return raw_score * decay
