#!/usr/bin/env python3
"""
Бэктестер: проверка торговых планов из SSA_SIGNAL_LOG на исторических данных.

Метрики: win rate, PnL, profit factor, Sharpe, expectancy, avg win/loss,
consecutive streaks, max drawdown, monthly breakdown.

Пример:
  python tools/backtest.py signals.jsonl
  python tools/backtest.py signals.jsonl --min-tier A
  python tools/backtest.py signals.jsonl --target 1
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import yfinance as yf


@dataclass
class TradeResult:
    symbol: str
    direction: str
    tier: str
    entry: float
    stop: float
    target: float
    exit_price: float
    exit_reason: str
    pnl_pct: float
    hold_days: int
    ts_utc: str


@dataclass
class Stats:
    total: int = 0
    wins: int = 0
    losses: int = 0
    longs: int = 0
    shorts: int = 0
    pnl_list: list[float] = field(default_factory=list)
    results: list[TradeResult] = field(default_factory=list)
    by_tier: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    by_month: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))


def _load_signals(path: str, min_tier: str | None) -> list[dict[str, Any]]:
    valid_tiers = {"A", "B", "C"}
    if min_tier:
        tier_order = ["A", "B", "C"]
        cut = tier_order.index(min_tier) if min_tier in tier_order else len(tier_order)
        valid_tiers = set(tier_order[: cut + 1])

    signals: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            d = row.get("tp_direction") or row.get("direction", "")
            if d not in ("long", "short"):
                continue
            tier = str(row.get("signal_tier", "C"))
            if tier not in valid_tiers:
                continue
            signals.append(row)
    return signals


def _simulate_trade(
    row: dict[str, Any],
    target_n: int = 1,
    commission_pct: float = 0.1,
) -> TradeResult | None:
    """Симуляция сделки.

    Улучшения vs v1:
    - Вход по Open следующего дня (реалистичнее, чем по цене сигнала).
    - Комиссия (commission_pct × 2: вход + выход) вычитается из PnL.
    - Если в один день сработали и стоп, и цель — считаем стоп
      (worst-case; без интрадей-данных нельзя определить порядок).
    """
    symbol = row.get("symbol", "")
    direction = row.get("tp_direction") or row.get("direction", "")
    signal_entry = float(row.get("tp_entry") or row.get("ref_price", 0))
    initial_stop = float(row.get("tp_stop", 0))
    if target_n == 2:
        target = float(row.get("tp_target2") or row.get("tp_target1", 0))
    else:
        target = float(row.get("tp_target1", 0))
    max_hold = int(row.get("tp_max_hold_days", 5))
    tier = str(row.get("signal_tier", "C"))
    ts_utc = str(row.get("ts_utc", ""))

    trail_act_pct = float(row.get("tp_trailing_act_pct", 0))
    trail_step_pct = float(row.get("tp_trailing_step_pct", 0))

    if not symbol or signal_entry <= 0 or initial_stop <= 0 or target <= 0:
        return None

    try:
        dt_signal = datetime.fromisoformat(ts_utc.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None

    # Загружаем на 1 день больше: первый день — для Open (реальный вход)
    start = (dt_signal + timedelta(days=1)).strftime("%Y-%m-%d")
    end = (dt_signal + timedelta(days=max_hold + 7)).strftime("%Y-%m-%d")

    try:
        hist = yf.Ticker(symbol).history(start=start, end=end, interval="1d", auto_adjust=True)
    except Exception:
        return None

    if hist is None or hist.empty:
        return None

    # ── Реалистичный вход: Open следующего торгового дня ──
    actual_entry = float(hist.iloc[0].get("Open", 0))
    if actual_entry <= 0:
        actual_entry = signal_entry  # fallback

    # Пересчитать стоп/цель пропорционально сдвигу входа
    entry_ratio = actual_entry / signal_entry if signal_entry > 0 else 1.0
    stop = initial_stop * entry_ratio
    initial_stop_adj = stop
    target = target * entry_ratio
    entry = actual_entry

    trail_act_abs = entry * trail_act_pct / 100.0 if trail_act_pct > 0 else 0.0
    trail_step_abs = entry * trail_step_pct / 100.0 if trail_step_pct > 0 else 0.0

    round_trip_commission = commission_pct * 2.0  # вход + выход

    for i in range(min(max_hold, len(hist))):
        day = hist.iloc[i]
        h = float(day.get("High", 0))
        lo = float(day.get("Low", 0))

        if direction == "long":
            if trail_act_abs > 0:
                excursion = h - entry
                if excursion >= 2.0 * trail_act_abs:
                    stop = max(stop, entry + trail_step_abs)
                elif excursion >= trail_act_abs:
                    stop = max(stop, entry)

            stop_hit = lo <= stop
            target_hit = h >= target

            if stop_hit:
                pnl = (stop / entry - 1.0) * 100.0 - round_trip_commission
                reason = "trail" if stop > initial_stop_adj else "stop"
                return TradeResult(symbol, direction, tier, entry, initial_stop_adj, target, stop, reason, pnl, i + 1, ts_utc)
            if target_hit:
                pnl = (target / entry - 1.0) * 100.0 - round_trip_commission
                return TradeResult(symbol, direction, tier, entry, initial_stop_adj, target, target, "target", pnl, i + 1, ts_utc)
        else:
            if trail_act_abs > 0:
                excursion = entry - lo
                if excursion >= 2.0 * trail_act_abs:
                    stop = min(stop, entry - trail_step_abs)
                elif excursion >= trail_act_abs:
                    stop = min(stop, entry)

            stop_hit = h >= stop
            target_hit = lo <= target

            if stop_hit:
                pnl = (1.0 - stop / entry) * 100.0 - round_trip_commission
                reason = "trail" if stop < initial_stop_adj else "stop"
                return TradeResult(symbol, direction, tier, entry, initial_stop_adj, target, stop, reason, pnl, i + 1, ts_utc)
            if target_hit:
                pnl = (1.0 - target / entry) * 100.0 - round_trip_commission
                return TradeResult(symbol, direction, tier, entry, initial_stop_adj, target, target, "target", pnl, i + 1, ts_utc)

    last_close = float(hist["Close"].iloc[min(max_hold - 1, len(hist) - 1)])
    if direction == "long":
        pnl = (last_close / entry - 1.0) * 100.0 - round_trip_commission
    else:
        pnl = (1.0 - last_close / entry) * 100.0 - round_trip_commission
    days_held = min(max_hold, len(hist))
    return TradeResult(symbol, direction, tier, entry, initial_stop_adj, target, last_close, "time", pnl, days_held, ts_utc)


def _consecutive_streaks(pnl_list: list[float]) -> tuple[int, int]:
    """Макс. серия побед и макс. серия поражений."""
    max_win_streak = max_loss_streak = 0
    cur_win = cur_loss = 0
    for p in pnl_list:
        if p > 0:
            cur_win += 1
            cur_loss = 0
        else:
            cur_loss += 1
            cur_win = 0
        max_win_streak = max(max_win_streak, cur_win)
        max_loss_streak = max(max_loss_streak, cur_loss)
    return max_win_streak, max_loss_streak


def _sharpe_ratio(pnl_list: list[float]) -> float:
    """Annualized Sharpe (assume ~250 trades/year, risk-free=0)."""
    if len(pnl_list) < 3:
        return 0.0
    mean = sum(pnl_list) / len(pnl_list)
    std = (sum((p - mean) ** 2 for p in pnl_list) / (len(pnl_list) - 1)) ** 0.5
    if std < 1e-9:
        return 0.0
    return mean / std * math.sqrt(min(len(pnl_list), 250))


def _print_stats(stats: Stats) -> None:
    n = stats.total
    if n == 0:
        print("Нет сделок для анализа.")
        return

    avg_pnl = sum(stats.pnl_list) / n
    wins_pct = stats.wins / n * 100.0
    gross_profit = sum(p for p in stats.pnl_list if p > 0)
    gross_loss = abs(sum(p for p in stats.pnl_list if p <= 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    win_pnls = [p for p in stats.pnl_list if p > 0]
    loss_pnls = [p for p in stats.pnl_list if p <= 0]
    avg_win = sum(win_pnls) / len(win_pnls) if win_pnls else 0.0
    avg_loss = sum(loss_pnls) / len(loss_pnls) if loss_pnls else 0.0
    win_loss_ratio = abs(avg_win / avg_loss) if avg_loss != 0 else float("inf")

    # Expectancy = (win_rate × avg_win) + (loss_rate × avg_loss)
    expectancy = (stats.wins / n) * avg_win + (stats.losses / n) * avg_loss if n > 0 else 0.0

    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in stats.pnl_list:
        cum += p
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd

    max_win_streak, max_loss_streak = _consecutive_streaks(stats.pnl_list)
    sharpe = _sharpe_ratio(stats.pnl_list)

    avg_hold = sum(r.hold_days for r in stats.results) / n if n > 0 else 0

    print(f"\n{'='*56}")
    print(f"  РЕЗУЛЬТАТЫ БЭКТЕСТА ({n} сделок)")
    print(f"{'='*56}")
    print(f"  Long: {stats.longs}  |  Short: {stats.shorts}")
    print(f"  Win rate:       {wins_pct:.1f}%")
    print(f"  Средний PnL:    {avg_pnl:+.2f}%")
    print(f"  Средний выигрыш: {avg_win:+.2f}%  |  Средний убыток: {avg_loss:+.2f}%")
    print(f"  Win/Loss ratio: {win_loss_ratio:.2f}" if not math.isinf(win_loss_ratio) else "  Win/Loss ratio: inf")
    print(f"  Expectancy:     {expectancy:+.3f}% на сделку")
    print(f"  Profit Factor:  {profit_factor:.2f}" if not math.isinf(profit_factor) else "  Profit Factor:  inf")
    print(f"  Sharpe ratio:   {sharpe:.2f}")
    print(f"  Max Drawdown:   -{max_dd:.2f}%")
    print(f"  Суммарный PnL:  {cum:+.2f}%")
    print(f"  Ср. удержание:  {avg_hold:.1f} дней")
    print(f"  Макс. серия побед:   {max_win_streak}")
    print(f"  Макс. серия убытков: {max_loss_streak}")
    print()

    exit_counts: dict[str, int] = defaultdict(int)
    for r in stats.results:
        exit_counts[r.exit_reason] += 1
    print("Выходы:")
    for reason in ("target", "stop", "trail", "time"):
        c = exit_counts.get(reason, 0)
        if c:
            print(f"  {reason}: {c} ({c/n*100:.0f}%)")

    print()
    print("По классам:")
    for tier in sorted(stats.by_tier.keys()):
        pnls = stats.by_tier[tier]
        cnt = len(pnls)
        w = sum(1 for p in pnls if p > 0)
        avg = sum(pnls) / cnt if cnt else 0
        print(f"  {tier} ({cnt} сделок): win {w/cnt*100:.1f}%, avg PnL {avg:+.2f}%")

    if stats.by_month:
        print()
        print("По месяцам:")
        for month_key in sorted(stats.by_month.keys()):
            pnls = stats.by_month[month_key]
            cnt = len(pnls)
            w = sum(1 for p in pnls if p > 0)
            total_pnl = sum(pnls)
            wr = w / cnt * 100 if cnt else 0
            print(f"  {month_key}: {cnt} сделок, win {wr:.0f}%, PnL {total_pnl:+.2f}%")

    print()
    print("Последние 10 сделок:")
    for r in stats.results[-10:]:
        d = "L" if r.direction == "long" else "S"
        print(
            f"  {r.ts_utc[:10]} {r.symbol:>10} {d} entry={r.entry:.2f} "
            f"exit={r.exit_price:.2f} ({r.exit_reason:6}) PnL={r.pnl_pct:+.2f}% {r.hold_days}д [{r.tier}]"
        )
    print()


@dataclass
class ForwardMetrics:
    """Предиктивные метрики: что происходит после сигнала без учёта стопов/целей."""
    by_day: dict[int, list[float]] = field(default_factory=lambda: defaultdict(list))
    directional_accuracy: dict[int, float] = field(default_factory=dict)


def _forward_metrics(signals: list[dict[str, Any]]) -> ForwardMetrics:
    """Считает доходность через 1/3/5/10 дней после сигнала и directional accuracy."""
    fm = ForwardMetrics()
    days_list = [1, 3, 5, 10]
    for row in signals:
        symbol = row.get("symbol", "")
        direction = row.get("tp_direction") or row.get("direction", "")
        ts_utc = str(row.get("ts_utc", ""))
        if not symbol or not ts_utc:
            continue
        try:
            dt_signal = datetime.fromisoformat(ts_utc.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        start = (dt_signal + timedelta(days=1)).strftime("%Y-%m-%d")
        end = (dt_signal + timedelta(days=15)).strftime("%Y-%m-%d")
        try:
            hist = yf.Ticker(symbol).history(start=start, end=end, interval="1d", auto_adjust=True)
        except Exception:
            continue
        if hist is None or hist.empty or len(hist) < 2:
            continue
        entry = float(hist.iloc[0].get("Open", 0))
        if entry <= 0:
            continue
        for d in days_list:
            if d < len(hist):
                close_d = float(hist.iloc[d].get("Close", 0))
                if close_d > 0:
                    ret = (close_d / entry - 1.0) * 100.0
                    fm.by_day[d].append(ret)
                    # Directional accuracy
                    correct = (direction == "long" and ret > 0) or (direction == "short" and ret < 0)
                    if d not in fm.directional_accuracy:
                        fm.directional_accuracy[d] = 0.0
                    fm.directional_accuracy[d] += 1.0 if correct else 0.0
    for d in days_list:
        if d in fm.directional_accuracy and len(fm.by_day.get(d, [])) > 0:
            fm.directional_accuracy[d] = fm.directional_accuracy[d] / len(fm.by_day[d]) * 100.0
    return fm


def _print_forward_metrics(fm: ForwardMetrics) -> None:
    print("\n" + "=" * 56)
    print("  ПРЕДИКТИВНЫЕ МЕТРИКИ (без стопов/целей)")
    print("=" * 56)
    for d in sorted(fm.by_day.keys()):
        rets = fm.by_day[d]
        if not rets:
            continue
        avg = sum(rets) / len(rets)
        wins = sum(1 for r in rets if r > 0)
        acc = fm.directional_accuracy.get(d, 0.0)
        print(f"  Через {d:2d} дней: avg {avg:+.2f}%  |  directional accuracy {acc:.1f}%  |  n={len(rets)}")
    print()


def main() -> int:
    p = argparse.ArgumentParser(description="Бэктест сигналов из JSONL.")
    p.add_argument("path", help="Файл .jsonl (SSA_SIGNAL_LOG)")
    p.add_argument("--min-tier", default=None, choices=["A", "B", "C"], help="Минимальный класс (A = только A)")
    p.add_argument("--target", type=int, default=1, choices=[1, 2], help="Какую цель проверять (1 или 2)")
    p.add_argument("--commission", type=float, default=0.1, help="Комиссия за сделку в %% (по умолчанию 0.1%%)")
    p.add_argument("--forward", action="store_true", help="Считать forward-looking метрики (медленно)")
    args = p.parse_args()

    signals = _load_signals(args.path, args.min_tier)
    if not signals:
        print(f"Нет подходящих записей в {args.path}.", file=sys.stderr)
        return 1

    print(f"Загружено {len(signals)} сигналов. Комиссия: {args.commission}% на сделку. Загрузка истории...")

    stats = Stats()
    for i, row in enumerate(signals, 1):
        result = _simulate_trade(row, target_n=args.target, commission_pct=args.commission)
        if result is None:
            continue
        stats.total += 1
        stats.pnl_list.append(result.pnl_pct)
        stats.results.append(result)
        stats.by_tier[result.tier].append(result.pnl_pct)
        if result.direction == "long":
            stats.longs += 1
        else:
            stats.shorts += 1
        if result.pnl_pct > 0:
            stats.wins += 1
        else:
            stats.losses += 1
        month_key = result.ts_utc[:7]
        if month_key:
            stats.by_month[month_key].append(result.pnl_pct)
        if i % 10 == 0:
            print(f"  обработано {i}/{len(signals)}...")

    _print_stats(stats)

    if args.forward:
        print("\nСчитаю forward-looking метрики (займёт время)...")
        fm = _forward_metrics(signals)
        _print_forward_metrics(fm)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
