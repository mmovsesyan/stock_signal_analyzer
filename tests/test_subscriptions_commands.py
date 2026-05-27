"""Tests for tier-based command rate limits and feature access."""
from __future__ import annotations

import pytest

from stock_signal_analyzer.subscriptions import (
    TIERS,
    TierLimits,
    check_command_rate_limit,
    check_feature_access,
    get_tier_limits,
)


def test_tier_limits_new_fields():
    """TierLimits should include new command fields."""
    free = get_tier_limits("free")
    assert free.screen_max_results == 5
    assert free.screen_cache_ttl == 300
    assert free.backtest_detail == "basic"
    assert free.clusters is False
    assert free.mlscore is False
    assert free.portfolio is False
    assert free.alerts is False
    assert free.screen_daily == 5

    pro = get_tier_limits("pro")
    assert pro.screen_max_results == 15
    assert pro.clusters is True
    assert pro.mlscore is False
    assert pro.portfolio is True
    assert pro.alerts is False

    premium = get_tier_limits("premium")
    assert premium.screen_max_results == 30
    assert premium.clusters is True
    assert premium.mlscore is True
    assert premium.portfolio is True
    assert premium.alerts is True


def test_check_feature_access_new_fields(monkeypatch):
    """check_feature_access should work for new fields."""
    monkeypatch.setattr("stock_signal_analyzer.subscriptions.SUBSCRIPTIONS_ENABLED", True)
    # Patch get_user_tier to return fixed tier
    monkeypatch.setattr("stock_signal_analyzer.subscriptions.get_user_tier", lambda uid: "pro")
    assert check_feature_access(1, "clusters") is True
    assert check_feature_access(1, "mlscore") is False
    assert check_feature_access(1, "portfolio") is True
    assert check_feature_access(1, "alerts") is False

    monkeypatch.setattr("stock_signal_analyzer.subscriptions.get_user_tier", lambda uid: "premium")
    assert check_feature_access(1, "mlscore") is True
    assert check_feature_access(1, "alerts") is True


def test_check_command_rate_limit_free(monkeypatch):
    """Free tier should be limited for screen command."""
    monkeypatch.setattr("stock_signal_analyzer.subscriptions.SUBSCRIPTIONS_ENABLED", True)
    monkeypatch.setattr("stock_signal_analyzer.subscriptions.get_user_tier", lambda uid: "free")
    monkeypatch.setattr(
        "stock_signal_analyzer.subscriptions._get_command_usage_db",
        lambda uid, today: {},
    )
    # bump is no-op for this test
    monkeypatch.setattr(
        "stock_signal_analyzer.subscriptions._bump_command_usage_db",
        lambda uid, today, cmd: None,
    )
    allowed, msg = check_command_rate_limit(1, "screen")
    assert allowed is True
    assert msg == ""


def test_check_command_rate_limit_exceeded(monkeypatch):
    """When daily limit exceeded, should deny."""
    monkeypatch.setattr("stock_signal_analyzer.subscriptions.SUBSCRIPTIONS_ENABLED", True)
    monkeypatch.setattr("stock_signal_analyzer.subscriptions.get_user_tier", lambda uid: "free")
    monkeypatch.setattr(
        "stock_signal_analyzer.subscriptions._get_command_usage_db",
        lambda uid, today: {"screen": 5},
    )
    monkeypatch.setattr(
        "stock_signal_analyzer.subscriptions._bump_command_usage_db",
        lambda uid, today, cmd: None,
    )
    allowed, msg = check_command_rate_limit(1, "screen")
    assert allowed is False
    assert "достигнут" in msg.lower() or "limit" in msg.lower()


def test_check_command_rate_limit_unknown_command(monkeypatch):
    """Unknown commands should pass through."""
    monkeypatch.setattr("stock_signal_analyzer.subscriptions.SUBSCRIPTIONS_ENABLED", True)
    monkeypatch.setattr("stock_signal_analyzer.subscriptions.get_user_tier", lambda uid: "free")
    allowed, msg = check_command_rate_limit(1, "unknown_cmd")
    assert allowed is True
    assert msg == ""


def test_check_command_rate_limit_disabled_tier(monkeypatch):
    """Free tier should have clusters disabled (daily limit 0)."""
    monkeypatch.setattr("stock_signal_analyzer.subscriptions.SUBSCRIPTIONS_ENABLED", True)
    monkeypatch.setattr("stock_signal_analyzer.subscriptions.get_user_tier", lambda uid: "free")
    allowed, msg = check_command_rate_limit(1, "clusters")
    assert allowed is False
    assert "недоступна" in msg.lower() or "unavailable" in msg.lower()
