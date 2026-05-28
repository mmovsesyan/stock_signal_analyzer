"""
Backtest Report — генерация отчётов по историческим результатам сигналов.

Читает outcomes.jsonl, считает win-rate, средний P&L,
profit factor, Sharpe-like, max drawdown.

Использование:
    from stock_signal_analyzer.backtest_report import BacktestReport, format_report
    report = BacktestReport()
    print(format_report(report))
"""
from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

# PnL в outcomes.jsonl уже net (slippage + commission вычтены в outcome_tracker).
# Дополнительных вычетов здесь не нужно.
_COMMISSION_PCT = 0.0


@dataclass
class TierStats:
    """Статистика по тиру или направлению."""
    group: str
    total: int = 0
    wins: int = 0
    losses: int = 0
    timeouts: int = 0
    win_rate: float = 0.0
    avg_pnl_pct: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    profit_factor: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0


@dataclass
class EquityPoint:
    """Точка эквити-кривой."""
    date: str
    equity: float
    trade_num: int


@dataclass
class BacktestResult:
    """Полный результат бэктеста."""
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    timeouts: int = 0
    win_rate: float = 0.0
    avg_pnl_pct: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    profit_factor: float = 0.0
    sharpe_like: float = 0.0
    max_drawdown_pct: float = 0.0
    total_return_pct: float = 0.0
    commission_pct: float = _COMMISSION_PCT
    by_tier: dict[str, TierStats] = field(default_factory=dict)
    by_direction: dict[str, TierStats] = field(default_factory=dict)
    equity_curve: list[EquityPoint] = field(default_factory=list)
    date_range: str = ""


class BacktestReport:
    """Генератор отчёта по outcomes.jsonl."""

    def __init__(self, outcomes_path: str | None = None):
        self.outcomes_path = outcomes_path or os.path.join(
            os.environ.get("STOCK_SIGNAL_DATA", "/var/lib/stock_signal_analyzer"),
            "outcomes.jsonl",
        )

    def _load_outcomes(self) -> list[dict[str, Any]]:
        """Загрузить закрытые сделки из outcomes.jsonl."""
        path = Path(self.outcomes_path)
        if not path.exists():
            _log.warning("Outcomes file not found: %s", self.outcomes_path)
            return []

        outcomes: list[dict[str, Any]] = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    out = rec.get("outcome", "")
                    if out in ("win_t1", "win_t2", "loss", "timeout"):
                        outcomes.append(rec)
                except json.JSONDecodeError:
                    continue
        return outcomes

    @staticmethod
    def _safe_pnl(pnl_pct: float | None) -> float:
        """Вернуть PnL как есть (уже net в outcomes)."""
        return pnl_pct if pnl_pct is not None else 0.0

    def _build_equity_curve(self, outcomes: list[dict[str, Any]]) -> list[EquityPoint]:
        """Построить эквити-кривую от хронологически отсортированных сделок."""
        sorted_outcomes = sorted(
            outcomes,
            key=lambda r: r.get("exit_date") or r.get("checked_at") or "",
        )
        equity: list[EquityPoint] = []
        running = 100.0
        for i, rec in enumerate(sorted_outcomes, start=1):
            pnl = self._safe_pnl(rec.get("pnl_pct"))
            running += running * (pnl / 100.0)
            date = rec.get("exit_date") or rec.get("checked_at") or ""
            if isinstance(date, str):
                date = date[:10]  # YYYY-MM-DD
            equity.append(EquityPoint(date=date, equity=round(running, 4), trade_num=i))
        return equity

    @staticmethod
    def _max_drawdown(equity: list[EquityPoint]) -> float:
        """Максимальная просадка в процентах."""
        if not equity:
            return 0.0
        peak = equity[0].equity
        max_dd = 0.0
        for point in equity:
            if point.equity > peak:
                peak = point.equity
            dd = (peak - point.equity) / peak * 100.0
            if dd > max_dd:
                max_dd = dd
        return round(max_dd, 2)

    @staticmethod
    def _sharpe_like(returns: list[float]) -> float:
        """Sharpe-like ratio: mean / std * sqrt(n) по сделкам."""
        if len(returns) < 2:
            return 0.0
        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
        std = math.sqrt(variance) if variance > 0 else 0.0
        if std == 0:
            return 0.0
        return round(mean / std * math.sqrt(len(returns)), 2)

    def _group_stats(self, records: list[dict[str, Any]], group_name: str) -> TierStats:
        """Посчитать статистику по группе записей."""
        if not records:
            return TierStats(group=group_name)

        wins = [r for r in records if r.get("outcome") in ("win_t1", "win_t2")]
        losses = [r for r in records if r.get("outcome") == "loss"]
        timeouts = [r for r in records if r.get("outcome") == "timeout"]

        win_pnls = [self._safe_pnl(r.get("pnl_pct")) for r in wins]
        loss_pnls = [self._safe_pnl(r.get("pnl_pct")) for r in losses]
        all_pnls = [self._safe_pnl(r.get("pnl_pct")) for r in records]

        total = len(wins) + len(losses)
        win_rate = len(wins) / total if total > 0 else 0.0

        gross_profit = sum(p for p in win_pnls if p > 0)
        gross_loss = abs(sum(p for p in loss_pnls if p < 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        return TierStats(
            group=group_name,
            total=len(records),
            wins=len(wins),
            losses=len(losses),
            timeouts=len(timeouts),
            win_rate=round(win_rate, 2),
            avg_pnl_pct=round(sum(all_pnls) / len(all_pnls), 2) if all_pnls else 0.0,
            avg_win_pct=round(sum(win_pnls) / len(win_pnls), 2) if win_pnls else 0.0,
            avg_loss_pct=round(sum(loss_pnls) / len(loss_pnls), 2) if loss_pnls else 0.0,
            profit_factor=round(profit_factor, 2),
            gross_profit=round(gross_profit, 2),
            gross_loss=round(gross_loss, 2),
        )

    def generate(self) -> BacktestResult:
        """Сгенерировать полный отчёт."""
        outcomes = self._load_outcomes()
        if not outcomes:
            return BacktestResult()

        # Фильтруем timeout из win_rate / profit factor, но оставляем в total_trades
        decisive = [r for r in outcomes if r.get("outcome") in ("win_t1", "win_t2", "loss")]

        wins = [r for r in decisive if r.get("outcome") in ("win_t1", "win_t2")]
        losses = [r for r in decisive if r.get("outcome") == "loss"]
        timeouts = [r for r in outcomes if r.get("outcome") == "timeout"]

        win_pnls = [self._safe_pnl(r.get("pnl_pct")) for r in wins]
        loss_pnls = [self._safe_pnl(r.get("pnl_pct")) for r in losses]
        all_decisive_pnls = [self._safe_pnl(r.get("pnl_pct")) for r in decisive]

        total_decisive = len(wins) + len(losses)
        win_rate = len(wins) / total_decisive if total_decisive > 0 else 0.0

        gross_profit = sum(p for p in win_pnls if p > 0)
        gross_loss = abs(sum(p for p in loss_pnls if p < 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        # Sharpe-like по net PnL каждой сделки
        sharpe = self._sharpe_like(all_decisive_pnls)

        # Equity curve и drawdown
        equity = self._build_equity_curve(outcomes)
        max_dd = self._max_drawdown(equity)
        total_return = round(equity[-1].equity - 100.0, 2) if equity else 0.0

        # Дата-диапазон
        dates = sorted(
            {
                (r.get("exit_date") or r.get("checked_at") or "")[:10]
                for r in outcomes
                if (r.get("exit_date") or r.get("checked_at"))
            }
        )
        date_range = f"{dates[0]} — {dates[-1]}" if dates else ""

        # Разбивки
        by_tier: dict[str, list[dict[str, Any]]] = {}
        by_direction: dict[str, list[dict[str, Any]]] = {}
        for r in outcomes:
            tier = r.get("signal_tier") or "unknown"
            direction = r.get("direction") or "unknown"
            by_tier.setdefault(tier, []).append(r)
            by_direction.setdefault(direction, []).append(r)

        tier_stats = {k: self._group_stats(v, k) for k, v in by_tier.items()}
        direction_stats = {k: self._group_stats(v, k) for k, v in by_direction.items()}

        return BacktestResult(
            total_trades=len(outcomes),
            wins=len(wins),
            losses=len(losses),
            timeouts=len(timeouts),
            win_rate=round(win_rate, 2),
            avg_pnl_pct=round(sum(all_decisive_pnls) / len(all_decisive_pnls), 2) if all_decisive_pnls else 0.0,
            avg_win_pct=round(sum(win_pnls) / len(win_pnls), 2) if win_pnls else 0.0,
            avg_loss_pct=round(sum(loss_pnls) / len(loss_pnls), 2) if loss_pnls else 0.0,
            profit_factor=round(profit_factor, 2),
            sharpe_like=sharpe,
            max_drawdown_pct=max_dd,
            total_return_pct=total_return,
            commission_pct=_COMMISSION_PCT,
            by_tier=tier_stats,
            by_direction=direction_stats,
            equity_curve=equity,
            date_range=date_range,
        )


def format_report(result: BacktestResult) -> str:
    """Форматировать отчёт в человекочитаемую строку на русском."""
    if result.total_trades == 0:
        return "📊 Бэктест: нет закрытых сделок для анализа."

    lines: list[str] = [
        f"📊 Бэктест-отчёт ({result.date_range})",
        "",
        f"Всего сделок: {result.total_trades}  (побед: {result.wins}, убытков: {result.losses}, таймаутов: {result.timeouts})",
        f"Win rate: {result.win_rate * 100:.0f}%",
        f"Средний P&L: {result.avg_pnl_pct:+.2f}%",
        f"Средний выигрыш: {result.avg_win_pct:+.2f}%  |  Средний убыток: {result.avg_loss_pct:+.2f}%",
        f"Profit factor: {result.profit_factor:.2f}",
        f"Sharpe-like: {result.sharpe_like:.2f}",
        f"Max drawdown: {result.max_drawdown_pct:.1f}%",
        f"Total return: {result.total_return_pct:+.2f}%  (комиссия уже учтена в PnL)",
        "",
        "── По тирам ──",
    ]

    for tier in sorted(result.by_tier.keys()):
        s = result.by_tier[tier]
        lines.append(
            f"  Тир {tier}: {s.total} сделок, WR {s.win_rate * 100:.0f}%, "
            f"avg P&L {s.avg_pnl_pct:+.2f}%, PF {s.profit_factor:.2f}"
        )

    lines.append("")
    lines.append("── По направлению ──")
    for direction in sorted(result.by_direction.keys()):
        s = result.by_direction[direction]
        lines.append(
            f"  {direction}: {s.total} сделок, WR {s.win_rate * 100:.0f}%, "
            f"avg P&L {s.avg_pnl_pct:+.2f}%, PF {s.profit_factor:.2f}"
        )

    return "\n".join(lines)
