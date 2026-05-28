"""Tests for backtest_report module."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from stock_signal_analyzer.backtest_report import (
    BacktestReport,
    EquityPoint,
    TierStats,
    format_report,
)


@pytest.fixture
def empty_report():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "outcomes.jsonl"
        yield BacktestReport(outcomes_path=str(path))


@pytest.fixture
def sample_outcomes_file():
    records = [
        # Tier A long wins
        {"signal_id": "A_1", "symbol": "AAPL", "outcome": "win_t1", "pnl_pct": 5.0, "signal_tier": "A", "direction": "long", "exit_date": "2026-01-05T00:00:00+00:00", "checked_at": "2026-01-05T00:00:00+00:00"},
        {"signal_id": "A_2", "symbol": "AAPL", "outcome": "win_t2", "pnl_pct": 8.0, "signal_tier": "A", "direction": "long", "exit_date": "2026-01-10T00:00:00+00:00", "checked_at": "2026-01-10T00:00:00+00:00"},
        # Tier A short loss
        {"signal_id": "A_3", "symbol": "SBER.ME", "outcome": "loss", "pnl_pct": -4.0, "signal_tier": "A", "direction": "short", "exit_date": "2026-01-15T00:00:00+00:00", "checked_at": "2026-01-15T00:00:00+00:00"},
        # Tier B long timeout
        {"signal_id": "B_1", "symbol": "GAZP.ME", "outcome": "timeout", "pnl_pct": 1.0, "signal_tier": "B", "direction": "long", "exit_date": "2026-01-20T00:00:00+00:00", "checked_at": "2026-01-20T00:00:00+00:00"},
        # Tier C long loss
        {"signal_id": "C_1", "symbol": "T", "outcome": "loss", "pnl_pct": -6.0, "signal_tier": "C", "direction": "long", "exit_date": "2026-01-25T00:00:00+00:00", "checked_at": "2026-01-25T00:00:00+00:00"},
    ]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "outcomes.jsonl"
        with open(path, "w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec) + "\n")
        yield str(path)


class TestBacktestReport:
    def test_empty_outcomes(self, empty_report):
        result = empty_report.generate()
        assert result.total_trades == 0
        assert result.win_rate == 0.0
        assert result.max_drawdown_pct == 0.0
        assert result.equity_curve == []

    def test_basic_stats(self, sample_outcomes_file):
        report = BacktestReport(outcomes_path=sample_outcomes_file)
        result = report.generate()
        # 5 total: 2 wins, 2 losses, 1 timeout
        assert result.total_trades == 5
        assert result.wins == 2
        assert result.losses == 2
        assert result.timeouts == 1
        # Win rate among decisive trades (2 wins / 4 decisive)
        assert result.win_rate == 0.5
        # PnL in outcomes.jsonl is already net (outcome_tracker subtracts commission)
        # wins: 5.0, 8.0  avg_win = 6.5
        assert result.avg_win_pct == pytest.approx(6.5, abs=0.01)
        # losses: -4.0, -6.0  avg_loss = -5.0
        assert result.avg_loss_pct == pytest.approx(-5.0, abs=0.01)
        # all decisive net: 5.0, 8.0, -4.0, -6.0  avg = 0.75
        assert result.avg_pnl_pct == pytest.approx(0.75, abs=0.01)
        # gross profit = 5.0 + 8.0 = 13.0; gross loss = 4.0 + 6.0 = 10.0; PF = 13.0 / 10.0
        assert result.profit_factor == pytest.approx(1.30, abs=0.01)
        assert result.commission_pct == 0.0

    def test_equity_curve_and_drawdown(self, sample_outcomes_file):
        report = BacktestReport(outcomes_path=sample_outcomes_file)
        result = report.generate()
        assert len(result.equity_curve) == 5
        assert result.equity_curve[0].trade_num == 1
        # Starting equity 100, first trade net +5.0% (commission already in pnl)
        assert result.equity_curve[0].equity == pytest.approx(105.0, abs=0.01)
        # Max drawdown should be positive after a losing streak
        assert result.max_drawdown_pct >= 0.0

    def test_format_report(self, sample_outcomes_file):
        report = BacktestReport(outcomes_path=sample_outcomes_file)
        result = report.generate()
        text = format_report(result)
        assert "Бэктест-отчёт" in text
        assert "Всего сделок: 5" in text
        assert "Win rate: 50%" in text
        assert "Тир A:" in text
        assert "Тир B:" in text
        assert "Тир C:" in text
        assert "long:" in text
        assert "short:" in text

    def test_tier_breakdown(self, sample_outcomes_file):
        report = BacktestReport(outcomes_path=sample_outcomes_file)
        result = report.generate()
        assert "A" in result.by_tier
        assert "B" in result.by_tier
        assert "C" in result.by_tier

        tier_a = result.by_tier["A"]
        assert tier_a.total == 3
        assert tier_a.wins == 2
        assert tier_a.losses == 1
        assert tier_a.win_rate == pytest.approx(2 / 3, abs=0.01)

        tier_b = result.by_tier["B"]
        assert tier_b.total == 1
        assert tier_b.timeouts == 1

        tier_c = result.by_tier["C"]
        assert tier_c.total == 1
        assert tier_c.losses == 1

    def test_direction_breakdown(self, sample_outcomes_file):
        report = BacktestReport(outcomes_path=sample_outcomes_file)
        result = report.generate()
        assert "long" in result.by_direction
        assert "short" in result.by_direction
        assert result.by_direction["long"].total == 4
        assert result.by_direction["short"].total == 1

    def test_format_report_empty(self):
        empty = BacktestReport(outcomes_path="/nonexistent/outcomes.jsonl").generate()
        text = format_report(empty)
        assert "нет закрытых сделок" in text

    def test_sharpe_like(self, sample_outcomes_file):
        report = BacktestReport(outcomes_path=sample_outcomes_file)
        result = report.generate()
        # Sharpe-like = mean/std * sqrt(n)
        # Returns: 5.0, 8.0, -4.0, -6.0 — mean=0.75, std≈6.80, sharpe≈0.22
        assert result.sharpe_like == pytest.approx(0.22, abs=0.05)
