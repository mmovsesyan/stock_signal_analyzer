#!/usr/bin/env python3
"""
CLI: анализ тикера и периодическое обновление сигналов.

Примеры:
  python main.py AAPL
  python main.py SBER.ME --watch --interval 300
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone

import stenv

stenv.load_project_env()

from stock_signal_analyzer.config_validator import validate_symbol
from stock_signal_analyzer.engine import build_report


def _print_report(r) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print()
    print(f"=== {ts} | {r.symbol} — {r.company} ===")
    print(f"Инструмент: {r.instrument_label}")
    tp = getattr(r, "trade_plan", None)
    if tp and tp.direction != "none":
        d = "LONG" if tp.direction == "long" else "SHORT"
        print()
        print(f"  ╔══ ТОРГОВЫЙ ПЛАН ══════════════════════")
        print(f"  ║  {d} {r.symbol} @ {tp.entry_price:.2f}")
        print(f"  ║  Стоп: {tp.stop_price:.2f} ({tp.stop_pct:+.2f}%)")
        print(f"  ║  Цель 1: {tp.target1_price:.2f} ({tp.target1_pct:+.2f}%)  R:R {tp.risk_reward_1:.1f} — закрыть {tp.partial_exit_pct:.0f}%")
        print(f"  ║  Цель 2: {tp.target2_price:.2f} ({tp.target2_pct:+.2f}%)  R:R {tp.risk_reward_2:.1f} — остаток")
        print(f"  ║  Трейлинг: после +{tp.trailing_activation_pct:.1f}% → безубыток")
        print(f"  ║  Удержание: до {tp.max_hold_days} дней  |  Позиция: {tp.position_size_pct:.0f}%  |  Класс: {r.signal_tier}")
        print(f"  ╚═══════════════════════════════════════")
        print()
    elif tp:
        print(f"  {tp.plan_text}")
        print()
    print(f"Итоговый балл: {r.score:+.3f}  (-1…+1)  (до макро: {r.score_before_macro:+.3f})")
    print(
        f"Согласованность: {r.confidence:.2f}  |  ADX14≈{r.adx14:.1f}  |  Режим: {r.regime_label}"
    )
    atr = f"{r.atr_pct:.2f}%" if r.atr_pct is not None else "н/д"
    sh = f"{r.stop_hint_pct:.2f}%" if r.stop_hint_pct is not None else "н/д"
    print(f"Класс качества: {r.signal_tier}  |  ATR(14): {atr}  |  стоп-ориентир ~1.5×ATR: {sh}  |  ref {r.ref_price:.4f}")
    print(r.tier_rationale)
    print(f"Контекст: [{r.weekly_regime}] {r.timing_detail}")
    print(r.verdict)
    print()
    print("Компоненты:")
    print(f"  Техника:   {r.technical_score:+.3f}  |  {r.technical_detail}")
    if r.pattern_summary:
        print(f"             Паттерны: {r.pattern_summary}")
    print(f"  Импульс:   {r.momentum_score:+.3f}  |  {r.momentum_detail}")
    print(f"  Новости:   {r.news_score:+.3f}  |  {r.news_detail}")
    print(f"  Объём:     {r.volume_score:+.3f}  |  {r.volume_detail}")
    if r.intraday_score is not None:
        print(f"  Онлайн:    {r.intraday_score:+.3f}  |  {r.intraday_detail}")
    else:
        print(f"  Онлайн:    —  |  {getattr(r, 'online_hint', '') or 'нет данных'}")
    print()
    if hasattr(r, "levels_detail") and r.levels_detail:
        print(f"Уровни: {r.levels_detail}")
    print()

    # Quant models
    quant_lines = []
    for attr in ("mtf_momentum_detail", "trend_detail", "zscore_detail", "vol_regime_detail", "cross_asset_detail"):
        val = getattr(r, attr, "")
        if val:
            quant_lines.append(f"  {val}")
    if quant_lines:
        qs = getattr(r, "quant_score", 0.0) or 0.0
        print(f"Квант-модели (score: {qs:+.3f}):")
        for ql in quant_lines:
            print(ql)
    ps_detail = getattr(r, "position_size_detail", "")
    if ps_detail:
        print(f"  {ps_detail}")
    print()

    print("Макро (ставки, инфляция, заседания ЦБ):")
    print(f"  Коэффициент к баллу: ×{r.macro_dampening:.2f}")
    print(r.macro_summary)
    print()
    print(r.risk_note)
    print()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Сигналы по акции: техника + импульс + новости (RSS/Google).")
    p.add_argument("symbol", help="Тикер Yahoo Finance, напр. AAPL, MSFT, SBER.ME")
    p.add_argument("--watch", action="store_true", help="Повторять анализ в цикле")
    p.add_argument("--interval", type=int, default=600, help="Интервал в секундах в режиме watch (по умолчанию 600)")
    p.add_argument(
        "--fast",
        action="store_true",
        help="Быстрый режим: пропустить новости и real-time данные (анализ ~3-5 сек вместо ~16 сек)",
    )
    p.add_argument(
        "--finnhub-ws",
        action="store_true",
        help="Краткий WebSocket Finnhub по сделкам (нужен FINNHUB_API_KEY; для US-ликвидных тикеров)",
    )
    p.add_argument("--ws-seconds", type=float, default=8.0, help="Длительность сбора сделок WebSocket (сек.)")
    p.add_argument(
        "--volume-tape",
        action="store_true",
        help="Добавить к анализу объёма ленту сделок Finnhub (tick rule; нужен ключ; US)",
    )
    args = p.parse_args(argv)

    try:
        args.symbol = validate_symbol(args.symbol)
    except ValueError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1

    if args.interval < 60:
        print("Интервал < 60 с не рекомендуется: ленты и котировки могут блокировать частые запросы.", file=sys.stderr)

    while True:
        try:
            r = build_report(
                args.symbol,
                use_finnhub_ws=args.finnhub_ws,
                ws_seconds=args.ws_seconds,
                volume_tape_ws=args.volume_tape,
                fast_mode=args.fast,
            )
            _print_report(r)
        except KeyboardInterrupt:
            print("\nОстановка.")
            return 0
        except Exception as e:
            print(f"Ошибка: {e}", file=sys.stderr)
            if not args.watch:
                return 1
        if not args.watch:
            break
        time.sleep(max(60, args.interval))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
