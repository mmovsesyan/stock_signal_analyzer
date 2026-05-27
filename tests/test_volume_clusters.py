"""Tests for volume_clusters module."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stock_signal_analyzer.volume_clusters import (
    PriceBin,
    VolumeClusterResult,
    _find_poc,
    _hvn_lvn,
    _value_area,
    _volume_profile,
    analyze_volume_clusters,
)


class TestVolumeProfile:
    def test_empty_hist(self):
        assert _volume_profile(pd.DataFrame(), n_bins=10) == []

    def test_basic_bins(self):
        hist = pd.DataFrame({
            "High": [110, 112, 111, 113, 115],
            "Low": [100, 102, 101, 103, 105],
            "Close": [105, 107, 106, 108, 110],
            "Volume": [1000, 2000, 1500, 500, 3000],
        })
        bins = _volume_profile(hist, n_bins=5)
        assert len(bins) == 5
        total_vol = sum(b.volume for b in bins)
        assert total_vol == pytest.approx(8000, abs=1e-6)

    def test_missing_columns(self):
        hist = pd.DataFrame({"Close": [1, 2, 3], "Volume": [100, 200, 300]})
        assert _volume_profile(hist, n_bins=5) == []


class TestFindPoc:
    def test_single_max(self):
        bins = [
            PriceBin(0, 10, volume=100),
            PriceBin(10, 20, volume=500),
            PriceBin(20, 30, volume=200),
        ]
        poc, vol = _find_poc(bins)
        assert poc == 15.0
        assert vol == 500.0

    def test_tie_breaker(self):
        bins = [
            PriceBin(0, 10, volume=100),
            PriceBin(10, 20, volume=100),
            PriceBin(20, 30, volume=100),
        ]
        poc, vol = _find_poc(bins)
        assert poc == 15.0
        assert vol == 100.0

    def test_empty(self):
        assert _find_poc([]) == (None, 0.0)


class TestValueArea:
    def test_symmetric_around_poc(self):
        bins = [
            PriceBin(0, 10, volume=50),
            PriceBin(10, 20, volume=200),
            PriceBin(20, 30, volume=100),
            PriceBin(30, 40, volume=50),
            PriceBin(40, 50, volume=25),
        ]
        va_low, va_high = _value_area(bins, poc_idx=1, target_pct=0.70)
        assert va_low is not None
        assert va_high is not None
        # With 70% of 425 = 297.5, starting at POC bin (200) we need ~97.5 more
        # Next highest adjacent is bin 2 (100) so VA should be 10-30
        assert va_low == 10.0
        assert va_high == 30.0

    def test_invalid_poc_idx(self):
        assert _value_area([], poc_idx=0) == (None, None)


class TestHvnLvn:
    def test_classification(self):
        bins = [
            PriceBin(0, 10, volume=10),
            PriceBin(10, 20, volume=100),
            PriceBin(20, 30, volume=5),
        ]
        hvn, lvn = _hvn_lvn(bins)
        avg = (10 + 100 + 5) / 3  # 38.33
        assert len(hvn) == 1
        assert hvn[0][0] == 15.0
        # LVN: volume <= avg * 0.5 = 19.17, so both 10 and 5 qualify
        assert len(lvn) == 2
        lvn_prices = {p for p, _ in lvn}
        assert lvn_prices == {5.0, 25.0}

    def test_empty(self):
        assert _hvn_lvn([]) == ([], [])


class TestAnalyzeVolumeClusters:
    def test_full_analysis(self):
        np.random.seed(42)
        n = 60
        hist = pd.DataFrame({
            "High": 100 + np.cumsum(np.random.randn(n) * 0.5) + 2,
            "Low": 100 + np.cumsum(np.random.randn(n) * 0.5) - 2,
            "Close": 100 + np.cumsum(np.random.randn(n) * 0.5),
            "Volume": np.random.randint(1000, 10000, n),
        })
        result = analyze_volume_clusters(hist, n_bins=20)
        assert isinstance(result, VolumeClusterResult)
        assert result.poc is not None
        assert result.value_area_low is not None
        assert result.value_area_high is not None
        assert result.total_volume > 0
        assert result.detail != ""
        # VA should contain POC
        assert result.value_area_low <= result.poc <= result.value_area_high

    def test_no_data(self):
        result = analyze_volume_clusters(pd.DataFrame(), n_bins=10)
        assert result.poc is None
        assert result.value_area_low is None
        assert result.value_area_high is None
        assert "Недостаточно данных" in result.detail
