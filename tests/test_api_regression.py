"""
Regression tests for api/main.py fixes.
Found by /qa on 2026-05-16
Report: .gstack/qa-reports/qa-report-localhost-2026-05-16.md
"""

from __future__ import annotations

import pytest

# Regression: ISSUE-001 — _normalize_tv_symbol incorrectly appended .ME to US tickers
# Found by /qa on 2026-05-16
# Report: .gstack/qa-reports/qa-report-localhost-2026-05-16.md
def test_normalize_tv_symbol_us_tickers_not_russian():
    """US blue-chip tickers must NOT get .ME suffix."""
    from api.main import _normalize_tv_symbol

    us_tickers = ["IBM", "INTC", "AMD", "NFLX", "CRM", "JPM", "BAC", "KO", "PEP", "WMT"]
    for t in us_tickers:
        assert _normalize_tv_symbol(t) == t, f"{t} should stay {t}, not {t}.ME"


def test_normalize_tv_symbol_russian_tickers_get_me():
    """Known Russian blue-chip tickers MUST get .ME suffix."""
    from api.main import _normalize_tv_symbol

    ru_tickers = ["SBER", "GAZP", "LKOH", "GMKN", "NVTK", "ROSN", "TATN", "MOEX", "YDEX"]
    for t in ru_tickers:
        assert _normalize_tv_symbol(t) == f"{t}.ME", f"{t} should become {t}.ME"


def test_normalize_tv_symbol_crypto_pairs():
    """Crypto pairs normalized to Yahoo Finance format."""
    from api.main import _normalize_tv_symbol

    assert _normalize_tv_symbol("BTCUSDT") == "BTC-USD"
    assert _normalize_tv_symbol("ETHUSDT") == "ETH-USD"
    assert _normalize_tv_symbol("BINANCE:BTCUSDT") == "BTC-USD"


def test_normalize_tv_symbol_exchange_prefix_stripped():
    """Exchange prefix removed before processing."""
    from api.main import _normalize_tv_symbol

    assert _normalize_tv_symbol("MOEX:SBER") == "SBER.ME"
    assert _normalize_tv_symbol("NASDAQ:AAPL") == "AAPL"


# Regression: ISSUE-001 — rate limit cleanup lost current request tracking
# Found by /qa on 2026-05-16
def test_rate_limit_tracks_request_after_cleanup():
    """After 60s gap, the new request itself must be counted."""
    import time
    from api.main import _check_rate_limit, _rate_store

    client = "test-client-cleanup"
    # clean slate
    _rate_store.pop(client, None)

    # first request → should be allowed and recorded
    assert _check_rate_limit(client) is True
    assert len(_rate_store[client]) == 1

    # simulate 60s gap by manipulating timestamps
    old_ts = time.time() - 61
    _rate_store[client] = [old_ts]

    # next request after gap → should be allowed and recorded (not lost)
    assert _check_rate_limit(client) is True
    assert len(_rate_store[client]) == 1
    assert _rate_store[client][0] > old_ts
