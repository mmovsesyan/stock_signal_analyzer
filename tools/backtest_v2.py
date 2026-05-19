#!/usr/bin/env python3
"""
Бэктестер v2: полный candle replay с эмуляцией ордеров.

Отличия от v1 (backtest.py):
- Прогон engine.build_report() на исторических данных день за днём
- Эмуляция входов по Open следующего дня
- Slippage модель (configurable)
- Комиссии (round-trip)
- Equity curve + max drawdown
- Отчёт: Sharpe, Sortino, Calmar, profit factor
- Экспорт результатов в JSON

Пример:
  python tools/backtest_v2.py --symbols AAPL MSFT --days 180
  python tools/backtest_v2.py --symbols SBER.ME --days 90 --slippage 0.05
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yfinance as yf

# Добавить корень проекта в path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_signal_analyzer.backtest_charts import generate_backtest_plots


@dataclass
class TradeRecord:
    symbol: str
    direction: str
    tier: str
    entry_price: float
    stop_price: float
    target1_price: float
    target2_price: float
    exit_price: float
    exit_reason: str  # target1, target2, stop, trail, time
    pnl_pct: float
    pnl_abs: float
    hold_days: int
    entry_date: str
    exit_date: str
    score: float
    confidence: float


@dataclass
class EquityPoint:
    date: str
    equity: float
    drawdown_pct: float


@dataclass
class BacktestResult:
    trades: list[TradeRecord] = field(default_factory=list)
    equity_curve: list[EquityPoint] = field(default_factory=list)
    initial_capital: float = 100000.0
    final_capital: float = 100000.0
    total_return_pct: float = 0.0
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    avg_pnl_pct: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0
    avg_hold_days: float = 0.0
    by_tier: dict[str, dict[str, float]] = field(default_factory=dict)
    by_symbol: dict[str, dict[str, float]] = field(default_factory=dict)


def _fetch_history(symbol: str, days: int) -> pd.DataFrame | None:
    """Загрузить историю для бэктеста с retry."""
    import time

    def _fetch():
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days + 30)  # запас для индикаторов
        t = yf.Ticker(symbol)
        hist = t.history(start=start.strftime("%Y-%m-%d"),
                        end=end.strftime("%Y-%m-%d"),
                        interval="1d", auto_adjust=True)
        if hist is None or hist.empty or len(hist) < 60:
            return None
        return hist

    last_err = None
    for attempt in range(3):
        try:
            result = _fetch()
            return result
        except Exception as e:
            last_err = e
            msg = str(e).lower()
            if "too many requests" in msg or "rate limited" in msg:
                wait = 2.0 * (2 ** attempt)
                print(f"  yfinance rate limit ({symbol}), retry in {wait:.1f}s...",
                      file=sys.stderr)
                time.sleep(wait)
            else:
                print(f"  Ошибка загрузки {symbol}: {e}", file=sys.stderr)
                return None
    print(f"  Ошибка загрузки {symbol} после 3 попыток: {last_err}", file=sys.stderr)
    return None


def _simulate_on_candles(
    symbol: str,
    hist: pd.DataFrame,
    lookback: int = 55,
    slippage_pct: float = 0.02,
    commission_pct: float = 0.1,
    position_size_pct: float = 10.0,
    min_tier: str = "B",
) -> list[TradeRecord]:
    """
    Candle replay: проходим по истории день за днём.
    На каждом баре (после lookback) запускаем анализ на доступной истории,
    если сигнал достаточно сильный — открываем позицию.
    """
    from stock_signal_analyzer.technical import analyze_technical
    from stock_signal_analyzer.momentum import analyze_momentum
    from stock_signal_analyzer.risk_context import atr_percent_14, classify_signal_tier
    from stock_signal_analyzer.trade_plan import build_trade_plan
    from stock_signal_analyzer.volume_pressure import analyze_volume_pressure

    tier_order = {"A": 0, "B": 1, "C": 2}
    min_tier_idx = tier_order.get(min_tier, 1)

    trades: list[TradeRecord] = []
    in_trade = False
    trade_entry = 0.0
    trade_stop = 0.0
    trade_target1 = 0.0
    trade_target2 = 0.0
    trade_direction = ""
    trade_tier = ""
    trade_entry_date = ""
    trade_score = 0.0
    trade_confidence = 0.0
    trade_max_hold = 5
    trade_days_held = 0
    trail_activated = False

    dates = hist.index.tolist()

    for i in range(lookback, len(dates) - 1):
        current_date = str(dates[i].date()) if hasattr(dates[i], 'date') else str(dates[i])[:10]
        window = hist.iloc[max(0, i - 250):i + 1]
        close = window["Close"]
        current_close = float(close.iloc[-1])

        if in_trade:
            # Проверяем стоп/цель на текущей свече
            day = hist.iloc[i]
            h = float(day.get("High", current_close))
            lo = float(day.get("Low", current_close))
            trade_days_held += 1

            exit_price = 0.0
            exit_reason = ""

            if trade_direction == "long":
                # Trailing stop
                if not trail_activated and h >= trade_entry * 1.03:
                    trade_stop = max(trade_stop, trade_entry)
                    trail_activated = True

                if lo <= trade_stop:
                    exit_price = trade_stop * (1 - slippage_pct / 100)
                    exit_reason = "trail" if trail_activated else "stop"
                elif h >= trade_target1:
                    exit_price = trade_target1 * (1 - slippage_pct / 100)
                    exit_reason = "target1"
                elif trade_days_held >= trade_max_hold:
                    exit_price = current_close
                    exit_reason = "time"
            else:  # short
                if not trail_activated and lo <= trade_entry * 0.97:
                    trade_stop = min(trade_stop, trade_entry)
                    trail_activated = True

                if h >= trade_stop:
                    exit_price = trade_stop * (1 + slippage_pct / 100)
                    exit_reason = "trail" if trail_activated else "stop"
                elif lo <= trade_target1:
                    exit_price = trade_target1 * (1 + slippage_pct / 100)
                    exit_reason = "target1"
                elif trade_days_held >= trade_max_hold:
                    exit_price = current_close
                    exit_reason = "time"

            if exit_reason:
                if trade_direction == "long":
                    pnl_pct = (exit_price / trade_entry - 1.0) * 100 - commission_pct * 2
                else:
                    pnl_pct = (1.0 - exit_price / trade_entry) * 100 - commission_pct * 2

                pnl_abs = pnl_pct / 100 * position_size_pct
                trades.append(TradeRecord(
                    symbol=symbol,
                    direction=trade_direction,
                    tier=trade_tier,
                    entry_price=trade_entry,
                    stop_price=trade_stop,
                    target1_price=trade_target1,
                    target2_price=trade_target2,
                    exit_price=exit_price,
                    exit_reason=exit_reason,
                    pnl_pct=round(pnl_pct, 3),
                    pnl_abs=round(pnl_abs, 3),
                    hold_days=trade_days_held,
                    entry_date=trade_entry_date,
                    exit_date=current_date,
                    score=trade_score,
                    confidence=trade_confidence,
                ))
                in_trade = False
                continue
            continue

        # Не в сделке — анализируем
        if len(window) < lookback:
            continue

        try:
            tech = analyze_technical(window)
            atr_pct = atr_percent_14(window)
            mom = analyze_momentum(close, atr_pct=atr_pct, adx14=tech.adx14)
            vol_res = analyze_volume_pressure(window, symbol)

            # Упрощённый score (без новостей и intraday для скорости)
            score = 0.45 * tech.score + 0.35 * mom.score + 0.20 * vol_res.score
            score = max(-1.0, min(1.0, score))

            confidence = 1.0 - abs(tech.score - mom.score) / 2.0
            confidence = max(0.22, min(1.0, confidence))

            has_pat = bool(tech.pattern_summary and tech.pattern_summary.strip())
            tier, _ = classify_signal_tier(
                total=score, confidence=confidence, macro_dampening=1.0,
                adx14=tech.adx14, news_score=0.0, liq_mult=1.0,
                vol_align_mult=1.0, has_chart_pattern=has_pat,
                weekly_aligned=True, earnings_window=False, index_headwind=False,
            )

            tier_idx = tier_order.get(tier, 2)
            if tier_idx > min_tier_idx:
                continue
            if abs(score) < 0.23:
                continue

            # Генерируем торговый план
            tp = build_trade_plan(
                score=score, ref_price=current_close, atr_pct=atr_pct,
                signal_tier=tier, adx14=tech.adx14, symbol=symbol,
                confidence=confidence, has_pattern=has_pat,
            )
            if tp is None or tp.direction == "none":
                continue

            # Вход на Open следующего дня
            next_day = hist.iloc[i + 1]
            entry_price = float(next_day.get("Open", current_close))
            entry_price *= (1 + slippage_pct / 100) if tp.direction == "long" else (1 - slippage_pct / 100)

            # Пересчитать уровни пропорционально
            ratio = entry_price / current_close if current_close > 0 else 1.0
            trade_entry = entry_price
            trade_stop = tp.stop_price * ratio
            trade_target1 = tp.target1_price * ratio
            trade_target2 = tp.target2_price * ratio
            trade_direction = tp.direction
            trade_tier = tier
            trade_entry_date = str(dates[i + 1].date()) if hasattr(dates[i + 1], 'date') else str(dates[i + 1])[:10]
            trade_score = score
            trade_confidence = confidence
            trade_max_hold = tp.max_hold_days
            trade_days_held = 0
            trail_activated = False
            in_trade = True

        except Exception:
            continue

    return trades


def _compute_stats(trades: list[TradeRecord], initial_capital: float = 100000.0) -> BacktestResult:
    """Вычислить все метрики из списка сделок."""
    result = BacktestResult(trades=trades, initial_capital=initial_capital)

    if not trades:
        return result

    pnl_list = [t.pnl_pct for t in trades]
    n = len(trades)
    result.total_trades = n
    result.wins = sum(1 for p in pnl_list if p > 0)
    result.losses = n - result.wins
    result.win_rate = result.wins / n * 100

    result.avg_pnl_pct = sum(pnl_list) / n
    win_pnls = [p for p in pnl_list if p > 0]
    loss_pnls = [p for p in pnl_list if p <= 0]
    result.avg_win_pct = sum(win_pnls) / len(win_pnls) if win_pnls else 0.0
    result.avg_loss_pct = sum(loss_pnls) / len(loss_pnls) if loss_pnls else 0.0

    gross_profit = sum(win_pnls)
    gross_loss = abs(sum(loss_pnls))
    result.profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Equity curve
    equity = initial_capital
    peak = equity
    max_dd = 0.0
    for t in trades:
        equity += equity * t.pnl_pct / 100
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak * 100 if peak > 0 else 0
        max_dd = max(max_dd, dd)
        result.equity_curve.append(EquityPoint(
            date=t.exit_date, equity=round(equity, 2), drawdown_pct=round(dd, 2)
        ))

    result.final_capital = equity
    result.total_return_pct = (equity / initial_capital - 1) * 100
    result.max_drawdown_pct = max_dd

    # Sharpe (annualized, assume ~250 trading days)
    if len(pnl_list) >= 3:
        mean = np.mean(pnl_list)
        std = np.std(pnl_list, ddof=1)
        result.sharpe_ratio = float(mean / std * math.sqrt(min(n, 250))) if std > 0 else 0.0

        # Sortino (downside deviation)
        downside = [p for p in pnl_list if p < 0]
        if downside:
            down_std = np.std(downside, ddof=1)
            result.sortino_ratio = float(mean / down_std * math.sqrt(min(n, 250))) if down_std > 0 else 0.0

    # Calmar
    if max_dd > 0:
        result.calmar_ratio = result.total_return_pct / max_dd

    # Consecutive streaks
    cur_win = cur_loss = max_win = max_loss = 0
    for p in pnl_list:
        if p > 0:
            cur_win += 1
            cur_loss = 0
        else:
            cur_loss += 1
            cur_win = 0
        max_win = max(max_win, cur_win)
        max_loss = max(max_loss, cur_loss)
    result.max_consecutive_wins = max_win
    result.max_consecutive_losses = max_loss

    result.avg_hold_days = sum(t.hold_days for t in trades) / n

    # By tier
    tier_groups: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        tier_groups[t.tier].append(t.pnl_pct)
    for tier, pnls in tier_groups.items():
        cnt = len(pnls)
        w = sum(1 for p in pnls if p > 0)
        result.by_tier[tier] = {
            "count": cnt,
            "win_rate": w / cnt * 100,
            "avg_pnl": sum(pnls) / cnt,
            "total_pnl": sum(pnls),
        }

    # By symbol
    sym_groups: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        sym_groups[t.symbol].append(t.pnl_pct)
    for sym, pnls in sym_groups.items():
        cnt = len(pnls)
        w = sum(1 for p in pnls if p > 0)
        result.by_symbol[sym] = {
            "count": cnt,
            "win_rate": w / cnt * 100,
            "avg_pnl": sum(pnls) / cnt,
            "total_pnl": sum(pnls),
        }

    return result


def _print_report(r: BacktestResult) -> None:
    """Вывести отчёт в консоль."""
    print(f"\n{'='*60}")
    print(f"  БЭКТЕСТ v2 — CANDLE REPLAY ({r.total_trades} сделок)")
    print(f"{'='*60}")
    print(f"  Капитал: {r.initial_capital:,.0f} → {r.final_capital:,.0f} ({r.total_return_pct:+.2f}%)")
    print(f"  Win rate:         {r.win_rate:.1f}%")
    print(f"  Средний PnL:      {r.avg_pnl_pct:+.3f}%")
    print(f"  Средний выигрыш:  {r.avg_win_pct:+.3f}%")
    print(f"  Средний убыток:   {r.avg_loss_pct:+.3f}%")
    print(f"  Profit Factor:    {r.profit_factor:.2f}")
    print(f"  Sharpe:           {r.sharpe_ratio:.2f}")
    print(f"  Sortino:          {r.sortino_ratio:.2f}")
    print(f"  Calmar:           {r.calmar_ratio:.2f}")
    print(f"  Max Drawdown:     -{r.max_drawdown_pct:.2f}%")
    print(f"  Ср. удержание:    {r.avg_hold_days:.1f} дней")
    print(f"  Макс. серия побед:   {r.max_consecutive_wins}")
    print(f"  Макс. серия убытков: {r.max_consecutive_losses}")
    print()

    if r.by_tier:
        print("По классам:")
        for tier in sorted(r.by_tier.keys()):
            s = r.by_tier[tier]
            print(f"  {tier}: {s['count']:.0f} сделок, win {s['win_rate']:.1f}%, "
                  f"avg {s['avg_pnl']:+.2f}%, total {s['total_pnl']:+.2f}%")
        print()

    if r.by_symbol:
        print("По тикерам:")
        for sym in sorted(r.by_symbol.keys(), key=lambda s: r.by_symbol[s]['total_pnl'], reverse=True):
            s = r.by_symbol[sym]
            print(f"  {sym:>10}: {s['count']:.0f} сделок, win {s['win_rate']:.1f}%, "
                  f"total {s['total_pnl']:+.2f}%")
        print()

    if r.trades:
        print("Последние 10 сделок:")
        for t in r.trades[-10:]:
            d = "L" if t.direction == "long" else "S"
            print(f"  {t.entry_date} {t.symbol:>10} {d} "
                  f"entry={t.entry_price:.2f} exit={t.exit_price:.2f} "
                  f"({t.exit_reason:7}) PnL={t.pnl_pct:+.2f}% {t.hold_days}д [{t.tier}]")
    print()


def main() -> int:
    p = argparse.ArgumentParser(description="Бэктест v2: candle replay с эмуляцией ордеров.")
    p.add_argument("--symbols", nargs="+", required=True, help="Тикеры для бэктеста")
    p.add_argument("--days", type=int, default=180, help="Период бэктеста в днях (по умолчанию 180)")
    p.add_argument("--min-tier", default="B", choices=["A", "B", "C"], help="Минимальный класс сигнала")
    p.add_argument("--slippage", type=float, default=0.02, help="Slippage в %% (по умолчанию 0.02)")
    p.add_argument("--commission", type=float, default=0.1, help="Комиссия в %% за сделку (по умолчанию 0.1)")
    p.add_argument("--capital", type=float, default=100000, help="Начальный капитал")
    p.add_argument("--export", type=str, default=None, help="Экспорт результатов в JSON файл")
    p.add_argument("--charts", action="store_true", help="Сгенерировать графики (PNG)")
    p.add_argument("--charts-dir", type=str, default="./data/backtest_charts", help="Директория для графиков")
    args = p.parse_args()

    import stenv
    stenv.load_project_env()

    all_trades: list[TradeRecord] = []

    for sym in args.symbols:
        print(f"  Загружаю историю {sym}...")
        hist = _fetch_history(sym, args.days)
        if hist is None:
            print(f"  ⚠ Пропускаю {sym}: недостаточно данных")
            continue
        print(f"  Replay {sym} ({len(hist)} свечей)...")
        trades = _simulate_on_candles(
            sym, hist,
            slippage_pct=args.slippage,
            commission_pct=args.commission,
            min_tier=args.min_tier,
        )
        print(f"  {sym}: {len(trades)} сделок")
        all_trades.extend(trades)

    # Сортируем по дате входа
    all_trades.sort(key=lambda t: t.entry_date)

    result = _compute_stats(all_trades, initial_capital=args.capital)
    _print_report(result)

    if args.charts:
        print("  Генерация графиков...")
        try:
            chart_paths = generate_backtest_plots(result, args.charts_dir)
            if chart_paths:
                print(f"  Сохранено {len(chart_paths)} файлов в {args.charts_dir}:")
                for p in chart_paths:
                    print(f"    • {p}")
            else:
                print("  Графики не сгенерированы (matplotlib недоступен или нет данных)")
        except Exception as exc:
            print(f"  ⚠ Ошибка генерации графиков: {exc}")

    if args.export:
        export_data = {
            "meta": {
                "symbols": args.symbols,
                "days": args.days,
                "min_tier": args.min_tier,
                "slippage_pct": args.slippage,
                "commission_pct": args.commission,
            },
            "summary": {
                "total_trades": result.total_trades,
                "win_rate": result.win_rate,
                "total_return_pct": result.total_return_pct,
                "profit_factor": result.profit_factor,
                "sharpe": result.sharpe_ratio,
                "sortino": result.sortino_ratio,
                "max_drawdown_pct": result.max_drawdown_pct,
            },
            "by_tier": result.by_tier,
            "by_symbol": result.by_symbol,
            "trades": [
                {
                    "symbol": t.symbol, "direction": t.direction, "tier": t.tier,
                    "entry": t.entry_price, "exit": t.exit_price,
                    "exit_reason": t.exit_reason, "pnl_pct": t.pnl_pct,
                    "hold_days": t.hold_days, "entry_date": t.entry_date,
                    "exit_date": t.exit_date,
                }
                for t in all_trades
            ],
            "equity_curve": [
                {"date": ep.date, "equity": ep.equity, "dd": ep.drawdown_pct}
                for ep in result.equity_curve
            ],
        }
        with open(args.export, "w", encoding="utf-8") as f:
            json.dump(export_data, f, ensure_ascii=False, indent=2)
        print(f"  Экспортировано в {args.export}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
