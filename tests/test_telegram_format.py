"""Tests for tier-based Telegram formatters."""
from __future__ import annotations

import pytest

from stock_signal_analyzer.telegram_format import (
    format_backtest_telegram,
    format_clusters_telegram,
    format_mlscore_telegram,
    format_portfolio_telegram,
    format_screen_results,
    render_ascii_equity_curve,
)


def test_format_screen_results_empty():
    assert "Нет сигналов" in format_screen_results([])


def test_format_screen_results_basic():
    results = [
        {
            "symbol": "AAPL",
            "company": "Apple Inc",
            "score": 0.42,
            "signal_tier": "A",
            "direction": "long",
            "confidence": 0.75,
        }
    ]
    text = format_screen_results(results)
    assert "AAPL" in text
    assert "+0.42" in text
    assert "A" in text


def test_render_ascii_equity_curve():
    curve = [100.0, 102.0, 101.0, 105.0, 104.0]
    chart = render_ascii_equity_curve(curve, width=10, height=4)
    assert "┤" in chart
    assert "└" in chart


def test_render_ascii_equity_curve_empty():
    assert render_ascii_equity_curve([]) == ""


def test_format_backtest_telegram_basic():
    report = {
        "total_signals": 20,
        "win_rate": 0.55,
        "profit_factor": 1.3,
        "avg_win_pct": 3.5,
        "avg_loss_pct": 2.1,
        "total_pnl_pct": 12.0,
    }
    text = format_backtest_telegram(report, tier="free")
    assert "Бэктест" in text
    assert "20" in text
    assert "55.0%" in text or "55%" in text


def test_format_backtest_telegram_premium():
    report = {
        "total_signals": 20,
        "win_rate": 0.55,
        "profit_factor": 1.3,
        "avg_win_pct": 3.5,
        "avg_loss_pct": 2.1,
        "total_pnl_pct": 12.0,
        "breakdown": {"A_long": {"count": 10, "win_rate": 0.6, "profit_factor": 1.5}},
        "equity_curve": [100, 105, 103, 110],
        "sharpe_like": 0.8,
        "max_drawdown_pct": 5.0,
    }
    text = format_backtest_telegram(report, tier="premium")
    assert "Разбивка" in text
    assert "Equity curve" in text
    assert "Sharpe-like" in text
    assert "Max drawdown" in text


def test_format_clusters_telegram():
    result = {
        "poc": 150.0,
        "value_area_low": 148.0,
        "value_area_high": 152.0,
        "hvn_levels": [149.0, 151.0],
        "lvn_levels": [147.0, 153.0],
    }
    text = format_clusters_telegram(result)
    assert "POC" in text
    assert "150.00" in text
    assert "Value Area" in text
    assert "HVN" in text


def test_format_clusters_telegram_dataclass():
    from stock_signal_analyzer.volume_clusters import VolumeClusterResult
    result = VolumeClusterResult(
        poc=150.0,
        poc_volume=1000.0,
        value_area_low=148.0,
        value_area_high=152.0,
        hvn_levels=[(149.0, 500.0), (151.0, 600.0)],
        lvn_levels=[(147.0, 100.0)],
        total_volume=5000.0,
    )
    text = format_clusters_telegram(result)
    assert "POC" in text
    assert "150.00" in text
    assert "Value Area" in text
    assert "HVN" in text
    assert "149.00" in text
    assert "LVN" in text
    assert "147.00" in text


def test_format_clusters_telegram_empty():
    assert "нет данных" in format_clusters_telegram({})


def test_format_mlscore_telegram():
    class FakeEnsemble:
        _trained_count = 42
        _last_fit_at = "2026-05-27T10:00:00"

        def feature_importances(self):
            return {"technical": 0.3, "momentum": 0.2}

    text = format_mlscore_telegram(FakeEnsemble())
    assert "ML RankEnsemble" in text
    assert "technical" in text
    assert "42" in text


def test_format_mlscore_telegram_none():
    assert "недоступна" in format_mlscore_telegram(None)


def test_format_portfolio_telegram():
    signals = [
        {"symbol": "AAPL", "direction": "long", "entry_price": 150.0, "current_price": 160.0, "pnl_pct": 6.67},
    ]
    text = format_portfolio_telegram(signals)
    assert "AAPL" in text
    assert "6.67%" in text
    assert "Общий PnL" in text


def test_format_portfolio_telegram_empty():
    assert "Нет открытых позиций" in format_portfolio_telegram([])
