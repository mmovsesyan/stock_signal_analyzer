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
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import numpy as np

_log = logging.getLogger(__name__)

# Базовые веса для 4 основных компонентов (используются в engine.py для wt/wm/wn/wi).
# Квант-модели (mtf_momentum, zscore, trend) добавляются в score отдельно в engine.py.
_BASE_WEIGHTS = {
    "technical": 0.35,
    "momentum": 0.25,
    "news": 0.20,
    "volume": 0.20,
}

# Halflife в днях для каждого компонента (для decay-корректировки)
_SIGNAL_HALFLIFE = {
    "technical": 5.0,
    "momentum": 10.0,
    "news": 1.5,
    "volume": 3.0,
    "mtf_momentum": 20.0,
    "zscore": 7.0,
    "trend": 15.0,
}

# Минимальное количество записей для адаптации (повышено с 30 для статистической значимости)
_MIN_RECORDS = 50

# Время жизни записей для IC (дни) — только последние 90 дней учитываются
_IC_LOOKBACK_DAYS = 90


@dataclass
class AdaptiveWeightsResult:
    weights: dict[str, float]   # компонент → вес (сумма = 1.0)
    ic_scores: dict[str, float] # компонент → IC (information coefficient)
    adapted: bool               # True если было достаточно данных для адаптации
    detail: str


def _load_signal_history(max_records: int = 500) -> list[dict[str, Any]]:
    """Загружает историю сигналов: сначала outcomes (с реальным PnL), потом signal log."""
    records: list[dict[str, Any]] = []

    # 1. Outcomes file (приоритет — содержит реальный PnL)
    outcomes_path = os.environ.get("STOCK_SIGNAL_DATA", "/var/lib/stock_signal_analyzer")
    outcomes_file = os.path.join(outcomes_path, "outcomes.jsonl")
    if os.path.exists(outcomes_file):
        try:
            with open(outcomes_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                        # Пропускаем open (ещё нет результата)
                        if r.get("outcome") == "open":
                            continue
                        records.append(r)
                    except json.JSONDecodeError:
                        continue
        except OSError:
            pass

    # 2. Signal log (fallback, если outcomes мало)
    if len(records) < max_records:
        path = os.environ.get("SSA_SIGNAL_LOG") or os.environ.get("SIGNAL_LOG_JSONL")
        if path and os.path.exists(path):
            seen_ids = {r.get("signal_id") for r in records}
            try:
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            r = json.loads(line)
                            sig_id = f"{r.get('symbol', '')}_{r.get('ts_utc', '')}"
                            if sig_id not in seen_ids:
                                records.append(r)
                        except json.JSONDecodeError:
                            continue
            except OSError:
                pass

    return records[-max_records:]


def _compute_ic(
    records: list[dict[str, Any]],
    component_key: str,
    outcome_key: str = "score",
) -> float | None:
    """
    Information Coefficient: ранговая корреляция Спирмена между
    значением компонента и реальным исходом.

    Приоритет outcome_key:
    1. 'outcome_pnl' — реальный PnL из outcome_tracker (если есть)
    2. 'score' — итоговый score (fallback, но НЕ рекомендуется)

    Экспоненциальный decay: недавние IC важнее старых (halflife=30 дней).

    Возвращает None, если нет реального PnL — IC без PnL бессмыслен.
    IC > 0.05 = информативный; IC > 0.10 = сильный.
    """
    now = time.time()
    halflife_days = 30.0
    decay_factor = np.log(2) / halflife_days

    vals = []
    outcomes = []
    weights = []

    for r in records:
        v = r.get(component_key)
        if v is None:
            continue
        # Используем ТОЛЬКО реальный PnL — корреляция с final_score циркулярна
        o = r.get("outcome_pnl")
        if o is None:
            continue

        # Time decay: недавние записи важнее
        ts_str = r.get("ts_utc") or r.get("exit_date") or r.get("checked_at")
        if ts_str:
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                age_days = (datetime.now(timezone.utc) - ts).total_seconds() / 86400
                # Только последние _IC_LOOKBACK_DAYS дней
                if age_days > _IC_LOOKBACK_DAYS:
                    continue
                w = float(np.exp(-decay_factor * age_days))
            except (ValueError, TypeError):
                w = 0.5  # Не можем распарсить дату — средний вес
        else:
            w = 0.5  # Нет даты — средний вес

        try:
            vals.append(float(v))
            outcomes.append(float(o))
            weights.append(w)
        except (ValueError, TypeError):
            continue

    if len(vals) < _MIN_RECORDS:
        return None

    # Weighted Spearman IC
    v_arr = np.array(vals)
    o_arr = np.array(outcomes)
    w_arr = np.array(weights)

    # Weighted ranks
    v_rank = v_arr.argsort().argsort().astype(float)
    o_rank = o_arr.argsort().argsort().astype(float)

    # Weighted correlation
    n = len(v_rank)
    w_sum = np.sum(w_arr)
    if w_sum <= 0:
        return 0.0

    v_mean = np.sum(w_arr * v_rank) / w_sum
    o_mean = np.sum(w_arr * o_rank) / w_sum

    cov = np.sum(w_arr * (v_rank - v_mean) * (o_rank - o_mean)) / w_sum
    v_std = np.sqrt(np.sum(w_arr * (v_rank - v_mean) ** 2) / w_sum)
    o_std = np.sqrt(np.sum(w_arr * (o_rank - o_mean) ** 2) / w_sum)

    if v_std <= 0 or o_std <= 0:
        return 0.0

    rho = cov / (v_std * o_std)
    return float(np.clip(rho, -1.0, 1.0))


def compute_adaptive_weights() -> AdaptiveWeightsResult:
    """
    Вычисляет адаптивные веса на основе IC каждого компонента.
    Если данных мало или нет реального PnL, возвращает базовые веса.
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
    has_any_ic = False
    for name, key in component_map.items():
        ic = _compute_ic(records, key)
        if ic is not None:
            ic_scores[name] = ic
            has_any_ic = True

    if not has_any_ic:
        return AdaptiveWeightsResult(
            weights=dict(_BASE_WEIGHTS),
            ic_scores={},
            adapted=False,
            detail="Адаптивные веса: нет записей с реальным PnL, используются базовые.",
        )

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
