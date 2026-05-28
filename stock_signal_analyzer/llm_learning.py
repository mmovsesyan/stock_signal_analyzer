"""
LLM Learning — обучение на исторических сигналах через LLM (локальный или cloud).

Совмещает числовой IC-анализ (adaptive_weights.py) с качественным LLM-анализом:
1. Загружает outcomes (сигналы с реальными PnL)
2. Группирует по win/loss, tier, рынку
3. LLM анализирует паттерны: какие комбинации компонентов приводят к успеху
4. Формирует weight adjustments и текстовые рекомендации
5. Результат кэшируется и используется в engine.py

Архитектура:
- Периодический анализ (раз в сутки или по команде)
- Результат сохраняется в JSON файл (learning_state.json)
- engine.py читает state и применяет корректировки
- Graceful: если LLM недоступен, используются только числовые IC

Переменные окружения:
  LLM_LEARNING        — включить (1/0, по умолчанию 1)
  LLM_LEARNING_MIN    — минимум outcomes для анализа (по умолчанию 20)
  LEARNING_STATE_PATH — путь к файлу состояния
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from .llm_client import llm_available, llm_chat_json, current_provider
import html

_log = logging.getLogger(__name__)

_LLM_LEARNING_ENABLED = os.environ.get("LLM_LEARNING", "1").strip() != "0"
_MIN_OUTCOMES = int(os.environ.get("LLM_LEARNING_MIN", "20"))


def _state_path() -> Path:
    base = os.environ.get("STOCK_SIGNAL_DATA", "/var/lib/stock_signal_analyzer")
    custom = os.environ.get("LEARNING_STATE_PATH")
    if custom:
        return Path(custom)
    return Path(base) / "learning_state.json"


def _outcomes_path() -> Path:
    base = os.environ.get("STOCK_SIGNAL_DATA", "/var/lib/stock_signal_analyzer")
    return Path(base) / "outcomes.jsonl"


# ── Данные ───────────────────────────────────────────────────────────────────


@dataclass
class OutcomeRecord:
    """Один исход сигнала для анализа."""
    symbol: str
    tier: str
    outcome: str  # win_t1, win_t2, loss, timeout
    pnl_pct: float
    score: float
    confidence: float
    technical_score: float
    momentum_score: float
    news_score: float
    volume_score: float
    direction: str
    hold_days: int
    entry_date: str


@dataclass
class LearningState:
    """Состояние обучения — результат LLM-анализа."""
    # Корректировки весов (множители к базовым весам)
    weight_adjustments: dict[str, float] = field(default_factory=dict)
    # Текстовые рекомендации от LLM
    recommendations: list[str] = field(default_factory=list)
    # Паттерны успеха (условия при которых win rate выше)
    win_patterns: list[str] = field(default_factory=list)
    # Паттерны неудачи (условия при которых loss rate выше)
    loss_patterns: list[str] = field(default_factory=list)
    # Оптимальные пороги
    optimal_score_threshold: float = 0.25
    optimal_confidence_threshold: float = 0.55
    # Метаданные
    total_outcomes_analyzed: int = 0
    win_rate: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    last_updated: str = ""
    data_hash: str = ""
    # IC scores (числовые, из adaptive_weights)
    ic_scores: dict[str, float] = field(default_factory=dict)
    # LLM статус для отчёта
    llm_used: bool = False
    llm_provider: str = ""


def _load_outcomes_from_db() -> list[OutcomeRecord]:
    """Загрузить закрытые outcomes из PostgreSQL."""
    try:
        from .db import db_available, get_session, Outcome as DbOutcome, Signal as DbSignal
        if not db_available():
            return []
        with get_session(read_only=True) as session:
            rows = (
                session.query(DbOutcome, DbSignal)
                .join(DbSignal, DbOutcome.signal_id == DbSignal.id)
                .filter(DbOutcome.outcome.notin_(['open', 'timeout']))
                .limit(5000)
                .all()
            )
            records: list[OutcomeRecord] = []
            for outcome, signal in rows:
                if outcome.pnl_pct is None:
                    continue
                records.append(OutcomeRecord(
                    symbol=signal.symbol or "",
                    tier=signal.signal_tier or "C",
                    outcome=outcome.outcome or "",
                    pnl_pct=float(outcome.pnl_pct),
                    score=float(signal.score or 0),
                    confidence=float(signal.confidence or 0),
                    technical_score=float(signal.technical_score or 0),
                    momentum_score=float(signal.momentum_score or 0),
                    news_score=float(signal.news_score or 0),
                    volume_score=float(signal.volume_score or 0),
                    direction=signal.direction or "",
                    hold_days=int(outcome.hold_days or 0),
                    entry_date=signal.created_at.isoformat() if signal.created_at else "",
                ))
            return records
    except Exception as e:
        _log.debug("DB outcomes load skipped in learning: %s", e)
        return []


def _load_outcomes() -> list[OutcomeRecord]:
    """Загрузить закрытые outcomes из файла и БД."""
    records: list[OutcomeRecord] = []
    seen_keys: set[str] = set()

    # 1. PostgreSQL (primary)
    db_records = _load_outcomes_from_db()
    for rec in db_records:
        key = f"{rec.symbol}_{rec.entry_date}_{rec.outcome}"
        if key not in seen_keys:
            records.append(rec)
            seen_keys.add(key)

    # 2. JSONL fallback (legacy)
    path = _outcomes_path()
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                    if r.get("outcome") in ("open", "timeout", None):
                        continue
                    pnl = r.get("outcome_pnl") or r.get("pnl_pct")
                    if pnl is None:
                        continue
                    entry_date = r.get("entry_date", "")
                    key = f"{r.get('symbol','')}_{entry_date}_{r.get('outcome','')}"
                    if key in seen_keys:
                        continue
                    records.append(OutcomeRecord(
                        symbol=r.get("symbol", ""),
                        tier=r.get("signal_tier", "C"),
                        outcome=r.get("outcome", ""),
                        pnl_pct=float(pnl),
                        score=float(r.get("score", 0)),
                        confidence=float(r.get("confidence", 0)),
                        technical_score=float(r.get("technical_score", 0)),
                        momentum_score=float(r.get("momentum_score", 0)),
                        news_score=float(r.get("news_score", 0)),
                        volume_score=float(r.get("volume_score", 0)),
                        direction=r.get("direction", ""),
                        hold_days=int(r.get("hold_days", 0)),
                        entry_date=entry_date,
                    ))
                    seen_keys.add(key)
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue
    return records


def _data_hash(records: list[OutcomeRecord]) -> str:
    """Хэш данных для определения нужно ли пересчитывать."""
    content = f"{len(records)}_{records[-1].entry_date if records else ''}"
    return hashlib.md5(content.encode()).hexdigest()[:12]


# ── Числовой анализ (без LLM) ────────────────────────────────────────────────


def _numeric_analysis(records: list[OutcomeRecord]) -> dict[str, Any]:
    """
    Числовой анализ паттернов: какие значения компонентов коррелируют с win/loss.
    Работает всегда, даже без Ollama.
    """
    wins = [r for r in records if r.pnl_pct > 0]
    losses = [r for r in records if r.pnl_pct <= 0]

    if not wins or not losses:
        return {}

    # Средние значения компонентов для wins vs losses
    components = ["technical_score", "momentum_score", "news_score", "volume_score"]
    analysis: dict[str, Any] = {}

    for comp in components:
        win_vals = [getattr(r, comp) for r in wins]
        loss_vals = [getattr(r, comp) for r in losses]
        win_mean = np.mean(win_vals)
        loss_mean = np.mean(loss_vals)
        # Разница показывает предсказательную силу компонента
        analysis[comp] = {
            "win_mean": float(win_mean),
            "loss_mean": float(loss_mean),
            "delta": float(win_mean - loss_mean),
            "predictive": abs(float(win_mean - loss_mean)) > 0.05,
        }

    # Оптимальные пороги score и confidence
    score_thresholds = np.arange(0.15, 0.50, 0.05)
    best_score_thr = 0.25
    best_score_wr = 0.0
    for thr in score_thresholds:
        filtered = [r for r in records if abs(r.score) >= thr]
        if len(filtered) < 10:
            continue
        wr = sum(1 for r in filtered if r.pnl_pct > 0) / len(filtered)
        if wr > best_score_wr:
            best_score_wr = wr
            best_score_thr = float(thr)

    conf_thresholds = np.arange(0.40, 0.80, 0.05)
    best_conf_thr = 0.55
    best_conf_wr = 0.0
    for thr in conf_thresholds:
        filtered = [r for r in records if r.confidence >= thr]
        if len(filtered) < 10:
            continue
        wr = sum(1 for r in filtered if r.pnl_pct > 0) / len(filtered)
        if wr > best_conf_wr:
            best_conf_wr = wr
            best_conf_thr = float(thr)

    analysis["optimal_score_threshold"] = best_score_thr
    analysis["optimal_confidence_threshold"] = best_conf_thr
    analysis["best_score_win_rate"] = best_score_wr
    analysis["best_conf_win_rate"] = best_conf_wr

    # Weight adjustments на основе delta
    adjustments: dict[str, float] = {}
    for comp in components:
        key = comp.replace("_score", "")
        delta = analysis[comp]["delta"]
        # Компонент с большей delta (wins > losses) получает буст
        if delta > 0.08:
            adjustments[key] = 1.15  # +15%
        elif delta > 0.04:
            adjustments[key] = 1.08  # +8%
        elif delta < -0.04:
            adjustments[key] = 0.90  # -10%
        else:
            adjustments[key] = 1.0
    analysis["weight_adjustments"] = adjustments

    # Tier analysis
    tier_stats: dict[str, dict[str, float]] = {}
    for tier in ("A", "B", "C"):
        tier_recs = [r for r in records if r.tier == tier]
        if tier_recs:
            wr = sum(1 for r in tier_recs if r.pnl_pct > 0) / len(tier_recs)
            avg_pnl = np.mean([r.pnl_pct for r in tier_recs])
            tier_stats[tier] = {"count": len(tier_recs), "win_rate": wr, "avg_pnl": float(avg_pnl)}
    analysis["tier_stats"] = tier_stats

    return analysis


# ── LLM анализ ───────────────────────────────────────────────────────────────

_LLM_SYSTEM_PROMPT = """Ты — опытный трейдер-аналитик. Твоя задача — проанализировать историю торговых сигналов и найти закономерности.

По статистике прибыльных и убыточных сделок определи:
1. Какие сочетания факторов ведут к прибыли (например: «сильная техника + импульс = хороший рост»)
2. При каких условиях сигналы обманывают (например: «новости позитивные, но объёма нет — ложный сигнал»)
3. Какие факторы доверять больше, какие меньше (корректировки весов)
4. Оптимальные пороги качества сигнала

Ответь ТОЛЬКО валидным JSON со структурой:
{
  "win_patterns": ["паттерн1", "паттерн2", "паттерн3"],
  "loss_patterns": ["паттерн1", "паттерн2"],
  "weight_recommendations": {
    "technical": 1.0,
    "momentum": 1.0,
    "news": 1.0,
    "volume": 1.0
  },
  "recommendations": ["совет1", "совет2"],
  "confidence_note": "краткая оценка качества данных"
}

Правила:
- weight_recommendations — множители (1.0 = без изменений, 1.2 = увеличить на 20%, 0.8 = уменьшить на 20%)
- Держи множители в диапазоне 0.7–1.3
- Будь конкретен в паттернах (указывай диапазоны оценок, значения компонентов)
- Не больше 5 паттернов успеха, 5 паттернов неудачи, 3 совета
- Пиши кратко и по делу на русском языке"""


def _build_llm_analysis_prompt(records: list[OutcomeRecord], numeric: dict[str, Any]) -> str:
    """Построить промпт с данными для LLM."""
    wins = [r for r in records if r.pnl_pct > 0]
    losses = [r for r in records if r.pnl_pct <= 0]

    # Топ-5 лучших и худших сделок
    sorted_by_pnl = sorted(records, key=lambda r: r.pnl_pct, reverse=True)
    top_wins = sorted_by_pnl[:5]
    top_losses = sorted_by_pnl[-5:]

    lines = [
        f"Всего сделок: {len(records)} ({len(wins)} прибыльных, {len(losses)} убыточных)",
        f"Доля успешных: {len(wins)/len(records)*100:.1f}%",
        f"Средний плюс: +{np.mean([r.pnl_pct for r in wins]):.2f}%",
        f"Средний минус: {np.mean([r.pnl_pct for r in losses]):.2f}%",
        "",
        "Средние значения факторов (прибыльные vs убыточные):",
    ]

    comp_ru = {
        "technical_score": "техника",
        "momentum_score": "импульс",
        "news_score": "новости",
        "volume_score": "объём",
    }
    for comp in ("technical_score", "momentum_score", "news_score", "volume_score"):
        d = numeric.get(comp, {})
        lines.append(f"  {comp_ru[comp]}: прибыльные={d.get('win_mean', 0):.3f}, убыточные={d.get('loss_mean', 0):.3f}, разница={d.get('delta', 0):.3f}")

    lines.append("")
    lines.append("Топ-5 лучших сделок:")
    for r in top_wins:
        lines.append(f"  {r.symbol} класс={r.tier} оценка={r.score:.3f} уверенность={r.confidence:.2f} "
                     f"тех={r.technical_score:.3f} имп={r.momentum_score:.3f} "
                     f"нов={r.news_score:.3f} об={r.volume_score:.3f} → результат={r.pnl_pct:+.2f}%")

    lines.append("")
    lines.append("Топ-5 худших сделок:")
    for r in top_losses:
        lines.append(f"  {r.symbol} класс={r.tier} оценка={r.score:.3f} уверенность={r.confidence:.2f} "
                     f"тех={r.technical_score:.3f} имп={r.momentum_score:.3f} "
                     f"нов={r.news_score:.3f} об={r.volume_score:.3f} → результат={r.pnl_pct:+.2f}%")

    # Tier breakdown
    tier_stats = numeric.get("tier_stats", {})
    if tier_stats:
        lines.append("")
        lines.append("По классам сигналов:")
        for tier, stats in sorted(tier_stats.items()):
            lines.append(f"  {tier}: {stats['count']} сделок, успешных={stats['win_rate']*100:.1f}%, средний результат={stats['avg_pnl']:+.2f}%")

    lines.append("")
    lines.append(f"Оптимальный порог оценки: {numeric.get('optimal_score_threshold', 0.25):.2f} "
                 f"(успешных={numeric.get('best_score_win_rate', 0)*100:.1f}%)")
    lines.append(f"Оптимальный порог уверенности: {numeric.get('optimal_confidence_threshold', 0.55):.2f} "
                 f"(успешных={numeric.get('best_conf_win_rate', 0)*100:.1f}%)")

    return "\n".join(lines)


def _call_llm_learning(prompt: str) -> dict[str, Any] | None:
    """Вызвать LLM для анализа паттернов (локальный или cloud)."""
    messages = [
        {"role": "system", "content": _LLM_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    result = llm_chat_json(messages, temperature=0.2, max_tokens=800)
    if result is None:
        _log.warning("LLM learning failed")
    return result


# ── Совмещение числового и LLM анализа ───────────────────────────────────────


def _merge_adjustments(
    numeric_adj: dict[str, float],
    llm_adj: dict[str, float] | None,
) -> dict[str, float]:
    """
    Совместить числовые и LLM weight adjustments.
    Числовой анализ: 60% веса (объективный, на данных).
    LLM анализ: 40% веса (качественный, паттерны).
    """
    if llm_adj is None:
        return numeric_adj

    NUMERIC_WEIGHT = 0.60
    LLM_WEIGHT = 0.40

    merged: dict[str, float] = {}
    all_keys = set(numeric_adj.keys()) | set(llm_adj.keys())

    for key in all_keys:
        n_val = numeric_adj.get(key, 1.0)
        l_val = llm_adj.get(key, 1.0)
        # Ограничиваем LLM рекомендации разумным диапазоном
        l_val = max(0.7, min(1.3, l_val))
        merged[key] = NUMERIC_WEIGHT * n_val + LLM_WEIGHT * l_val
        # Итоговый множитель не выходит за [0.75, 1.25]
        merged[key] = max(0.75, min(1.25, merged[key]))

    return merged


# ── Публичный API ────────────────────────────────────────────────────────────


def run_learning_cycle(force: bool = False) -> LearningState:
    """
    Запустить цикл обучения:
    1. Загрузить outcomes
    2. Числовой анализ (всегда)
    3. LLM анализ (если Ollama доступен)
    4. Совместить результаты
    5. Сохранить state

    Args:
        force: пересчитать даже если данные не изменились

    Returns:
        LearningState с корректировками и рекомендациями
    """
    records = _load_outcomes()

    if len(records) < _MIN_OUTCOMES:
        _log.info("Learning: недостаточно outcomes (%d/%d)", len(records), _MIN_OUTCOMES)
        state = LearningState(
            total_outcomes_analyzed=len(records),
            last_updated=datetime.now(timezone.utc).isoformat(),
            recommendations=[f"Нужно минимум {_MIN_OUTCOMES} закрытых сигналов для обучения"],
        )
        _save_learning_state(state)
        return state

    # Проверяем, изменились ли данные
    current_hash = _data_hash(records)
    existing_state = load_learning_state()
    if not force and existing_state and existing_state.data_hash == current_hash:
        _log.info("Learning: данные не изменились, используем кэш")
        return existing_state

    _log.info("Learning: анализ %d outcomes...", len(records))

    # 1. Числовой анализ (всегда работает)
    numeric = _numeric_analysis(records)
    numeric_adj = numeric.get("weight_adjustments", {})

    # 2. LLM анализ (если доступен)
    llm_result: dict[str, Any] | None = None
    llm_adj: dict[str, float] | None = None
    llm_used = False
    llm_provider = ""

    if _LLM_LEARNING_ENABLED:
        try:
            if llm_available():
                llm_provider = current_provider()
                prompt = _build_llm_analysis_prompt(records, numeric)
                llm_result = _call_llm_learning(prompt)
                if llm_result:
                    llm_adj = llm_result.get("weight_recommendations")
                    llm_used = True
                    _log.info("Learning: LLM анализ завершён (%s)", llm_provider)
                else:
                    _log.info("Learning: LLM не вернул результат, только числовой анализ")
            else:
                _log.info("Learning: LLM недоступен, только числовой анализ")
        except Exception as e:
            _log.warning("Learning: LLM failed: %s", e)
    else:
        _log.info("Learning: LLM обучение отключено (LLM_LEARNING=0)")

    # 3. Совместить
    merged_adj = _merge_adjustments(numeric_adj, llm_adj)

    # 4. Собрать state
    wins = [r for r in records if r.pnl_pct > 0]
    losses = [r for r in records if r.pnl_pct <= 0]

    state = LearningState(
        weight_adjustments=merged_adj,
        recommendations=llm_result.get("recommendations", []) if llm_result else [],
        win_patterns=llm_result.get("win_patterns", []) if llm_result else [],
        loss_patterns=llm_result.get("loss_patterns", []) if llm_result else [],
        optimal_score_threshold=numeric.get("optimal_score_threshold", 0.25),
        optimal_confidence_threshold=numeric.get("optimal_confidence_threshold", 0.55),
        total_outcomes_analyzed=len(records),
        win_rate=len(wins) / len(records) if records else 0,
        avg_win_pct=float(np.mean([r.pnl_pct for r in wins])) if wins else 0,
        avg_loss_pct=float(np.mean([r.pnl_pct for r in losses])) if losses else 0,
        last_updated=datetime.now(timezone.utc).isoformat(),
        data_hash=current_hash,
        ic_scores={},
        llm_used=llm_used,
        llm_provider=llm_provider,
    )

    # IC scores из adaptive_weights
    try:
        from .adaptive_weights import compute_adaptive_weights
        aw = compute_adaptive_weights()
        if aw.adapted:
            state.ic_scores = aw.ic_scores
    except Exception:
        pass

    # 5. Сохранить
    _save_learning_state(state)
    _log.info("Learning: state сохранён (win_rate=%.1f%%, adjustments=%s)",
              state.win_rate * 100, state.weight_adjustments)

    return state


def load_learning_state() -> LearningState | None:
    """Загрузить сохранённое состояние обучения."""
    path = _state_path()
    if not path.exists():
        return None
    try:
        content = path.read_text(encoding="utf-8")
        data = json.loads(content)
        if not isinstance(data, dict):
            _log.warning("load_learning_state: expected dict, got %s", type(data).__name__)
            return None
        return LearningState(
            weight_adjustments=data.get("weight_adjustments", {}),
            recommendations=data.get("recommendations", []),
            win_patterns=data.get("win_patterns", []),
            loss_patterns=data.get("loss_patterns", []),
            optimal_score_threshold=data.get("optimal_score_threshold", 0.25),
            optimal_confidence_threshold=data.get("optimal_confidence_threshold", 0.55),
            total_outcomes_analyzed=data.get("total_outcomes_analyzed", 0),
            win_rate=data.get("win_rate", 0),
            avg_win_pct=data.get("avg_win_pct", 0),
            avg_loss_pct=data.get("avg_loss_pct", 0),
            last_updated=data.get("last_updated", ""),
            data_hash=data.get("data_hash", ""),
            ic_scores=data.get("ic_scores", {}),
            llm_used=data.get("llm_used", False),
            llm_provider=data.get("llm_provider", ""),
        )
    except (json.JSONDecodeError, OSError) as e:
        _log.warning("Failed to load learning state: %s", e)
        return None


def _save_learning_state(state: LearningState) -> None:
    """Сохранить состояние обучения в JSON."""
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "weight_adjustments": state.weight_adjustments,
        "recommendations": state.recommendations,
        "win_patterns": state.win_patterns,
        "loss_patterns": state.loss_patterns,
        "optimal_score_threshold": state.optimal_score_threshold,
        "optimal_confidence_threshold": state.optimal_confidence_threshold,
        "total_outcomes_analyzed": state.total_outcomes_analyzed,
        "win_rate": state.win_rate,
        "avg_win_pct": state.avg_win_pct,
        "avg_loss_pct": state.avg_loss_pct,
        "last_updated": state.last_updated,
        "data_hash": state.data_hash,
        "ic_scores": state.ic_scores,
        "llm_used": state.llm_used,
        "llm_provider": state.llm_provider,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_weight_adjustments() -> dict[str, float]:
    """
    Получить текущие корректировки весов для engine.py.
    Быстрый вызов — кэшируется в памяти на 5 минут.
    Возвращает пустой dict если обучение не проводилось.
    """
    return _get_cached_adjustments()


# In-memory cache для weight adjustments (чтобы не читать JSON на каждый вызов engine)
_adj_cache: dict[str, float] = {}
_adj_cache_ts: float = 0.0
_ADJ_CACHE_TTL: float = 300.0  # 5 минут


def _get_cached_adjustments() -> dict[str, float]:
    global _adj_cache, _adj_cache_ts
    now = time.time()
    if now - _adj_cache_ts < _ADJ_CACHE_TTL and _adj_cache is not None:
        return _adj_cache
    state = load_learning_state()
    if state is None:
        _adj_cache = {}
    else:
        _adj_cache = state.weight_adjustments
    _adj_cache_ts = now
    return _adj_cache


def format_learning_report() -> str:
    """Форматировать отчёт об обучении для Telegram/CLI."""
    state = load_learning_state()
    if state is None:
        return "Обучение: нет данных. Запустите: python -m stock_signal_analyzer.llm_learning"

    if not _LLM_LEARNING_ENABLED:
        llm_status = "⏸️ выключен (LLM_LEARNING=0)"
    elif state.llm_used:
        llm_status = f"✅ {state.llm_provider}"
    elif state.llm_provider:
        llm_status = f"❌ {state.llm_provider} (не ответил)"
    elif state.total_outcomes_analyzed < _MIN_OUTCOMES:
        llm_status = "⏳ мало данных (нужно 20+ сигналов для обучения)"
    else:
        llm_status = "❌ недоступен"

    comp_names = {
        "technical": "Технический анализ",
        "momentum": "Импульс",
        "news": "Новости",
        "volume": "Объём",
    }

    lines = [
        "📊 Отчёт об обучении",
        f"Проанализировано сигналов: {state.total_outcomes_analyzed}",
        f"Доля успешных: {state.win_rate*100:.1f}%",
        f"Средний плюс: +{state.avg_win_pct:.2f}% | Средний минус: {state.avg_loss_pct:.2f}%",
        f"Нейросеть (LLM): {llm_status}",
        f"Обновлено: {state.last_updated[:16]}",
        "",
    ]

    if state.weight_adjustments:
        lines.append("Насколько доверять каждому фактору:")
        for comp, mult in sorted(state.weight_adjustments.items()):
            arrow = "↑" if mult > 1.02 else ("↓" if mult < 0.98 else "→")
            name = comp_names.get(comp, comp)
            if mult > 1.02:
                comment = "доверять больше"
            elif mult < 0.98:
                comment = "доверять меньше"
            else:
                comment = "без изменений"
            lines.append(f"  {arrow} {name}: ×{mult:.2f} ({comment})")
        lines.append("")

    if state.ic_scores:
        lines.append("Предсказательная сила факторов (чем дальше от нуля, тем точнее):")
        for comp, ic in sorted(state.ic_scores.items(), key=lambda x: abs(x[1]), reverse=True):
            name = comp_names.get(comp, comp)
            strength = "сильная" if abs(ic) > 0.1 else ("средняя" if abs(ic) > 0.05 else "слабая")
            lines.append(f"  {name}: {ic:+.3f} ({strength})")
        lines.append("")

    if state.win_patterns:
        lines.append("Что работает (паттерны успеха):")
        for p in state.win_patterns[:3]:
            lines.append(f"  ✓ {html.escape(p)}")
        lines.append("")

    if state.loss_patterns:
        lines.append("Что не работает (паттерны неудачи):")
        for p in state.loss_patterns[:3]:
            lines.append(f"  ✗ {html.escape(p)}")
        lines.append("")

    if state.recommendations:
        lines.append("Советы:")
        for r in state.recommendations[:3]:
            lines.append(f"  • {html.escape(r)}")

    return "\n".join(lines)


# ── CLI entry point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

    import stenv
    stenv.load_project_env()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    force = "--force" in sys.argv
    state = run_learning_cycle(force=force)
    print()
    print(format_learning_report())
