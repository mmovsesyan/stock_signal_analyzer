"""
Торговый план: конкретный вход, стоп-лосс, цели, R:R, макс. удержание,
трейлинг-стоп, размер позиции, слипаж-буфер.

Все уровни рассчитываются по ATR(14) и направлению сигнала.
Множители стопа/цели зависят от класса качества (tier) и режима рынка (ADX).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from .market_regime import MarketRegime
from .technical import chandelier_stop


@dataclass
class TradePlan:
    direction: str  # "long" | "short" | "none"
    entry_price: float
    stop_price: float
    stop_pct: float
    target1_price: float
    target1_pct: float
    target2_price: float
    target2_pct: float
    risk_reward_1: float
    risk_reward_2: float
    max_hold_days: int
    trailing_activation_pct: float
    trailing_step_pct: float
    position_size_pct: float
    partial_exit_pct: float  # % позиции закрыть на T1 (обычно 50)
    plan_text: str
    # Chandelier trailing stop (Chuck LeBeau)
    chandelier_stop_price: float | None = None  # None = не рассчитан / неприменим
    chandelier_atr_mult: float = 3.0
    chandelier_lookback: int = 22


# Порог для генерации торгового плана: |score| < 0.25 = недостаточная чёткость сигнала
# Синхронизировано с порогом B-tier (0.26/0.24), чтобы B-класс получал полный план.
_DIR_THRESHOLD = 0.25
_SLIPPAGE_ATR_MULT = 0.3

# (stop_mult, target1_mult, target2_mult, hold_days_base, hold_days_trend)
# Цели скорректированы для обеспечения минимального R:R >= 1.5 ПОСЛЕ учёта slippage:
# Slippage = _SLIPPAGE_ATR_MULT * atr_abs добавляется к расстоянию стопа (risk увеличивается)
# Tier A: stop=1.2, t1=2.4 -> R:R = (2.4) / (1.2 + 0.3) = 1.60 >= 1.5 ✓
# Tier B: stop=1.5, t1=2.8 -> R:R = (2.8) / (1.5 + 0.3) = 1.56 >= 1.5 ✓
_TIER_PARAMS: dict[str, tuple[float, float, float, int, int]] = {
    "A": (1.2, 2.4, 3.6, 5, 10),
    "B": (1.5, 2.8, 4.0, 5, 7),
}
_DEFAULT_PARAMS = (1.5, 2.8, 4.0, 5, 5)

# ADX 16-20: стопы шире, цели уже (боковик опасен)
# Минимальное масштабирование цели 0.90 чтобы R:R не упал ниже 1.0
_SIDEWAYS_STOP_SCALE = 1.2
_SIDEWAYS_TARGET_SCALE = 0.90

# Минимальный R:R для генерации торгового плана
_MIN_RR = 1.5
_MIN_RR_C_CLASS = 1.2  # для C классов (наблюдение)


def _position_size(confidence: float, atr_pct: float | None, market_regime: MarketRegime | None = None) -> float:
    """Рекомендуемый размер позиции (% капитала) на основе 1% риска на сделку.

    Формула: позиция = 1% / (1.5 × ATR%) × 100
    При ATR=2%, стоп=3% → позиция ≈ 33% капитала.
    При низкой confidence уменьшаем, при отсутствии ATR — консервативно.
    В sideways режиме уменьшаем размер позиции.
    """
    base_risk_pct = 1.0

    if atr_pct is None or atr_pct <= 0:
        return 10.0 if confidence >= 0.5 else 5.0

    stop_distance = 1.5 * atr_pct
    raw = base_risk_pct / stop_distance * 100.0

    if confidence < 0.30:
        return 5.0
    conf_scale = 0.5 + 0.5 * confidence

    # Reduce size in sideways markets
    if market_regime is not None and market_regime.regime == "sideways":
        conf_scale *= 0.85

    return float(np.clip(raw * conf_scale, 5.0, 100.0))


def _none_plan(ref_price: float, reason: str) -> TradePlan:
    """План без направления (не торговать)."""
    return TradePlan(
        direction="none",
        entry_price=ref_price,
        stop_price=ref_price,
        stop_pct=0.0,
        target1_price=ref_price,
        target1_pct=0.0,
        target2_price=ref_price,
        target2_pct=0.0,
        risk_reward_1=0.0,
        risk_reward_2=0.0,
        max_hold_days=0,
        trailing_activation_pct=0.0,
        trailing_step_pct=0.0,
        position_size_pct=0.0,
        partial_exit_pct=0.0,
        plan_text=reason,
    )


def _simple_plan(ref_price: float, atr_pct: float, score: float, tier: str, confidence: float = 0.3, adx14: float = 20.0, has_pattern: bool = False, hist: pd.DataFrame | None = None) -> TradePlan:
    """
    Упрощённый торговый план для C классов и слабых сигналов.
    Генерирует минимальный план для проверки исходов.

    Args:
        ref_price: Цена входа
        atr_pct: ATR в процентах
        score: Итоговый score сигнала
        tier: Класс сигнала (A/B/C)
        confidence: Уверенность
        adx14: Значение ADX(14) для проверки боковика
        has_pattern: Есть ли паттерн на графике
    """
    direction = "long" if score > 0 else "short"

    # Консервативные множители для C классов
    stop_mult = 1.2
    t1_mult = 2.0
    t2_mult = 3.0
    max_hold = 3  # короткое удержание для C классов

    # ADX 16-20 без тренда: шире стопы, уже цели
    # Для C классов используем упрощённые множители (без боковик-коррекции)
    if tier != "C" and adx14 < 20.0:
        stop_mult *= _SIDEWAYS_STOP_SCALE
        t1_mult *= _SIDEWAYS_TARGET_SCALE
        t2_mult *= _SIDEWAYS_TARGET_SCALE

    atr_abs = ref_price * atr_pct / 100.0
    sign = 1.0 if direction == "long" else -1.0
    slippage = 0.3 * atr_abs

    stop_price = ref_price - sign * (stop_mult * atr_abs + slippage)
    target1_price = ref_price + sign * (t1_mult * atr_abs)
    target2_price = ref_price + sign * (t2_mult * atr_abs)

    stop_pct = (stop_price / ref_price - 1.0) * 100.0
    target1_pct = (target1_price / ref_price - 1.0) * 100.0
    target2_pct = (target2_price / ref_price - 1.0) * 100.0

    risk = abs(ref_price - stop_price)
    rr1 = abs(target1_price - ref_price) / risk if risk > 0 else 0
    rr2 = abs(target2_price - ref_price) / risk if risk > 0 else 0

    # Проверяем минимальный R:R — если не достигается, возвращаем none_plan
    min_rr = _MIN_RR_C_CLASS if tier == "C" else _MIN_RR
    if rr1 + 1e-9 < min_rr:
        return _none_plan(
            ref_price,
            f"Нет торгового плана (R:R={rr1:.2f} < {min_rr:.1f} — риск/доходность неприемлемы).",
        )

    pos_size = 10.0 if tier == "C" else max(5.0, confidence * 20.0)

    plan_text = (
        f"C класс (наблюдение) — упрощённый план.\n"
        f"Для C классов: короткое удержание ({max_hold} дней), небольшая позиция ({pos_size:.0f}%)."
    )

    chand_stop = chandelier_stop(hist, direction) if hist is not None else None

    return TradePlan(
        direction=direction,
        entry_price=round(ref_price, 4),
        stop_price=round(stop_price, 4),
        stop_pct=round(stop_pct, 2),
        target1_price=round(target1_price, 4),
        target1_pct=round(target1_pct, 2),
        target2_price=round(target2_price, 4),
        target2_pct=round(target2_pct, 2),
        risk_reward_1=round(rr1, 2),
        risk_reward_2=round(rr2, 2),
        max_hold_days=max_hold,
        trailing_activation_pct=round(atr_pct, 2),
        trailing_step_pct=round(atr_pct, 2),
        position_size_pct=round(pos_size, 0),
        partial_exit_pct=50.0,
        plan_text=plan_text,
        chandelier_stop_price=round(chand_stop, 4) if chand_stop is not None else None,
    )


def build_trade_plan(
    score: float,
    ref_price: float,
    atr_pct: float | None,
    signal_tier: str,
    adx14: float,
    symbol: str = "",
    confidence: float = 0.5,
    has_pattern: bool = False,
    nearest_support: float | None = None,
    nearest_resistance: float | None = None,
    institutional_size_pct: float | None = None,
    vol_regime: str = "normal",
    market_regime: MarketRegime | None = None,
    hist: pd.DataFrame | None = None,
) -> TradePlan:
    if atr_pct is None or atr_pct <= 0 or ref_price <= 0:
        return _none_plan(ref_price, "Нет торгового плана (нет ATR или нулевая цена).")

    # --- Генерация торгового плана для всех классов (A/B/C) ---
    # Для C классов генерируем упрощённый план для проверки исходов
    if abs(score) < _DIR_THRESHOLD:
        # Слабый сигнал — всё ещё генерируем план, но с осторожными параметрами
        return _simple_plan(ref_price, atr_pct, score, signal_tier, confidence, adx14, has_pattern, hist)

    # --- Фильтр боковика ---
    if adx14 < 16.0 and not has_pattern:
        return _none_plan(
            ref_price,
            f"Нет торгового плана (ADX={adx14:.0f} < 16, нет паттерна — боковик, стопы выбьет).",
        )

    direction = "long" if score > 0 else "short"
    stop_m, t1_m, t2_m, hold_base, hold_trend = _TIER_PARAMS.get(signal_tier, _DEFAULT_PARAMS)

    # ADX 16-20 без тренда: шире стопы, уже цели
    if adx14 < 20.0:
        stop_m *= _SIDEWAYS_STOP_SCALE
        t1_m *= _SIDEWAYS_TARGET_SCALE
        t2_m *= _SIDEWAYS_TARGET_SCALE

    # Vol regime: в crisis/high расширяем стопы, сжимаем цели (Bridgewater)
    if vol_regime == "crisis":
        stop_m *= 1.5
        t1_m *= 0.6
        t2_m *= 0.6
    elif vol_regime == "high":
        stop_m *= 1.2
        t1_m *= 0.8
        t2_m *= 0.8

    # Directional regime adjustments
    if market_regime is not None:
        if market_regime.regime == "bull":
            stop_m *= 0.95
            t1_m *= 1.10
            t2_m *= 1.10
        elif market_regime.regime == "bear":
            stop_m *= 1.10
            t1_m *= 0.95
            t2_m *= 0.95

    # ── Масштабирование по абсолютному ATR% ──
    # Для высоковолатильных бумаг (ATR > 3%) сужаем множители,
    # чтобы стоп в абсолюте не был слишком далеко.
    # Для низковолатильных (ATR < 1%) расширяем, чтобы не выбивало шумом.
    if atr_pct > 4.0:
        atr_scale = 3.5 / atr_pct  # сжатие
        stop_m *= max(0.6, atr_scale)
        t1_m *= max(0.6, atr_scale)
        t2_m *= max(0.6, atr_scale)
    elif atr_pct > 3.0:
        atr_scale = 3.0 / atr_pct
        stop_m *= max(0.75, atr_scale)
        t1_m *= max(0.75, atr_scale)
        t2_m *= max(0.75, atr_scale)
    elif atr_pct < 0.8:
        atr_scale = 0.8 / atr_pct
        stop_m *= min(1.5, atr_scale)
        t1_m *= min(1.4, atr_scale)
        t2_m *= min(1.4, atr_scale)

    atr_abs = ref_price * atr_pct / 100.0
    sign = 1.0 if direction == "long" else -1.0

    # Slippage buffer: стоп отодвигается от входа на 0.3 x ATR
    slippage = _SLIPPAGE_ATR_MULT * atr_abs

    atr_stop = ref_price - sign * (stop_m * atr_abs + slippage)

    # Use support/resistance level as stop if it's tighter than ATR-based stop
    # but not too close (at least 0.3×ATR from entry)
    min_stop_dist = 0.3 * atr_abs
    stop_price = atr_stop
    if direction == "long" and nearest_support is not None:
        level_stop = nearest_support - slippage
        if level_stop < ref_price - min_stop_dist and level_stop > atr_stop:
            stop_price = level_stop
    elif direction == "short" and nearest_resistance is not None:
        level_stop = nearest_resistance + slippage
        if level_stop > ref_price + min_stop_dist and level_stop < atr_stop:
            stop_price = level_stop

    target1_price = ref_price + sign * t1_m * atr_abs
    target2_price = ref_price + sign * t2_m * atr_abs

    stop_pct = (stop_price / ref_price - 1.0) * 100.0
    target1_pct = (target1_price / ref_price - 1.0) * 100.0
    target2_pct = (target2_price / ref_price - 1.0) * 100.0

    risk = abs(ref_price - stop_price)
    if risk <= 0:
        return _none_plan(ref_price, "Нет торгового плана (нулевой риск, стоп совпадает с ценой входа).")
    rr1 = abs(target1_price - ref_price) / risk
    rr2 = abs(target2_price - ref_price) / risk

    # Проверяем минимальный R:R — если не достигается, план не генерируем
    min_rr = _MIN_RR_C_CLASS if signal_tier == "C" else _MIN_RR
    if rr1 + 1e-9 < min_rr:
        return _none_plan(
            ref_price,
            f"Нет торгового плана (R:R={rr1:.2f} < {min_rr:.1f} — риск/доходность неприемлемы).",
        )

    max_hold = hold_trend if adx14 > 25.0 else hold_base
    if market_regime is not None and market_regime.regime == "bull" and adx14 > 25.0:
        max_hold += 3  # extended hold in strong bull

    # Trailing stop: после +1xATR стоп на безубыток, после +2xATR стоп на +1xATR
    trail_activation = atr_pct  # +1 x ATR(14)% от входа
    trail_step = atr_pct  # каждый шаг = 1 x ATR

    pos_size = institutional_size_pct if institutional_size_pct is not None else _position_size(confidence, atr_pct, market_regime)

    partial_exit = 50.0

    # Chandelier trailing stop
    chand_stop = chandelier_stop(hist, direction) if hist is not None else None

    plan_text = _format_plan(
        direction=direction,
        symbol=symbol,
        entry=ref_price,
        stop=stop_price,
        stop_pct=stop_pct,
        t1=target1_price,
        t1_pct=target1_pct,
        rr1=rr1,
        t2=target2_price,
        t2_pct=target2_pct,
        rr2=rr2,
        hold=max_hold,
        tier=signal_tier,
        pos_size=pos_size,
        trail_act_pct=trail_activation,
        partial_exit=partial_exit,
        chandelier_stop=chand_stop,
    )

    return TradePlan(
        direction=direction,
        entry_price=round(ref_price, 4),
        stop_price=round(stop_price, 4),
        stop_pct=round(stop_pct, 2),
        target1_price=round(target1_price, 4),
        target1_pct=round(target1_pct, 2),
        target2_price=round(target2_price, 4),
        target2_pct=round(target2_pct, 2),
        risk_reward_1=round(rr1, 2),
        risk_reward_2=round(rr2, 2),
        max_hold_days=max_hold,
        trailing_activation_pct=round(trail_activation, 2),
        trailing_step_pct=round(trail_step, 2),
        position_size_pct=round(pos_size, 0),
        partial_exit_pct=partial_exit,
        plan_text=plan_text,
        chandelier_stop_price=round(chand_stop, 4) if chand_stop is not None else None,
    )


def _no_plan_text(tier: str) -> str:
    if tier == "C":
        return "Нет торгового плана (класс C — наблюдение)."
    return "Нет торгового плана (|score| < порога или нет ATR)."


def _format_plan(
    *,
    direction: str,
    symbol: str,
    entry: float,
    stop: float,
    stop_pct: float,
    t1: float,
    t1_pct: float,
    rr1: float,
    t2: float,
    t2_pct: float,
    rr2: float,
    hold: int,
    tier: str,
    pos_size: float,
    trail_act_pct: float,
    partial_exit: float,
    chandelier_stop: float | None = None,
) -> str:
    d = "LONG" if direction == "long" else "SHORT"
    sym = symbol or "—"
    chand_line = ""
    if chandelier_stop is not None:
        chand_line = f"Chandelier стоп: {chandelier_stop:.2f}\n"
    return (
        f"{d} {sym} @ {entry:.2f}\n"
        f"Стоп: {stop:.2f} ({stop_pct:+.2f}%)\n"
        f"{chand_line}"
        f"Цель 1: {t1:.2f} ({t1_pct:+.2f}%)  R:R {rr1:.1f} — закрыть {partial_exit:.0f}%\n"
        f"Цель 2: {t2:.2f} ({t2_pct:+.2f}%)  R:R {rr2:.1f} — остаток\n"
        f"Трейлинг: после {trail_act_pct:+.1f}% стоп на безубыток\n"
        f"Удержание: до {hold} дней  |  Позиция: {pos_size:.0f}% капитала\n"
        f"Класс: {tier}"
    )


def trade_plan_to_dict(tp: TradePlan) -> dict[str, Any]:
    """Поля для signal_log JSONL."""
    return {
        "tp_direction": tp.direction,
        "tp_entry": tp.entry_price,
        "tp_stop": tp.stop_price,
        "tp_stop_pct": tp.stop_pct,
        "tp_target1": tp.target1_price,
        "tp_target1_pct": tp.target1_pct,
        "tp_target2": tp.target2_price,
        "tp_target2_pct": tp.target2_pct,
        "tp_rr1": tp.risk_reward_1,
        "tp_rr2": tp.risk_reward_2,
        "tp_max_hold_days": tp.max_hold_days,
        "tp_trailing_act_pct": tp.trailing_activation_pct,
        "tp_trailing_step_pct": tp.trailing_step_pct,
        "tp_position_size_pct": tp.position_size_pct,
        "tp_partial_exit_pct": tp.partial_exit_pct,
        "tp_chandelier_stop": tp.chandelier_stop_price,
    }
