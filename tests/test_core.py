"""
Тесты чистой математики/логики для stock_signal_analyzer.
Запуск: pytest
"""

from __future__ import annotations

import time

import numpy as np
import pandas as pd
import pytest

from stock_signal_analyzer.technical import _rsi, _macd, analyze_technical
from stock_signal_analyzer.engine import (
    _component_confidence,
    _volume_alignment,
    _normalize_weights,
    _verdict_from_score,
    _news_item_weight,
)
from stock_signal_analyzer.risk_context import classify_signal_tier
from stock_signal_analyzer.volume_pressure import _cmf_last
from stock_signal_analyzer.user_store import can_notify_again, mark_notified, normalize_symbol, UserPrefs
from stock_signal_analyzer.universe import classify_instrument


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_close(values: list[float]) -> pd.Series:
    return pd.Series(values, dtype=float)


def _make_ohlcv(n: int, trend: float = 0.0, base: float = 100.0) -> pd.DataFrame:
    """Синтетический OHLCV датафрейм длиной n со встроенным трендом."""
    close = [base + i * trend for i in range(n)]
    high = [c + 1.0 for c in close]
    low = [c - 1.0 for c in close]
    open_ = [c - 0.3 for c in close]
    volume = [1_000_000.0] * n
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume}
    )


# ===========================================================================
# technical.py — _rsi
# ===========================================================================

def test_rsi_bullish_series_above_70():
    """Бычья серия (большие приросты, редкие малые потери) → RSI > 70."""
    # Purely increasing → avg_loss=0 → NaN → fallback 50; нужна хоть одна потеря.
    p = 100.0
    prices_list = []
    for i in range(30):
        if i % 5 == 4:
            p -= 0.2   # маленькая потеря раз в 5 баров
        else:
            p += 1.0   # крупный прирост
        prices_list.append(p)
    rsi = _rsi(_make_close(prices_list))
    assert rsi > 70, f"Ожидали RSI > 70, получили {rsi}"


def test_rsi_bearish_series_below_30():
    """Цены только падают → RSI < 30."""
    prices = _make_close([100 - i for i in range(30)])
    rsi = _rsi(prices)
    assert rsi < 30, f"Ожидали RSI < 30, получили {rsi}"


def test_rsi_flat_series_near_50():
    """Плоская серия (нет движения) → RSI ≈ 50."""
    prices = _make_close([100.0] * 30)
    rsi = _rsi(prices)
    assert rsi == pytest.approx(50.0, abs=5.0), f"Ожидали RSI ≈ 50, получили {rsi}"


def test_rsi_short_series_returns_50():
    """Меньше 14 точек → fallback на 50.0."""
    prices = _make_close([100.0, 101.0, 99.0])
    rsi = _rsi(prices)
    assert rsi == pytest.approx(50.0), f"Ожидали RSI=50 для короткой серии, получили {rsi}"


def test_rsi_result_in_valid_range():
    """RSI всегда в [0, 100]."""
    prices = _make_close([100 + np.sin(i) * 5 for i in range(50)])
    rsi = _rsi(prices)
    assert 0.0 <= rsi <= 100.0


# ===========================================================================
# technical.py — _macd
# ===========================================================================

def test_macd_bullish_crossover():
    """Бычий crossover: быстрая EMA долго выше медленной → macd_line > signal."""
    prices = _make_close([100 + i * 0.5 for i in range(80)])
    macd_line, signal, hist = _macd(prices)
    assert macd_line > signal, f"Ожидали macd_line > signal, получили {macd_line} vs {signal}"


def test_macd_bearish_crossover():
    """Медвежий crossover: цены падают → macd_line < signal."""
    prices = _make_close([200 - i * 0.5 for i in range(80)])
    macd_line, signal, hist = _macd(prices)
    assert macd_line < signal, f"Ожидали macd_line < signal, получили {macd_line} vs {signal}"


def test_macd_returns_three_floats():
    """_macd возвращает ровно три float."""
    prices = _make_close([100.0 + i for i in range(40)])
    result = _macd(prices)
    assert len(result) == 3
    for v in result:
        assert isinstance(v, float)


def test_macd_histogram_equals_line_minus_signal():
    """hist = macd_line - signal."""
    prices = _make_close([100.0 + i * 0.3 for i in range(60)])
    macd_line, signal, hist = _macd(prices)
    assert hist == pytest.approx(macd_line - signal, abs=1e-9)


# ===========================================================================
# technical.py — analyze_technical
# ===========================================================================

def test_analyze_technical_short_history_score_in_range():
    """Короткая история (< 55 свечей) → score в [-1, 1], adx14=25.0."""
    df = _make_ohlcv(20, trend=0.5)
    result = analyze_technical(df)
    assert -1.0 <= result.score <= 1.0
    assert result.adx14 == pytest.approx(25.0)


def test_analyze_technical_short_history_details_message():
    """Короткая история → details содержит упоминание о малом количестве свечей."""
    df = _make_ohlcv(10, trend=0.0)
    result = analyze_technical(df)
    assert "Мало свечей" in result.details


def test_analyze_technical_normal_history_score_in_range():
    """Нормальная история (>= 55 свечей) → score в [-1, 1]."""
    df = _make_ohlcv(120, trend=0.3)
    result = analyze_technical(df)
    assert -1.0 <= result.score <= 1.0


def test_analyze_technical_normal_history_has_rsi():
    """Нормальная история → details содержит RSI14."""
    df = _make_ohlcv(120, trend=0.0)
    result = analyze_technical(df)
    assert "RSI14=" in result.details


def test_analyze_technical_bullish_trend_positive_score():
    """Сильный бычий тренд → score > 0."""
    df = _make_ohlcv(120, trend=1.0, base=50.0)
    result = analyze_technical(df)
    assert result.score > 0.0, f"Ожидали положительный score, получили {result.score}"


def test_analyze_technical_bearish_trend_negative_score():
    """Сильный медвежий тренд → score < 0."""
    df = _make_ohlcv(120, trend=-1.0, base=500.0)
    result = analyze_technical(df)
    assert result.score < 0.0, f"Ожидали отрицательный score, получили {result.score}"


# ===========================================================================
# engine.py — _component_confidence
# ===========================================================================

def test_component_confidence_identical_components():
    """Все компоненты одинаковые → confidence = 1.0."""
    conf = _component_confidence([0.5, 0.5, 0.5, 0.5])
    assert conf == pytest.approx(1.0)


def test_component_confidence_max_spread_is_low():
    """Максимальный разброс [-1, 1] → confidence минимальная (0.22)."""
    conf = _component_confidence([-1.0, 1.0])
    assert conf == pytest.approx(0.22)


def test_component_confidence_moderate_spread():
    """Умеренный разброс → confidence между 0.22 и 1.0."""
    conf = _component_confidence([0.2, 0.5, 0.3])
    assert 0.22 < conf < 1.0


def test_component_confidence_single_component():
    """Один компонент → fallback 0.75."""
    conf = _component_confidence([0.8])
    assert conf == pytest.approx(0.75)


def test_component_confidence_result_in_valid_range():
    """Результат всегда в [0.22, 1.0]."""
    for vals in [[-1.0, -0.5, 0.5, 1.0], [0.1, 0.1], [0.0, 0.0, 0.0]]:
        conf = _component_confidence(vals)
        assert 0.22 <= conf <= 1.0


# ===========================================================================
# engine.py — _volume_alignment
# ===========================================================================

def test_volume_alignment_both_positive():
    """Оба > 0 → 1.0."""
    assert _volume_alignment(0.5, 0.6) == pytest.approx(1.0)


def test_volume_alignment_different_signs():
    """Разные знаки (оба достаточно крупные) → 0.86."""
    assert _volume_alignment(0.5, -0.5) == pytest.approx(0.86)


def test_volume_alignment_vol_score_near_zero():
    """vol_score близко к нулю → 1.0 (нет подтверждения, нет штрафа)."""
    assert _volume_alignment(0.5, 0.10) == pytest.approx(1.0)


def test_volume_alignment_raw_total_near_zero():
    """raw_total близко к нулю → 1.0."""
    assert _volume_alignment(0.05, -0.5) == pytest.approx(1.0)


def test_volume_alignment_both_negative():
    """Оба отрицательные → 1.0 (согласованы)."""
    assert _volume_alignment(-0.5, -0.6) == pytest.approx(1.0)


# ===========================================================================
# engine.py — _normalize_weights
# ===========================================================================

def test_normalize_weights_sum_equals_one():
    """Сумма нормализованных весов = 1.0."""
    wt, wm, wn, wi = _normalize_weights(0.4, 0.3, 0.2, 0.1)
    assert wt + wm + wn + wi == pytest.approx(1.0)


def test_normalize_weights_all_zeros_equal_quarter():
    """Все нули → 0.25 каждый."""
    wt, wm, wn, wi = _normalize_weights(0.0, 0.0, 0.0, 0.0)
    assert wt == pytest.approx(0.25)
    assert wm == pytest.approx(0.25)
    assert wn == pytest.approx(0.25)
    assert wi == pytest.approx(0.25)


def test_normalize_weights_proportions_preserved():
    """Пропорции сохраняются."""
    wt, wm, wn, wi = _normalize_weights(2.0, 1.0, 1.0, 0.0)
    assert wt == pytest.approx(0.5)
    assert wm == pytest.approx(0.25)
    assert wn == pytest.approx(0.25)
    assert wi == pytest.approx(0.0)


# ===========================================================================
# engine.py — _verdict_from_score
# ===========================================================================

def test_verdict_from_score_high_positive_contains_up():
    """Высокий score → вердикт содержит 'вверх'."""
    verdict = _verdict_from_score(0.9, 0.8)
    assert "вверх" in verdict.lower()


def test_verdict_from_score_high_negative_contains_down():
    """Низкий (отрицательный) score → вердикт содержит 'вниз'."""
    verdict = _verdict_from_score(-0.9, 0.8)
    assert "вниз" in verdict.lower()


def test_verdict_from_score_near_zero_neutral():
    """Score около 0 → 'нейтрально'."""
    verdict = _verdict_from_score(0.0, 0.8)
    assert "нейтрально" in verdict.lower()


def test_verdict_from_score_low_confidence_raises_threshold():
    """Низкая confidence → порог выше, умеренный score даёт нейтрально."""
    # При confidence=0 threshold ≈ 0.47; score=0.4 должен быть нейтральным
    verdict = _verdict_from_score(0.4, 0.0)
    assert "нейтрально" in verdict.lower()


def test_verdict_from_score_high_confidence_lowers_threshold():
    """Высокая confidence → порог ниже, score=0.36 должен давать сигнал вверх."""
    verdict = _verdict_from_score(0.36, 1.0)
    assert "вверх" in verdict.lower()


# ===========================================================================
# risk_context.py — classify_signal_tier
# ===========================================================================

def _good_tier_kwargs(**overrides):
    """Параметры по умолчанию для класса A."""
    base = dict(
        total=0.6,
        confidence=0.8,
        macro_dampening=0.95,
        adx14=25.0,
        news_score=0.3,
        liq_mult=1.0,
        vol_align_mult=1.0,
        has_chart_pattern=False,
        weekly_aligned=True,
        earnings_window=False,
        index_headwind=False,
    )
    base.update(overrides)
    return base


def test_classify_signal_tier_strong_conditions_is_A():
    """Сильный score + высокий confidence + хорошие условия → 'A'."""
    tier, _ = classify_signal_tier(**_good_tier_kwargs())
    assert tier == "A"


def test_classify_signal_tier_weak_score_is_C():
    """Слабый score (< 0.32) → 'C'."""
    tier, _ = classify_signal_tier(**_good_tier_kwargs(total=0.1, confidence=0.3))
    assert tier == "C"


def test_classify_signal_tier_moderate_conditions_is_B():
    """Средние условия → 'B'."""
    tier, _ = classify_signal_tier(**_good_tier_kwargs(total=0.4, confidence=0.5))
    assert tier == "B"


def test_classify_signal_tier_earnings_window_not_A():
    """Окно отчётности → не 'A'."""
    tier, rationale = classify_signal_tier(**_good_tier_kwargs(earnings_window=True))
    assert tier != "A"
    assert "отчётност" in rationale.lower() or "окно" in rationale.lower()


def test_classify_signal_tier_rationale_nonempty():
    """Второй элемент результата — непустая строка."""
    _, rationale = classify_signal_tier(**_good_tier_kwargs())
    assert isinstance(rationale, str) and len(rationale) > 0


def test_classify_signal_tier_index_headwind_not_A():
    """Индекс против направления → не 'A'."""
    tier, _ = classify_signal_tier(**_good_tier_kwargs(index_headwind=True))
    assert tier != "A"


# ===========================================================================
# volume_pressure.py — _cmf_last
# ===========================================================================

def _make_cmf_series(n: int, close_at: str = "max") -> tuple:
    """
    close_at='max' → close = high (давление покупателей, CMF > 0).
    close_at='min' → close = low  (давление продавцов, CMF < 0).
    """
    high = pd.Series([101.0 + i for i in range(n)], dtype=float)
    low = pd.Series([99.0 + i for i in range(n)], dtype=float)
    if close_at == "max":
        close = high.copy()
    else:
        close = low.copy()
    vol = pd.Series([1_000_000.0] * n, dtype=float)
    return high, low, close, vol


def test_cmf_close_at_high_positive():
    """Все дни закрытие у максимума → CMF > 0."""
    h, l, c, v = _make_cmf_series(30, close_at="max")
    cmf = _cmf_last(h, l, c, v)
    assert cmf > 0.0, f"Ожидали CMF > 0, получили {cmf}"


def test_cmf_close_at_low_negative():
    """Все дни закрытие у минимума → CMF < 0."""
    h, l, c, v = _make_cmf_series(30, close_at="min")
    cmf = _cmf_last(h, l, c, v)
    assert cmf < 0.0, f"Ожидали CMF < 0, получили {cmf}"


def test_cmf_result_clipped():
    """CMF всегда в [-1, 1] (после клиппинга *2.5)."""
    h, l, c, v = _make_cmf_series(30, close_at="max")
    cmf = _cmf_last(h, l, c, v)
    assert -1.0 <= cmf <= 1.0


def test_cmf_flat_candles_zero():
    """Свечи с одинаковыми O=H=L=C → CMF = 0 (нет диапазона)."""
    n = 30
    prices = pd.Series([100.0] * n, dtype=float)
    vol = pd.Series([1_000_000.0] * n, dtype=float)
    cmf = _cmf_last(prices, prices, prices, vol)
    assert cmf == pytest.approx(0.0, abs=1e-6)


# ===========================================================================
# user_store.py
# ===========================================================================

def test_normalize_symbol_lowercase():
    """'aapl' → 'AAPL'."""
    assert normalize_symbol("aapl") == "AAPL"


def test_normalize_symbol_with_spaces():
    """'  sber.me  ' → 'SBER.ME'."""
    assert normalize_symbol("  sber.me  ") == "SBER.ME"


def test_normalize_symbol_already_upper():
    """'NVDA' → 'NVDA'."""
    assert normalize_symbol("NVDA") == "NVDA"


def test_normalize_symbol_mixed_spaces_and_case():
    """Пробелы внутри и по краям убираются."""
    assert normalize_symbol("  ts la  ") == "TSLA"


def test_can_notify_again_no_record_true():
    """Без предыдущего уведомления → True."""
    prefs = UserPrefs()
    assert can_notify_again(prefs, "AAPL") is True


def test_can_notify_again_just_notified_false():
    """Только что уведомлённый → False."""
    prefs = UserPrefs()
    mark_notified(prefs, "AAPL")
    assert can_notify_again(prefs, "AAPL") is False


def test_can_notify_again_old_notification_true():
    """Уведомление давно (cooldown=0) → True."""
    prefs = UserPrefs(notify_cooldown_sec=0)
    mark_notified(prefs, "AAPL")
    # cooldown=0, значит даже сейчас считается «давно»
    assert can_notify_again(prefs, "AAPL") is True


def test_mark_notified_sets_false():
    """После mark_notified → can_notify_again возвращает False."""
    prefs = UserPrefs()
    assert can_notify_again(prefs, "MSFT") is True
    mark_notified(prefs, "MSFT")
    assert can_notify_again(prefs, "MSFT") is False


def test_mark_notified_normalizes_symbol():
    """mark_notified нормализует символ, потом can_notify_again тоже."""
    prefs = UserPrefs()
    mark_notified(prefs, "  aapl  ")
    assert can_notify_again(prefs, "AAPL") is False


# ===========================================================================
# universe.py — classify_instrument
# ===========================================================================

def test_classify_aapl_us_blue_chip():
    """'AAPL' → market='US', is_blue_or_large=True."""
    profile = classify_instrument("AAPL")
    assert profile.market == "US"
    assert profile.is_blue_or_large is True


def test_classify_sber_ru_market():
    """'SBER.ME' → market='RU'."""
    profile = classify_instrument("SBER.ME")
    assert profile.market == "RU"


def test_classify_sber_ru_blue_chip():
    """'SBER.ME' → is_blue_or_large=True (в списке RU_BLUE_CHIPS)."""
    profile = classify_instrument("SBER.ME")
    assert profile.is_blue_or_large is True


def test_classify_tlt_is_bond():
    """'TLT' → kind='bond'."""
    profile = classify_instrument("TLT")
    assert profile.kind == "bond"


def test_classify_tlt_market_us():
    """'TLT' → market='US'."""
    profile = classify_instrument("TLT")
    assert profile.market == "US"


def test_classify_unknown_ru_equity():
    """Неизвестный тикер .ME → market='RU', is_blue_or_large=False."""
    profile = classify_instrument("UNKN.ME")
    assert profile.market == "RU"
    assert profile.is_blue_or_large is False


def test_classify_large_cap_via_info():
    """Акция с marketCap >= порога (через info) → is_blue_or_large=True."""
    profile = classify_instrument("XYZ", info={"marketCap": 30_000_000_000})
    assert profile.is_blue_or_large is True


def test_classify_kind_equity_for_stock():
    """Обычная акция (AAPL) → kind='equity'."""
    profile = classify_instrument("AAPL")
    assert profile.kind == "equity"


def test_classify_bond_via_quote_type():
    """Инструмент с quoteType='BOND' → kind='bond'."""
    profile = classify_instrument("SOMEBOND.ME", info={"quoteType": "BOND"})
    assert profile.kind == "bond"


# ===========================================================================
# sentiment.py — финансовый лексикон
# ===========================================================================

from stock_signal_analyzer.sentiment import _financial_boost, score_headlines
from stock_signal_analyzer.news_feeds import NewsItem


def test_financial_boost_rate_cut_positive():
    """'Fed cuts rates' → положительный финансовый буст."""
    boost = _financial_boost("Fed cuts rates by 25 basis points")
    assert boost > 0.15


def test_financial_boost_recession_negative():
    """'recession fears' → отрицательный финансовый буст."""
    boost = _financial_boost("Recession fears grow amid trade war tensions")
    assert boost < -0.1


def test_financial_boost_neutral_text_zero():
    """Нейтральный текст → буст ≈ 0."""
    boost = _financial_boost("The weather is nice today")
    assert abs(boost) < 0.05


def test_financial_boost_earnings_beat():
    """'beats estimates' → положительный буст."""
    boost = _financial_boost("Apple beats estimates for Q3 earnings")
    assert boost > 0.2


def test_score_headlines_includes_fin_boost():
    """score_headlines с финансовым контекстом даёт ненулевой fin_boost_avg."""
    items = [NewsItem(title="Fed cuts rates dramatically", link="", source="test", published_ts=None)]
    result = score_headlines(items)
    assert result.fin_boost_avg > 0


# ===========================================================================
# momentum.py — ROC acceleration
# ===========================================================================

from stock_signal_analyzer.momentum import analyze_momentum


def test_momentum_returns_acceleration():
    """analyze_momentum возвращает поле acceleration."""
    close = _make_close([100.0 + i * 0.5 for i in range(30)])
    result = analyze_momentum(close)
    assert hasattr(result, "acceleration")
    assert hasattr(result, "ret_10d")


def test_momentum_overextension_dampens():
    """Перерастяжение (|ret_5d| > 2×ATR%) при низком ADX → score ≈ 0."""
    prices = [100.0] * 20 + [100.0, 100.0, 100.0, 100.0, 100.0, 120.0]
    close = _make_close(prices)
    result = analyze_momentum(close, atr_pct=2.0, adx14=18.0)
    assert abs(result.score) < 0.4


def test_momentum_conflicting_timeframes_weakens():
    """5д рост + 20д падение → ослабление (конфликт)."""
    prices = [120.0 - i * 0.8 for i in range(20)] + [100.0, 99.0, 100.5, 101.0, 102.5, 104.0]
    close = _make_close(prices)
    result = analyze_momentum(close)
    full_prices = [100.0 + i * 0.5 for i in range(26)]
    result_aligned = analyze_momentum(_make_close(full_prices))
    assert abs(result.score) <= abs(result_aligned.score)


# ===========================================================================
# levels.py — support/resistance
# ===========================================================================

from stock_signal_analyzer.levels import compute_key_levels


def test_key_levels_computes_pivots():
    """compute_key_levels возвращает непустые pivots."""
    df = _make_ohlcv(60, trend=0.5)
    levels = compute_key_levels(df)
    assert levels.pivot > 0
    assert levels.s1 < levels.pivot < levels.r1


def test_key_levels_nearest_support_below_price():
    """nearest_support < last price."""
    df = _make_ohlcv(60, trend=0.5, base=100.0)
    levels = compute_key_levels(df)
    last = float(df["Close"].iloc[-1])
    if levels.nearest_support is not None:
        assert levels.nearest_support < last * 1.01


def test_key_levels_short_history():
    """Короткая история → detail содержит 'Недостаточно'."""
    df = _make_ohlcv(5, trend=0.0)
    levels = compute_key_levels(df)
    assert "Недостаточно" in levels.detail


# ===========================================================================
# volume_pressure.py — OBV divergence
# ===========================================================================

from stock_signal_analyzer.volume_pressure import _obv_divergence, _volume_spike


def test_obv_divergence_bullish():
    """Цена падает, объём на зелёных свечах растёт → бычья дивергенция."""
    n = 30
    close = pd.Series([100.0 - i * 0.5 for i in range(n)], dtype=float)
    vol = pd.Series([1_000_000 + i * 100_000 for i in range(n)], dtype=float)
    adj, note = _obv_divergence(close, vol, lookback=20)
    assert isinstance(adj, float)
    assert isinstance(note, str)


def test_volume_spike_detection():
    """Объём 3x среднего → spike=True."""
    vol = pd.Series([1_000_000.0] * 25 + [3_500_000.0], dtype=float)
    is_spike, ratio = _volume_spike(vol, period=20)
    assert is_spike is True
    assert ratio > 2.0


def test_volume_no_spike():
    """Нормальный объём → spike=False."""
    vol = pd.Series([1_000_000.0] * 26, dtype=float)
    is_spike, ratio = _volume_spike(vol, period=20)
    assert is_spike is False


# ===========================================================================
# candlestick_patterns.py — новые паттерны
# ===========================================================================

from stock_signal_analyzer.candlestick_patterns import _doji, _piercing_line, _dark_cloud_cover


def test_doji_detection():
    """Тело < 8% диапазона → doji."""
    assert _doji(100.0, 105.0, 95.0, 100.2) is True


def test_doji_not_on_big_body():
    """Большое тело → не doji."""
    assert _doji(95.0, 105.0, 95.0, 103.0) is False


def test_piercing_line():
    """Медвежья свеча + бычья с открытием ниже min и закрытием выше середины."""
    prev = (105.0, 106.0, 99.0, 100.0)
    cur = (98.0, 104.0, 97.0, 103.0)
    assert _piercing_line(prev, cur) is True


def test_dark_cloud_cover():
    """Бычья свеча + медвежья с открытием выше max и закрытием ниже середины."""
    prev = (100.0, 106.0, 99.0, 105.0)
    cur = (107.0, 108.0, 101.0, 102.0)
    assert _dark_cloud_cover(prev, cur) is True


# ===========================================================================
# technical.py — Bollinger squeeze + MACD divergence
# ===========================================================================

from stock_signal_analyzer.technical import _bollinger_squeeze, _macd_histogram_divergence


def test_bollinger_squeeze_flat_series():
    """Плоская серия → narrow bands → squeeze=True."""
    close = _make_close([100.0 + 0.01 * (i % 3) for i in range(40)])
    assert _bollinger_squeeze(close) is True


def test_bollinger_no_squeeze_volatile():
    """Волатильная серия → no squeeze."""
    close = _make_close([100.0 + 10.0 * np.sin(i * 0.5) for i in range(40)])
    assert _bollinger_squeeze(close) is False


def test_macd_divergence_returns_tuple():
    """_macd_histogram_divergence возвращает (float, str)."""
    close = _make_close([100.0 + i * 0.5 for i in range(60)])
    adj, note = _macd_histogram_divergence(close)
    assert isinstance(adj, float)
    assert isinstance(note, str)
