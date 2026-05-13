"""
Торговый план: конкретный вход, стоп-лосс, цели, R:R, макс. удержание,
трейлинг-стоп, размер позиции, слипаж-буфер.

Все уровни рассчитываются по ATR(14) и направлению сигнала.
Множители стопа/цели зависят от класса качества (tier) и режима рынка (ADX).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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


_DIR_THRESHOLD = 0.15
_SLIPPAGE_ATR_MULT = 0.3

# (stop_mult, target1_mult, target2_mult, hold_days_base, hold_days_trend)
_TIER_PARAMS: dict[str, tuple[float, float, float, int, int]] = {
    "A": (1.2, 2.0, 3.0, 5, 10),
    "B": (1.5, 2.0, 2.8, 5, 7),
}
_DEFAULT_PARAMS = (1.5, 2.0, 2.5, 5, 5)

# ADX 16-20: стопы шире, цели уже (боковик опасен, R:R хуже)
_SIDEWAYS_STOP_SCALE = 1.3
_SIDEWAYS_TARGET_SCALE = 0.75


def _position_size(confidence: float) -> float:
    """Размер позиции 0..100% на основе уверенности сигнала."""
    if confidence < 0.30:
        return 25.0
    if confidence < 0.45:
        return 50.0
    if confidence < 0.60:
        return 75.0
    return 100.0


def _none_plan(ref_price: float, reason: str) -> TradePlan:
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
) -> TradePlan:
    if abs(score) < _DIR_THRESHOLD or atr_pct is None or atr_pct <= 0 or ref_price <= 0:
        return _none_plan(ref_price, _no_plan_text(signal_tier))

    if signal_tier == "C":
        return _none_plan(ref_price, "Нет торгового плана (класс C — наблюдение).")

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
    rr1 = abs(target1_price - ref_price) / risk if risk > 0 else 0.0
    rr2 = abs(target2_price - ref_price) / risk if risk > 0 else 0.0

    max_hold = hold_trend if adx14 > 25.0 else hold_base

    # Trailing stop: после +1xATR стоп на безубыток, после +2xATR стоп на +1xATR
    trail_activation = atr_pct  # +1 x ATR(14)% от входа
    trail_step = atr_pct  # каждый шаг = 1 x ATR

    pos_size = institutional_size_pct if institutional_size_pct is not None else _position_size(confidence)

    partial_exit = 50.0

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
) -> str:
    d = "LONG" if direction == "long" else "SHORT"
    sym = symbol or "—"
    return (
        f"{d} {sym} @ {entry:.2f}\n"
        f"Стоп: {stop:.2f} ({stop_pct:+.2f}%)\n"
        f"Цель 1: {t1:.2f} ({t1_pct:+.2f}%)  R:R {rr1:.1f} — закрыть {partial_exit:.0f}%\n"
        f"Цель 2: {t2:.2f} ({t2_pct:+.2f}%)  R:R {rr2:.1f} — остаток\n"
        f"Трейлинг: после {trail_act_pct:+.1f}% стоп на безубыток\n"
        f"Удержание: до {hold} дней  |  Позиция: {pos_size:.0f}%\n"
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
    }
