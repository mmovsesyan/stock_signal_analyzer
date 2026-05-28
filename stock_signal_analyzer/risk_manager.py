"""
Институциональный риск-менеджмент.

1. Kelly Criterion — оптимальный размер позиции (f* = edge / variance)
2. Volatility Targeting — нормализация позиции по vol (risk parity)
3. Correlation Filter — отсечение коррелированных позиций в портфеле
4. Circuit Breaker — авто-снижение риска при просадке (drawdown control)
5. Adaptive Position Sizing — объединение Kelly + vol target + drawdown
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time as _time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

_log = logging.getLogger(__name__)
_lock = threading.Lock()


# ── 1. Kelly Criterion ───────────────────────────────────────────────────────

@dataclass
class KellyResult:
    full_kelly: float       # f* = p/a - q/b (raw Kelly fraction)
    half_kelly: float       # f*/2 — практикуемый фондами вариант
    edge: float             # ожидаемое преимущество (expected excess return)
    detail: str


def kelly_criterion(
    win_rate: float,
    avg_win: float,
    avg_loss: float,
) -> KellyResult:
    """
    Критерий Келли: f* = (p * b - q) / b
    где p = win_rate, q = 1-p, b = avg_win/avg_loss.

    Фонды используют half-Kelly (f*/2) для устойчивости к ошибкам оценки.
    Без исторических данных: используются консервативные defaults из confidence.
    """
    if avg_loss <= 0 or win_rate <= 0 or win_rate >= 1.0:
        return KellyResult(
            full_kelly=0.0, half_kelly=0.0, edge=0.0,
            detail="Kelly: недостаточно данных.",
        )

    p = win_rate
    q = 1.0 - p
    b = avg_win / avg_loss

    full_f = (p * b - q) / b
    full_f = max(0.0, full_f)
    half_f = full_f / 2.0

    edge = p * avg_win - q * avg_loss

    detail = (
        f"Kelly: WR={p:.0%}, avg_win/loss={b:.2f}, "
        f"f*={full_f:.1%}, half-Kelly={half_f:.1%}, edge={edge:+.3f}"
    )

    return KellyResult(
        full_kelly=float(np.clip(full_f, 0.0, 1.0)),
        half_kelly=float(np.clip(half_f, 0.0, 0.5)),
        edge=edge,
        detail=detail,
    )


def kelly_from_confidence(confidence: float, signal_strength: float, tier: str = "C") -> KellyResult:
    """
    Proxy Kelly, когда нет бэктест-статистики.
    Оценка win_rate из confidence + |signal| + tier.
    """
    # Tier bonus: A-класс = более высокая оценка WR
    tier_bonus = {"A": 0.08, "B": 0.04, "C": 0.0}.get(tier, 0.0)
    base_wr = 0.50 + 0.20 * confidence + tier_bonus
    strength_bonus = min(0.12, abs(signal_strength) * 0.15)
    est_wr = float(np.clip(base_wr + strength_bonus, 0.40, 0.75))

    est_rr = 1.8 + 0.7 * confidence
    return kelly_criterion(est_wr, est_rr, 1.0)


# ── Kelly caching ─────────────────────────────────────────────────────────────
_kelly_cache: tuple[KellyResult | None, float] | None = None
_KELLY_CACHE_TTL_SEC = 3600.0  # 1 hour


def kelly_from_outcomes(outcomes_path: str | None = None, min_trades: int = 30) -> KellyResult | None:
    """
    Рассчитать Kelly из реальной статистики сделок.

    Читает outcomes.jsonl и вычисляет:
    - Реальный win rate
    - Средний выигрыш / средний проигрыш
    - Kelly fraction

    Результат кэшируется на 1 час чтобы избежать повторного чтения файла.
    Возвращает None, если недостаточно сделок (< min_trades).
    """
    global _kelly_cache
    if _kelly_cache is not None:
        cached_result, cached_ts = _kelly_cache
        if _time.time() - cached_ts < _KELLY_CACHE_TTL_SEC:
            return cached_result

    path = outcomes_path or os.path.join(
        os.environ.get("STOCK_SIGNAL_DATA", "/var/lib/stock_signal_analyzer"),
        "outcomes.jsonl"
    )

    if not os.path.exists(path):
        _kelly_cache = (None, _time.time())
        return None

    pnl_values = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                    outcome = r.get("outcome", "")
                    pnl = r.get("pnl_pct")
                    if outcome in ("win_t1", "win_t2", "loss") and pnl is not None:
                        pnl_values.append(float(pnl))
                except (json.JSONDecodeError, ValueError):
                    continue
    except OSError:
        _kelly_cache = (None, _time.time())
        return None

    if len(pnl_values) < min_trades:
        _kelly_cache = (None, _time.time())
        return None

    wins = [p for p in pnl_values if p > 0]
    losses = [abs(p) for p in pnl_values if p < 0]

    if not wins or not losses:
        _kelly_cache = (None, _time.time())
        return None

    win_rate = len(wins) / len(pnl_values)
    avg_win = sum(wins) / len(wins)
    avg_loss = sum(losses) / len(losses)

    result = kelly_criterion(win_rate, avg_win, avg_loss)
    _kelly_cache = (result, _time.time())
    return result


# ── 2. Volatility Targeting ──────────────────────────────────────────────────

_TARGET_VOL_ANNUAL = 0.15  # 15% target annualized vol (institutional standard)


def vol_target_position_pct(
    current_vol_annual: float,
    target_vol: float = _TARGET_VOL_ANNUAL,
) -> float:
    """
    Risk parity / vol targeting: позиция обратно пропорциональна волатильности.
    Если текущий vol = 30%, а цель = 15%, то позиция = 50% от базовой.
    """
    if current_vol_annual <= 0:
        return 100.0
    raw = target_vol / current_vol_annual
    return float(np.clip(raw * 100.0, 15.0, 200.0))


# ── 3. Correlation Filter ────────────────────────────────────────────────────

@dataclass
class CorrelationCheck:
    max_corr: float            # максимальная корреляция с открытыми позициями
    correlated_with: str       # тикер с наибольшей корреляцией
    should_reduce: bool        # True если корреляция > порога
    position_scale: float      # 0..1 (1.0 = полная, <1 = уменьшить)
    detail: str


def check_correlation(
    target_close: pd.Series,
    portfolio_closes: dict[str, pd.Series],
    threshold: float = 0.75,
    lookback: int = 60,
) -> CorrelationCheck:
    """
    Проверяет корреляцию нового сигнала с открытыми позициями.
    Фонды (Citadel, DE Shaw) отсекают позиции с корреляцией > 0.7-0.8.
    """
    if not portfolio_closes:
        return CorrelationCheck(
            max_corr=0.0, correlated_with="",
            should_reduce=False, position_scale=1.0,
            detail="Нет открытых позиций — корреляция не ограничивает.",
        )

    target = target_close.astype(float).dropna()
    if len(target) < lookback:
        return CorrelationCheck(
            max_corr=0.0, correlated_with="",
            should_reduce=False, position_scale=1.0,
            detail="Мало данных для корреляции.",
        )

    t_ret = target.pct_change().dropna().iloc[-lookback:]
    best_corr = 0.0
    best_sym = ""

    for sym, pc in portfolio_closes.items():
        p = pc.astype(float).dropna()
        if len(p) < lookback:
            continue
        p_ret = p.pct_change().dropna().iloc[-lookback:]
        common = t_ret.index.intersection(p_ret.index)
        if len(common) < 20:
            continue
        corr = float(t_ret.loc[common].corr(p_ret.loc[common]))
        if abs(corr) > abs(best_corr):
            best_corr = corr
            best_sym = sym

    should_reduce = abs(best_corr) > threshold
    if should_reduce:
        scale = float(np.clip(1.0 - (abs(best_corr) - threshold) * 2.0, 0.3, 0.8))
    else:
        scale = 1.0

    detail = (
        f"Max corr: {best_corr:.2f} с {best_sym}"
        if best_sym
        else "Корреляция не определена."
    )
    if should_reduce:
        detail += f" → снижение до {scale:.0%}"

    return CorrelationCheck(
        max_corr=best_corr,
        correlated_with=best_sym,
        should_reduce=should_reduce,
        position_scale=scale,
        detail=detail,
    )


# ── 4. Circuit Breaker / Drawdown Control ────────────────────────────────────

_DRAWDOWN_FILE_ENV = "SSA_DRAWDOWN_STATE"


@dataclass
class DrawdownState:
    peak_equity: float = 10000.0
    current_equity: float = 10000.0
    max_drawdown_pct: float = 0.0
    consecutive_losses: int = 0
    risk_level: str = "normal"       # "normal" | "reduced" | "halt"
    risk_multiplier: float = 1.0     # множитель позиции
    detail: str = ""


def _dd_state_path() -> str | None:
    return os.environ.get(_DRAWDOWN_FILE_ENV) or os.environ.get("SSA_SIGNAL_LOG", "").replace(
        ".jsonl", "_drawdown.json"
    ) or None


def load_drawdown_state() -> DrawdownState:
    path = _dd_state_path()
    if not path or not os.path.exists(path):
        return DrawdownState()
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return DrawdownState(
            peak_equity=d.get("peak_equity", 10000.0),
            current_equity=d.get("current_equity", 10000.0),
            max_drawdown_pct=d.get("max_drawdown_pct", 0.0),
            consecutive_losses=d.get("consecutive_losses", 0),
        )
    except Exception:
        return DrawdownState()


def save_drawdown_state(state: DrawdownState) -> None:
    path = _dd_state_path()
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "peak_equity": state.peak_equity,
                "current_equity": state.current_equity,
                "max_drawdown_pct": state.max_drawdown_pct,
                "consecutive_losses": state.consecutive_losses,
                "updated_utc": datetime.now(timezone.utc).isoformat(),
            }, f)
    except Exception as e:
        _log.warning("save_drawdown_state: %s", e)


def evaluate_drawdown(state: DrawdownState | None = None) -> DrawdownState:
    """
    Правила circuit breaker (применяют все крупные фонды):
    - Drawdown 5-10%: уменьшить размер позиций на 30-50%
    - Drawdown > 15%: остановить торговлю (halt)
    - 5+ consecutive losses: уменьшить на 40%
    - 3+ consecutive losses: уменьшить на 20%
    """
    if state is None:
        state = load_drawdown_state()

    if state.peak_equity <= 0:
        state.peak_equity = state.current_equity

    dd = 0.0
    if state.peak_equity > 0:
        dd = (state.peak_equity - state.current_equity) / state.peak_equity * 100.0
    dd = max(0.0, dd)
    state.max_drawdown_pct = max(state.max_drawdown_pct, dd)

    mult = 1.0
    level = "normal"

    if dd >= 15.0:
        mult = 0.0
        level = "halt"
    elif dd >= 10.0:
        mult = 0.3
        level = "reduced"
    elif dd >= 5.0:
        mult = 0.6
        level = "reduced"

    if state.consecutive_losses >= 5:
        mult = min(mult, 0.4)
        level = "reduced" if level == "normal" else level
    elif state.consecutive_losses >= 3:
        mult = min(mult, 0.7)

    state.risk_level = level
    state.risk_multiplier = mult

    parts = [f"DD={dd:.1f}%"]
    if state.consecutive_losses > 0:
        parts.append(f"серия убытков: {state.consecutive_losses}")
    parts.append(f"risk: {level} (×{mult:.2f})")
    state.detail = "Circuit breaker: " + ", ".join(parts)

    return state


# ── 5. Composite Position Sizing ─────────────────────────────────────────────

@dataclass
class PositionSizeResult:
    base_pct: float           # базовый % (от confidence)
    kelly_pct: float          # Kelly-scaled
    vol_adjusted_pct: float   # vol-target adjusted
    drawdown_adjusted_pct: float  # после circuit breaker
    final_pct: float          # итоговый размер позиции
    detail: str


def compute_position_size(
    confidence: float,
    signal_strength: float,
    current_vol_annual: float,
    drawdown_state: DrawdownState | None = None,
    regime_risk_mult: float = 1.0,
    tier: str = "C",
) -> PositionSizeResult:
    """
    Институциональный sizing: Kelly × vol_target × circuit_breaker × regime × tier.
    Использует реальный Kelly из outcomes.jsonl если есть ≥30 сделок.
    """
    # Пробуем реальный Kelly из статистики сделок
    real_kelly = kelly_from_outcomes()
    if real_kelly is not None:
        kelly = real_kelly
        kelly_note = "real"
    else:
        # Fallback на confidence-based proxy
        kelly = kelly_from_confidence(confidence, signal_strength, tier=tier)
        kelly_note = "proxy"

    kelly_pct = kelly.half_kelly * 100.0

    vol_pct = vol_target_position_pct(current_vol_annual) if current_vol_annual > 0 else 100.0

    dd_state = evaluate_drawdown(drawdown_state)
    dd_mult = dd_state.risk_multiplier

    # Tier-based base sizing: A=агрессивнее, C=консервативнее
    tier_mult = {"A": 1.0, "B": 0.85, "C": 0.6}.get(tier, 0.6)
    base = (25.0 + 75.0 * confidence) * tier_mult
    combined = min(base, kelly_pct, vol_pct) * dd_mult * regime_risk_mult
    final = float(np.clip(combined, 5.0, 100.0))

    detail = (
        f"Size: base={base:.0f}%, Kelly½={kelly_pct:.0f}% ({kelly_note}), "
        f"vol_target={vol_pct:.0f}%, DD×{dd_mult:.2f}, "
        f"regime×{regime_risk_mult:.2f} → {final:.0f}%"
    )

    return PositionSizeResult(
        base_pct=base,
        kelly_pct=kelly_pct,
        vol_adjusted_pct=vol_pct,
        drawdown_adjusted_pct=dd_mult * 100,
        final_pct=final,
        detail=detail,
    )
