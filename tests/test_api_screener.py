"""Tests for the FastAPI screener endpoint."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.main import app, _screen_cache


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_screen_cache():
    _screen_cache.clear()
    yield
    _screen_cache.clear()


class FakeTradePlan:
    direction = "long"
    entry_price = 100.0
    stop_price = 95.0
    target1_price = 110.0
    target2_price = 120.0
    max_hold_days = 15
    risk_reward = 2.0
    position_size_pct = 5.0
    rationale = "test"


class FakeReport:
    symbol = "AAPL"
    company = "Apple Inc"
    score = 0.45
    ml_score = None
    signal_tier = "A"
    confidence = 0.72
    verdict = "Bullish"
    technical_score = 0.3
    momentum_score = 0.2
    news_score = 0.1
    volume_score = 0.05
    adx14 = 25.0
    atr_pct = 1.5
    trade_plan = FakeTradePlan()


def test_screen_get_basic(client, monkeypatch):
    calls = []

    def fake_build_report(symbol, fast_mode=False):
        calls.append(symbol)
        r = FakeReport()
        r.symbol = symbol
        return r

    monkeypatch.setattr("api.main._run_screen_single", fake_build_report)
    monkeypatch.setattr("api.main.trade_plan_to_dict", lambda tp: {"direction": tp.direction})

    response = client.get("/screen?market=us&max_results=5")
    assert response.status_code == 200
    data = response.json()
    assert data["market"] == "us"
    assert "screened_at" in data
    assert isinstance(data["results"], list)
    assert len(data["results"]) > 0


def test_screen_post_filter_by_market(client, monkeypatch):
    def fake_build_report(symbol, fast_mode=False):
        r = FakeReport()
        r.symbol = symbol
        r.score = 0.5 if "ME" in symbol else 0.3
        return r

    monkeypatch.setattr("api.main._run_screen_single", fake_build_report)
    monkeypatch.setattr("api.main.trade_plan_to_dict", lambda tp: {"direction": tp.direction})

    response = client.post("/screen", json={"market": "ru", "max_results": 10})
    assert response.status_code == 200
    data = response.json()
    assert data["market"] == "ru"
    assert all(".ME" in item["symbol"] for item in data["results"])


def test_screen_min_score_filter(client, monkeypatch):
    def fake_build_report(symbol, fast_mode=False):
        r = FakeReport()
        r.symbol = symbol
        r.score = 0.05
        return r

    monkeypatch.setattr("api.main._run_screen_single", fake_build_report)

    response = client.get("/screen?market=us&min_score=0.1&max_results=50")
    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 0


def test_screen_caching(client, monkeypatch):
    call_count = [0]

    def fake_build_report(symbol, fast_mode=False):
        call_count[0] += 1
        r = FakeReport()
        r.symbol = symbol
        return r

    monkeypatch.setattr("api.main._run_screen_single", fake_build_report)
    monkeypatch.setattr("api.main.trade_plan_to_dict", lambda tp: {"direction": tp.direction})

    response1 = client.get("/screen?market=us&max_results=3")
    assert response1.status_code == 200
    count1 = call_count[0]

    response2 = client.get("/screen?market=us&max_results=3")
    assert response2.status_code == 200
    count2 = call_count[0]

    # Second request should hit cache (no new calls)
    assert count1 == count2


def test_screen_invalid_market(client):
    response = client.post("/screen", json={"market": "xx", "max_results": 10})
    assert response.status_code == 200
    data = response.json()
    # Unknown market falls back to "all" universe mapping; if empty it returns 0 results
    assert isinstance(data["results"], list)
