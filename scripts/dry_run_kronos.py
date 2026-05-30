#!/usr/bin/env python3
"""
Dry-run script for Kronos integration.

Usage:
    KRONOS_ENABLED=1 python scripts/dry_run_kronos.py
    KRONOS_ENABLED=0 python scripts/dry_run_kronos.py

Verifies that:
1. KronosComponent loads lazily and fails gracefully.
2. Score normalization stays within [-1, 1].
3. Engine dataclasses accept kronos_score without error.
4. No import regressions when Kronos deps are missing.
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _make_synthetic_hist(n: int = 80) -> pd.DataFrame:
    """Synthetic daily OHLCV compatible with Yahoo-like column names."""
    rng = np.random.default_rng(seed=42)
    trend = np.cumsum(rng.normal(0.001, 0.02, n))
    close = 100.0 * np.exp(trend)
    noise = rng.normal(0, 1.5, n)
    open_p = close + noise
    high = np.maximum(open_p, close) + rng.uniform(0.5, 2.0, n)
    low = np.minimum(open_p, close) - rng.uniform(0.5, 2.0, n)
    volume = rng.integers(1_000_000, 10_000_000, n)
    return pd.DataFrame({
        "Open": open_p,
        "High": high,
        "Low": low,
        "Close": close,
        "Volume": volume,
    })


def main() -> int:
    enabled = os.environ.get("KRONOS_ENABLED", "0") == "1"
    print(f"KRONOS_ENABLED={enabled}")

    # 1. Import smoke test
    try:
        from stock_signal_analyzer.kronos_component import KronosComponent
        from stock_signal_analyzer.engine import SignalReport
        print("[PASS] Imports succeed")
    except Exception as exc:
        print(f"[FAIL] Import error: {exc}")
        return 1

    # 2. SignalReport default kronos_score
    try:
        report = SignalReport(
            symbol="TEST",
            company="Test Inc",
            instrument_label="equity",
            verdict="neutral",
            score=0.0,
            score_before_macro=0.0,
            technical_score=0.0,
            momentum_score=0.0,
            news_score=0.0,
            technical_detail="",
            momentum_detail="",
            news_detail="",
            intraday_score=None,
            intraday_detail=None,
            macro_summary="",
            macro_dampening=1.0,
            volume_score=0.0,
            volume_detail="",
            risk_note="",
            confidence=0.5,
            adx14=20.0,
            regime_label="sideways",
            pattern_summary="",
            signal_tier="C",
            tier_rationale="",
            atr_pct=2.0,
            ref_price=100.0,
            timing_detail="",
            stop_hint_pct=1.5,
            weekly_regime="sideways",
            online_hint="",
            levels_detail="",
            trade_plan=None,
        )
        assert report.kronos_score == 0.0
        print("[PASS] SignalReport kronos_score default is 0.0")
    except Exception as exc:
        print(f"[FAIL] SignalReport error: {exc}")
        return 1

    # 3. KronosComponent score on synthetic data
    hist = _make_synthetic_hist(80)
    comp = KronosComponent()
    score, detail = comp.score(hist)
    print(f"[INFO] Kronos score={score:.4f} detail='{detail}'")

    if enabled:
        # Without model deps installed, loading will fail gracefully
        if score == 0.0 and "Kronos" in detail:
            print("[PASS] Graceful fallback when model not installed / load failed")
        elif -1.0 <= score <= 1.0:
            print("[PASS] Score within expected bounds [-1, 1]")
        else:
            print(f"[FAIL] Score out of bounds: {score}")
            return 1
    else:
        if score == 0.0 and detail == "":
            print("[PASS] Disabled mode returns zero with empty detail")
        else:
            print(f"[FAIL] Unexpected disabled result: score={score} detail={detail}")
            return 1

    # 4. DataFrame preparation logic
    try:
        df = KronosComponent._prepare_dataframe(hist)
        assert set(df.columns) >= {"open", "high", "low", "close", "volume", "amount"}
        assert len(df) >= 60
        print("[PASS] DataFrame preparation correct")
    except Exception as exc:
        print(f"[FAIL] DataFrame preparation error: {exc}")
        return 1

    print("\n[OK] All dry-run checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
