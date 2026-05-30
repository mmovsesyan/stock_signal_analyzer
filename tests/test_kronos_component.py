"""
Unit tests for KronosComponent.

These tests verify graceful degradation when Kronos is disabled or when
dependencies are missing, and correctness of the score normalization logic.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from stock_signal_analyzer.kronos_component import KronosComponent, _is_enabled


class TestIsEnabled:
    def test_default_disabled(self) -> None:
        """By default KRONOS_ENABLED is unset → False."""
        assert _is_enabled() is False

    def test_enabled_explicitly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KRONOS_ENABLED", "1")
        assert _is_enabled() is True

    def test_enabled_whitespace(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KRONOS_ENABLED", " 1 ")
        assert _is_enabled() is True


class TestPrepareDataFrame:
    def test_renames_yahoo_columns(self) -> None:
        hist = pd.DataFrame({
            "Open": [1, 2, 3],
            "High": [2, 3, 4],
            "Low": [0, 1, 2],
            "Close": [1.5, 2.5, 3.5],
            "Volume": [100, 200, 300],
        })
        df = KronosComponent._prepare_dataframe(hist)
        assert list(df.columns) == ["open", "high", "low", "close", "volume", "amount"]
        # amount = volume * mean(open, high, low, close)
        expected_amount = 100 * ((1 + 2 + 0 + 1.5) / 4)
        assert df["amount"].iloc[0] == expected_amount

    def test_missing_required_column_raises(self) -> None:
        hist = pd.DataFrame({"Open": [1, 2], "High": [2, 3], "Low": [0, 1]})
        with pytest.raises(ValueError, match="Missing required column 'close'"):
            KronosComponent._prepare_dataframe(hist)

    def test_drops_nan_rows(self) -> None:
        hist = pd.DataFrame({
            "open": [1, 2, np.nan],
            "high": [2, 3, 4],
            "low": [0, 1, 2],
            "close": [1.5, 2.5, 3.5],
            "volume": [100, 200, 300],
        })
        df = KronosComponent._prepare_dataframe(hist)
        assert len(df) == 2


class TestScoreDisabled:
    def test_returns_zero_when_disabled(self) -> None:
        comp = KronosComponent()
        # Ensure disabled (default)
        score, detail = comp.score(pd.DataFrame())
        assert score == 0.0
        assert detail == ""


class TestScoreWithMockPredictor:
    def test_score_normalization(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """
        When Kronos predicts a +3% move, score should be ~1.0.
        When it predicts -1.5%, score should be ~-0.5.
        """
        monkeypatch.setenv("KRONOS_ENABLED", "1")
        monkeypatch.setenv("KRONOS_PRED_LEN", "5")

        comp = KronosComponent()

        # Build a fake predictor that returns a 5-bar forecast with +3% final close
        fake_pred_df = pd.DataFrame({
            "open": [1, 1, 1, 1, 1],
            "high": [1, 1, 1, 1, 1],
            "low": [1, 1, 1, 1, 1],
            "close": [1.0, 1.01, 1.02, 1.029, 1.03],  # +3% final
            "volume": [0, 0, 0, 0, 0],
            "amount": [0, 0, 0, 0, 0],
        })

        fake_predictor = MagicMock()
        fake_predictor.predict.return_value = fake_pred_df
        fake_predictor.device = "cpu"
        comp._predictor = fake_predictor
        comp._loaded = True

        hist = pd.DataFrame({
            "Open": [1.0] * 70,
            "High": [1.1] * 70,
            "Low": [0.9] * 70,
            "Close": [1.0] * 70,
            "Volume": [100] * 70,
        })

        score, detail = comp.score(hist)
        assert pytest.approx(score, abs=0.01) == 1.0
        assert "+3.00%" in detail or "pred=1.03" in detail

    def test_negative_prediction(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KRONOS_ENABLED", "1")
        monkeypatch.setenv("KRONOS_PRED_LEN", "3")

        comp = KronosComponent()
        fake_pred_df = pd.DataFrame({
            "open": [1, 1, 1],
            "high": [1, 1, 1],
            "low": [1, 1, 1],
            "close": [1.0, 0.99, 0.985],  # -1.5%
            "volume": [0, 0, 0],
            "amount": [0, 0, 0],
        })

        fake_predictor = MagicMock()
        fake_predictor.predict.return_value = fake_pred_df
        fake_predictor.device = "cpu"
        comp._predictor = fake_predictor
        comp._loaded = True

        hist = pd.DataFrame({
            "Open": [1.0] * 70,
            "High": [1.1] * 70,
            "Low": [0.9] * 70,
            "Close": [1.0] * 70,
            "Volume": [100] * 70,
        })

        score, detail = comp.score(hist)
        assert pytest.approx(score, abs=0.01) == -0.5  # -1.5% / 3% = -0.5

    def test_graceful_on_predict_exception(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KRONOS_ENABLED", "1")
        comp = KronosComponent()
        fake_predictor = MagicMock()
        fake_predictor.predict.side_effect = RuntimeError("CUDA OOM")
        fake_predictor.device = "cpu"
        comp._predictor = fake_predictor
        comp._loaded = True

        hist = pd.DataFrame({
            "Open": [1.0] * 70,
            "High": [1.1] * 70,
            "Low": [0.9] * 70,
            "Close": [1.0] * 70,
            "Volume": [100] * 70,
        })

        score, detail = comp.score(hist)
        assert score == 0.0
        assert "Kronos error" in detail

    def test_insufficient_history(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KRONOS_ENABLED", "1")
        comp = KronosComponent()
        fake_predictor = MagicMock()
        comp._predictor = fake_predictor
        comp._loaded = True

        hist = pd.DataFrame({
            "Open": [1.0] * 30,
            "High": [1.1] * 30,
            "Low": [0.9] * 30,
            "Close": [1.0] * 30,
            "Volume": [100] * 30,
        })

        score, detail = comp.score(hist)
        assert score == 0.0
        assert "insufficient history" in detail
