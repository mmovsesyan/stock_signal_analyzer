"""
Backtest chart generation — equity curve, drawdown, monthly returns, PnL distribution.

Usage:
    from stock_signal_analyzer.backtest_charts import generate_backtest_plots
    paths = generate_backtest_plots(result, output_dir="./data/backtest_charts/")

Requires matplotlib (auto-skipped with warning if missing).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from tools.backtest_v2 import BacktestResult

_log = logging.getLogger(__name__)


def _ensure_matplotlib():
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        return plt
    except Exception as exc:
        _log.warning("matplotlib unavailable: %s", exc)
        return None


def _plot_equity_curve(equity_curve: list[Any], output_path: str) -> None:
    """Equity curve with peak and drawdown shading."""
    plt = _ensure_matplotlib()
    if plt is None:
        return

    dates = [datetime.strptime(ep.date, "%Y-%m-%d") for ep in equity_curve]
    equity = [ep.equity for ep in equity_curve]
    peak = np.maximum.accumulate(equity)
    drawdown = [(p - e) / p * 100 for p, e in zip(peak, equity)]

    fig, ax = plt.subplots(figsize=(12, 5))
    ax.fill_between(dates, equity, peak, color="red", alpha=0.3, label="Drawdown")
    ax.plot(dates, equity, color="#00ff88", linewidth=1.5, label="Equity")
    ax.plot(dates, peak, color="#888888", linewidth=0.8, linestyle="--", label="Peak")
    ax.set_title("Equity Curve", fontsize=14, fontweight="bold", color="white")
    ax.set_xlabel("Date", color="white")
    ax.set_ylabel("Capital", color="white")
    ax.legend(loc="upper left")
    ax.grid(True, alpha=0.2)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150, facecolor="#1a1a1a")
    plt.close(fig)


def _plot_drawdown(equity_curve: list[Any], output_path: str) -> None:
    """Underwater (drawdown) area chart."""
    plt = _ensure_matplotlib()
    if plt is None:
        return

    dates = [datetime.strptime(ep.date, "%Y-%m-%d") for ep in equity_curve]
    equity = [ep.equity for ep in equity_curve]
    peak = np.maximum.accumulate(equity)
    drawdown = [(p - e) / p * 100 for p, e in zip(peak, equity)]

    fig, ax = plt.subplots(figsize=(12, 4))
    ax.fill_between(dates, drawdown, 0, color="red", alpha=0.5)
    ax.plot(dates, drawdown, color="red", linewidth=1.0)
    ax.set_title("Drawdown", fontsize=14, fontweight="bold", color="white")
    ax.set_xlabel("Date", color="white")
    ax.set_ylabel("Drawdown %", color="white")
    ax.grid(True, alpha=0.2)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150, facecolor="#1a1a1a")
    plt.close(fig)


def _plot_monthly_returns(trades: list[Any], output_path: str) -> None:
    """Bar chart of monthly returns."""
    plt = _ensure_matplotlib()
    if plt is None:
        return

    monthly: dict[str, float] = defaultdict(float)
    for t in trades:
        key = t.entry_date[:7]  # YYYY-MM
        monthly[key] += t.pnl_pct

    if not monthly:
        return

    labels = sorted(monthly.keys())
    values = [monthly[l] for l in labels]
    colors = ["#00ff88" if v >= 0 else "#ff4444" for v in values]

    fig, ax = plt.subplots(figsize=(12, 4))
    ax.bar(labels, values, color=colors, width=0.7)
    ax.axhline(0, color="white", linewidth=0.5)
    ax.set_title("Monthly Returns", fontsize=14, fontweight="bold", color="white")
    ax.set_xlabel("Month", color="white")
    ax.set_ylabel("Return %", color="white")
    ax.grid(True, alpha=0.2, axis="y")
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha="right")
    fig.tight_layout()
    fig.savefig(output_path, dpi=150, facecolor="#1a1a1a")
    plt.close(fig)


def _plot_pnl_distribution(trades: list[Any], output_path: str) -> None:
    """Histogram of trade PnL."""
    plt = _ensure_matplotlib()
    if plt is None:
        return

    pnls = [t.pnl_pct for t in trades]
    if not pnls:
        return

    fig, ax = plt.subplots(figsize=(10, 5))
    n_bins = max(10, min(40, len(pnls) // 3))
    ax.hist(pnls, bins=n_bins, color="#00aaff", edgecolor="white", alpha=0.7)
    ax.axvline(0, color="white", linewidth=0.8, linestyle="--")
    ax.set_title("PnL Distribution per Trade", fontsize=14, fontweight="bold", color="white")
    ax.set_xlabel("PnL %", color="white")
    ax.set_ylabel("Count", color="white")
    ax.grid(True, alpha=0.2, axis="y")
    fig.tight_layout()
    fig.savefig(output_path, dpi=150, facecolor="#1a1a1a")
    plt.close(fig)


def _plot_tier_stats(by_tier: dict[str, dict[str, float]], output_path: str) -> None:
    """Grouped bar chart: win rate and avg PnL by tier."""
    plt = _ensure_matplotlib()
    if plt is None:
        return

    if not by_tier:
        return

    tiers = sorted(by_tier.keys())
    win_rates = [by_tier[t].get("win_rate", 0.0) for t in tiers]
    avg_pnls = [by_tier[t].get("avg_pnl", 0.0) for t in tiers]

    x = np.arange(len(tiers))
    width = 0.35

    fig, ax1 = plt.subplots(figsize=(8, 5))
    bars1 = ax1.bar(x - width / 2, win_rates, width, label="Win Rate %", color="#00ff88", alpha=0.8)
    ax1.set_ylabel("Win Rate %", color="white")
    ax1.set_xticks(x)
    ax1.set_xticklabels(tiers)
    ax1.tick_params(axis="y", labelcolor="white")

    ax2 = ax1.twinx()
    bars2 = ax2.bar(x + width / 2, avg_pnls, width, label="Avg PnL %", color="#ffaa00", alpha=0.8)
    ax2.set_ylabel("Avg PnL %", color="white")
    ax2.tick_params(axis="y", labelcolor="white")
    ax2.axhline(0, color="white", linewidth=0.5)

    ax1.set_title("Performance by Tier", fontsize=14, fontweight="bold", color="white")
    ax1.grid(True, alpha=0.2, axis="y")
    fig.legend(loc="upper right", bbox_to_anchor=(0.95, 0.95))
    fig.tight_layout()
    fig.savefig(output_path, dpi=150, facecolor="#1a1a1a")
    plt.close(fig)


def _write_text_report(result: Any, output_path: str) -> None:
    """Save a plain-text summary alongside charts."""
    lines = [
        "Backtest Report",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        f"Total trades: {result.total_trades}",
        f"Win rate: {result.win_rate:.1f}%",
        f"Total return: {getattr(result, 'total_return_pct', 0.0):+.2f}%",
        f"Avg PnL per trade: {result.avg_pnl_pct:+.3f}%",
        f"Profit factor: {result.profit_factor:.2f}",
        f"Sharpe: {result.sharpe_ratio:.2f}",
        f"Sortino: {result.sortino_ratio:.2f}",
        f"Calmar: {result.calmar_ratio:.2f}",
        f"Max drawdown: -{result.max_drawdown_pct:.2f}%",
        f"Avg hold days: {result.avg_hold_days:.1f}",
        "",
        "By tier:",
    ]
    for tier, stats in sorted(result.by_tier.items()):
        lines.append(
            f"  {tier}: {stats['count']:.0f} trades, win {stats['win_rate']:.1f}%, "
            f"avg {stats['avg_pnl']:+.2f}%, total {stats['total_pnl']:+.2f}%"
        )
    Path(output_path).write_text("\n".join(lines), encoding="utf-8")


def generate_backtest_plots(result: Any, output_dir: str) -> list[str]:
    """
    Generate PNG charts and text report for a BacktestResult.

    Returns list of generated file paths.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    files: list[str] = []

    plt = _ensure_matplotlib()
    if plt is None:
        _log.warning("matplotlib not available, skipping charts")
        return files

    # Apply dark style globally for this session
    import matplotlib.pyplot as _plt
    _plt.style.use("dark_background")

    charts = [
        ("equity_curve", _plot_equity_curve, result.equity_curve),
        ("drawdown", _plot_drawdown, result.equity_curve),
        ("monthly_returns", _plot_monthly_returns, result.trades),
        ("pnl_distribution", _plot_pnl_distribution, result.trades),
        ("tier_stats", _plot_tier_stats, result.by_tier),
    ]

    for name, plot_fn, data in charts:
        if not data:
            continue
        path = str(out / f"{name}_{ts}.png")
        try:
            plot_fn(data, path)
            files.append(path)
        except Exception as exc:
            _log.warning("Chart %s failed: %s", name, exc)

    # Text report
    report_path = str(out / f"report_{ts}.txt")
    try:
        _write_text_report(result, report_path)
        files.append(report_path)
    except Exception as exc:
        _log.warning("Text report failed: %s", exc)

    return files
