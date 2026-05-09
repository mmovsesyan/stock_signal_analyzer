"""Объединение техники, импульса, новостей, онлайна, макро-фона и квант-моделей."""

from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

_log = logging.getLogger(__name__)

from .adaptive_weights import AdaptiveWeightsResult, compute_adaptive_weights
from .finnhub_live import fetch_company_news, fetch_recommendation_trends, fetch_earnings_surprise
from .intraday import IntradayBundle, build_intraday
from .levels import KeyLevels, compute_key_levels
from .macro_calendar import MacroContext, build_macro_context
from .market_data import TickerSnapshot, fetch_snapshot_with_meta
from .momentum import MomentumScore, analyze_momentum
from .news_feeds import NewsItem, fetch_macro_headlines, fetch_ticker_news_google
from .quant_models import (
    MtfMomentumResult,
    TrendStrengthResult,
    VolRegimeResult,
    ZScoreResult,
    analyze_mtf_momentum,
    analyze_trend_strength,
    analyze_vol_regime,
    analyze_zscore,
)
from .regime import CrossAssetRegime, build_cross_asset_regime
from .risk_context import atr_percent_14, classify_signal_tier
from .risk_manager import (
    DrawdownState,
    PositionSizeResult,
    compute_position_size,
    evaluate_drawdown,
    load_drawdown_state,
)
from .sentiment import SentimentResult, score_headlines
from .signal_log import append_signal_record, build_record_from_report, log_path_from_env
from .technical import TechnicalScore, analyze_technical
from .timing_context import (
    build_timing_context,
    index_tailwind_mult,
    stop_hint_atr_multiple,
    weekly_aligns_direction,
)
from .trade_plan import TradePlan, build_trade_plan, trade_plan_to_dict
from .universe import InstrumentProfile, select_component_weights
from .volume_pressure import VolumePressureResult, analyze_volume_pressure


# ── Константы ────────────────────────────────────────────────────────────────

VOL_BLEND = 0.08

# Штраф за несовпадение знака объёма с общим знаком сигнала
_VOL_ALIGN_PENALTY: float = 0.86

# Штраф, если интрадей-сигнал противоречит дневному знаку
_INTRADAY_CONFLICT_MULT: float = 0.92

# Штраф за несоответствие недельному тренду
_WEEKLY_MISALIGN_MULT: float = 0.96

# Пороги вердикта
_VERDICT_BASE_THR: float = 0.35
_VERDICT_CONF_SCALE: float = 0.12

# Пороги для фиксации конфликта интрадей vs дневной
_INTRADAY_CONFLICT_SCORE_THR: float = 0.17
_INTRADAY_CONFLICT_INTRA_THR: float = 0.2

# Масштабирование весов по ADX (боковик: меньше импульса, чуть больше техники)
_ADX_LOW_MOM_SCALE: float = 0.68
_ADX_LOW_TECH_SCALE: float = 1.06
_ADX_HIGH_MOM_SCALE: float = 1.06
_ADX_HIGH_TECH_SCALE: float = 1.02

# Смешивание confidence в финальном мультипликаторе
_CONF_BASE: float = 0.42
_CONF_SCALE: float = 0.58

# Штраф ликвидности при низком объёме
_LIQUIDITY_LOW_MULT: float = 0.91


# ── Публичный датакласс отчёта ────────────────────────────────────────────────

@dataclass
class SignalReport:
    symbol: str
    company: str
    instrument_label: str
    verdict: str
    score: float
    score_before_macro: float
    technical_score: float
    momentum_score: float
    news_score: float
    technical_detail: str
    momentum_detail: str
    news_detail: str
    intraday_score: float | None
    intraday_detail: str | None
    macro_summary: str
    macro_dampening: float
    volume_score: float
    volume_detail: str
    risk_note: str
    confidence: float
    adx14: float
    regime_label: str
    pattern_summary: str
    signal_tier: str
    tier_rationale: str
    atr_pct: float | None
    ref_price: float
    timing_detail: str
    stop_hint_pct: float | None
    weekly_regime: str
    online_hint: str
    levels_detail: str
    trade_plan: TradePlan | None
    # Quant model outputs
    mtf_momentum_detail: str = ""
    zscore_detail: str = ""
    vol_regime_detail: str = ""
    trend_detail: str = ""
    cross_asset_detail: str = ""
    position_size_detail: str = ""
    quant_score: float = 0.0
    # Аналитика Wall Street (Finnhub, только US)
    analyst_detail: str = ""
    earnings_detail: str = ""


# ── Приватные датаклассы для промежуточных результатов ───────────────────────

@dataclass
class _RawInputs:
    """Результаты всех сетевых запросов и первичных вычислений."""
    snap: TickerSnapshot
    profile: InstrumentProfile
    hist: pd.DataFrame
    close: pd.Series
    yahoo_last: float
    tech: TechnicalScore
    mom: MomentumScore
    ticker_news: list[NewsItem]
    fh_news: list[NewsItem]
    macro_news: list[NewsItem]
    combined: list[NewsItem]
    news_weights: list[float]
    sent: SentimentResult
    news_score: float
    intra: IntradayBundle | None
    has_intra: bool
    online_hint: str
    wt: float
    wm: float
    wn: float
    wi: float
    vol_res: VolumePressureResult
    macro_ctx: MacroContext
    e_mult: float
    e_note: str
    earn_bad: bool
    wk_reg: str
    wk_note: str
    atr_pct: float | None
    stop_hint: float | None
    levels: KeyLevels
    # Quant models
    mtf_mom: MtfMomentumResult | None
    zscore: ZScoreResult | None
    vol_regime: VolRegimeResult | None
    trend_str: TrendStrengthResult | None
    cross_asset: CrossAssetRegime | None
    adaptive_w: AdaptiveWeightsResult | None
    live_price: float | None  # актуальная цена (T-Bank / MOEX ISS / Finnhub)


@dataclass
class _ScoreBundle:
    """Результат всей математики весов, мультипликаторов и confidence."""
    score_pre_macro: float  # после vol_al*liq*confidence, но ДО макро
    total: float
    confidence: float
    vol_al: float
    liq: float
    online_note: str
    timing_detail: str
    signal_tier: str
    tier_rationale: str
    news_detail: str


# ── Вспомогательные функции ──────────────────────────────────────────────────

def _news_item_weight(it: NewsItem, kind: str, now: float) -> float:
    if kind == "finnhub":
        w = 1.22
    elif kind == "polygon":
        w = 1.15
    elif kind == "ticker":
        w = 1.0
    else:
        w = 0.32
    if it.published_ts:
        age_d = max(0.0, (now - float(it.published_ts)) / 86400.0)
        w *= float(0.25 + 0.75 * np.exp(-age_d / 2.0))
    else:
        w *= 0.88
    return float(max(0.05, w))


def _merge_news_with_weights(
    fh_news: list[NewsItem],
    ticker_news: list[NewsItem],
    macro: list[NewsItem],
    now: float,
    polygon_news: list[NewsItem] | None = None,
) -> tuple[list[NewsItem], list[float]]:
    seen: set[str] = set()
    combined: list[NewsItem] = []
    weights: list[float] = []
    blocks: list[tuple[list[NewsItem], str]] = [
        (fh_news, "finnhub"),
        (ticker_news, "ticker"),
        (macro, "macro"),
    ]
    if polygon_news:
        blocks.insert(1, (polygon_news, "polygon"))
    for block, kind in blocks:
        for it in block:
            k = it.title.strip().lower()[:200]
            if k in seen:
                continue
            seen.add(k)
            combined.append(it)
            weights.append(_news_item_weight(it, kind, now))
    return combined, weights


def _component_confidence(components: list[float]) -> float:
    if len(components) < 2:
        return 0.75
    arr = np.array(components, dtype=float)
    spread = float(np.max(arr) - np.min(arr))
    return float(np.clip(1.0 - spread / 2.2, 0.22, 1.0))


def _volume_alignment(raw_total: float, vol_score: float) -> float:
    if abs(vol_score) < 0.15 or abs(raw_total) < 0.08:
        return 1.0
    if raw_total == 0.0 or vol_score == 0.0:
        return 1.0
    if np.sign(vol_score) != np.sign(raw_total):
        return _VOL_ALIGN_PENALTY
    return 1.0


def _liquidity_mult(vol_series: pd.Series) -> float:
    if len(vol_series) < 20:
        return 1.0
    ratio = float(vol_series.iloc[-1] / (vol_series.tail(20).mean() + 1e-9))
    if ratio < 0.28:
        return _LIQUIDITY_LOW_MULT
    return 1.0


def _normalize_weights(
    wt: float, wm: float, wn: float, wi: float
) -> tuple[float, float, float, float]:
    s = wt + wm + wn + wi
    if s <= 0:
        return 0.25, 0.25, 0.25, 0.25
    return wt / s, wm / s, wn / s, wi / s


def _online_hint(symbol: str, has_intra: bool, finnhub_configured: bool) -> str:
    """Пояснение, если блок «онлайн» пустой."""
    if has_intra:
        return ""
    sym = symbol.strip().upper()
    if sym.endswith(".ME"):
        return (
            "Онлайн: нет данных MOEX ISS (сеть/тикер/сессия). "
            "Опционально: pip install -r requirements-tbank.txt (или tinkoff-investments) и TINKOFF_INVEST_TOKEN — см. docs/TINKOFF.md."
        )
    if not finnhub_configured:
        return "Онлайн: задайте FINNHUB_API_KEY — иначе для US нет потока котировок в этом блоке."
    return "Онлайн: Finnhub не вернул котировку (ключ, лимит или тикер)."


def _verdict_from_score(x: float, confidence: float) -> str:
    thr = _VERDICT_BASE_THR + _VERDICT_CONF_SCALE * (1.0 - float(np.clip(confidence, 0.0, 1.0)))
    if x >= thr:
        return "СИГНАЛ: ожидается давление вверх (ограниченная уверенность)"
    if x <= -thr:
        return "СИГНАЛ: ожидается давление вниз (ограниченная уверенность)"
    return "СИГНАЛ: нейтрально / неопределённость"


# ── Приватные функции-части build_report ────────────────────────────────────

def _fetch_news_parallel(symbol: str, company_name: str, key: str | None) -> tuple[list[NewsItem], list[NewsItem], list[NewsItem], list[NewsItem]]:
    """Параллельная загрузка новостей из разных источников."""
    ticker_news: list[NewsItem] = []
    fh_news: list[NewsItem] = []
    macro_news: list[NewsItem] = []
    polygon_news: list[NewsItem] = []

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(fetch_ticker_news_google, symbol, company_name): 'ticker',
            executor.submit(fetch_macro_headlines): 'macro',
        }

        if key:
            # Finnhub не поддерживает .ME тикеры — не тратим запросы
            if not symbol.strip().upper().endswith(".ME"):
                futures[executor.submit(fetch_company_news, symbol, api_key=key, limit=30)] = 'finnhub'

        # Polygon news (US тикеры, если ключ настроен)
        if not symbol.strip().upper().endswith(".ME"):
            try:
                from .polygon_data import polygon_available, fetch_ticker_news as polygon_fetch_news
                if polygon_available():
                    futures[executor.submit(polygon_fetch_news, symbol, limit=20)] = 'polygon'
            except ImportError:
                pass

        for future in as_completed(futures, timeout=15):
            source = futures[future]
            try:
                result = future.result(timeout=5)
                if source == 'ticker':
                    ticker_news = result
                elif source == 'finnhub':
                    fh_news = result
                elif source == 'macro':
                    macro_news = result
                elif source == 'polygon':
                    polygon_news = result
            except Exception as e:
                # Логируем, но не падаем - продолжаем с пустым списком
                pass

    return ticker_news, fh_news, macro_news, polygon_news


def _gather_inputs(
    symbol: str,
    key: str | None,
    use_finnhub_ws: bool,
    ws_seconds: float,
    volume_tape_ws: bool,
    fast_mode: bool = False,
) -> _RawInputs:
    """Все сетевые запросы и первичная обработка данных."""
    snap, _info, profile = fetch_snapshot_with_meta(symbol)
    hist = snap.history
    close = hist["Close"]
    yahoo_last = float(close.iloc[-1])
    tech = analyze_technical(hist)
    atr_pct = atr_percent_14(hist)
    mom = analyze_momentum(close, atr_pct=atr_pct, adx14=tech.adx14)

    # В быстром режиме пропускаем новости
    if fast_mode:
        ticker_news: list[NewsItem] = []
        fh_news: list[NewsItem] = []
        macro_news: list[NewsItem] = []
        polygon_news: list[NewsItem] = []
    else:
        # Параллельная загрузка новостей
        ticker_news, fh_news, macro_news, polygon_news = _fetch_news_parallel(snap.symbol, snap.company_name, key)

    now_ts = time.time()
    combined, news_weights = _merge_news_with_weights(fh_news, ticker_news, macro_news, now_ts, polygon_news)
    combined = combined[:60]
    news_weights = news_weights[: len(combined)]
    sent = score_headlines(combined, news_weights)
    news_score = float(np.clip(sent.compound, -1.0, 1.0))

    # LLM sentiment blending (если Ollama доступен)
    _llm_detail = ""
    if not fast_mode and combined:
        try:
            from .llm_sentiment import analyze_headlines_llm, blend_sentiment_scores
            llm_res = analyze_headlines_llm(combined)
            if llm_res is not None:
                news_score, _llm_detail = blend_sentiment_scores(news_score, llm_res)
        except Exception as exc:
            _log.debug("LLM sentiment failed: %s", exc)

    # В быстром режиме пропускаем intraday данные
    if fast_mode:
        intra = None
    else:
        intra = build_intraday(
            snap.symbol,
            finnhub_api_key=key,
            use_finnhub_ws=use_finnhub_ws,
            ws_seconds=ws_seconds,
            yahoo_last_daily_close=yahoo_last,
        )
    has_intra = intra is not None
    online_hint = _online_hint(snap.symbol, has_intra, bool(key))
    wt, wm, wn, wi = select_component_weights(profile, has_intra)

    # Режим по ADX: в боковике меньше вес импульса, чуть больше техника.
    if tech.adx14 < 20.0:
        wm *= _ADX_LOW_MOM_SCALE
        wt *= _ADX_LOW_TECH_SCALE
    elif tech.adx14 > 28.0:
        wm *= _ADX_HIGH_MOM_SCALE
        wt *= _ADX_HIGH_TECH_SCALE
    wt, wm, wn, wi = _normalize_weights(wt, wm, wn, wi)

    vol_res = analyze_volume_pressure(
        hist,
        snap.symbol,
        finnhub_api_key=key,
        use_tape_ws=volume_tape_ws,
        ws_seconds=ws_seconds,
    )

    macro_ctx = build_macro_context(api_key=key)
    e_mult, e_note, earn_bad, wk_reg, wk_note = build_timing_context(snap.symbol)

    stop_hint = stop_hint_atr_multiple(atr_pct, mult=1.5)
    levels = compute_key_levels(hist)

    # ── Quant models ──
    try:
        mtf_mom = analyze_mtf_momentum(close)
    except Exception as exc:
        _log.debug("MTF momentum failed: %s", exc)
        mtf_mom = None
    try:
        zscore_res = analyze_zscore(close)
    except Exception as exc:
        _log.debug("Z-score failed: %s", exc)
        zscore_res = None
    try:
        vol_regime_res = analyze_vol_regime(close)
    except Exception as exc:
        _log.debug("Vol regime failed: %s", exc)
        vol_regime_res = None

    trend_str_res: TrendStrengthResult | None = None
    if all(c in hist.columns for c in ("High", "Low")):
        try:
            trend_str_res = analyze_trend_strength(close, hist["High"], hist["Low"])
        except Exception as exc:
            _log.debug("Trend strength failed: %s", exc)
            trend_str_res = None

    try:
        cross_asset_res = build_cross_asset_regime()
    except Exception as exc:
        _log.debug("Cross-asset regime failed: %s", exc)
        cross_asset_res = None

    try:
        adaptive_w = compute_adaptive_weights()
    except Exception as exc:
        _log.debug("Adaptive weights failed: %s", exc)
        adaptive_w = None

    # ── Адаптивная корректировка весов по IC ──
    # Если накоплено достаточно данных (≥30 сигналов с исходами),
    # adaptive_weights сдвигает веса в сторону компонентов с высоким IC.
    # Сдвиг мягкий (blend 30% adaptive + 70% base), чтобы не переоптимизировать.
    if adaptive_w is not None and adaptive_w.adapted:
        _AW_BLEND = 0.30  # доля адаптивных весов
        aw = adaptive_w.weights
        wt = wt * (1.0 - _AW_BLEND) + aw.get("technical", wt) * _AW_BLEND
        wm = wm * (1.0 - _AW_BLEND) + aw.get("momentum", wm) * _AW_BLEND
        wn = wn * (1.0 - _AW_BLEND) + aw.get("news", wn) * _AW_BLEND
        # intraday вес не адаптируем (нет IC для него)
        wt, wm, wn, wi = _normalize_weights(wt, wm, wn, wi)

    # ── LLM Learning: корректировки весов из обучения на outcomes ──
    # Совмещает числовой IC + LLM-анализ паттернов (если Ollama доступен).
    # Множители мягкие (0.75–1.25), не ломают базовую логику.
    try:
        from .llm_learning import get_weight_adjustments
        _learning_adj = get_weight_adjustments()
        if _learning_adj:
            wt *= _learning_adj.get("technical", 1.0)
            wm *= _learning_adj.get("momentum", 1.0)
            wn *= _learning_adj.get("news", 1.0)
            # volume не в основных весах, но можно скорректировать news/tech
            wt, wm, wn, wi = _normalize_weights(wt, wm, wn, wi)
    except Exception:
        pass

    # ── Актуальная цена (real-time) ──
    # Приоритет: T-Bank → MOEX ISS marketdata → Finnhub → last_close из истории
    live_price: float | None = None
    sym_u = snap.symbol.strip().upper()
    if sym_u.endswith(".ME"):
        # T-Bank real-time
        try:
            from .tbank_invest import fetch_last_price_tbank
            tq = fetch_last_price_tbank(sym_u)
            if tq and tq.last_price > 0:
                live_price = tq.last_price
        except Exception:
            pass
        # MOEX ISS fallback
        if live_price is None:
            try:
                from .moex_iss import fetch_tqbr_quote
                mq = fetch_tqbr_quote(sym_u)
                if mq.last is not None and mq.last > 0:
                    live_price = mq.last
            except Exception:
                pass
    else:
        # Finnhub для US
        if key:
            try:
                from .finnhub_live import fetch_quote
                fq = fetch_quote(sym_u, api_key=key)
                if fq.current is not None and fq.current > 0:
                    live_price = fq.current
            except Exception:
                pass
        # Polygon.io fallback для US
        if live_price is None:
            try:
                from .polygon_data import polygon_available, fetch_snapshot as polygon_snapshot
                if polygon_available():
                    pq = polygon_snapshot(sym_u)
                    if pq.last_price is not None and pq.last_price > 0:
                        live_price = pq.last_price
            except Exception:
                pass

    return _RawInputs(
        snap=snap,
        profile=profile,
        hist=hist,
        close=close,
        yahoo_last=yahoo_last,
        tech=tech,
        mom=mom,
        ticker_news=ticker_news,
        fh_news=fh_news,
        macro_news=macro_news,
        combined=combined,
        news_weights=news_weights,
        sent=sent,
        news_score=news_score,
        intra=intra,
        has_intra=has_intra,
        online_hint=online_hint,
        wt=wt,
        wm=wm,
        wn=wn,
        wi=wi,
        vol_res=vol_res,
        macro_ctx=macro_ctx,
        e_mult=e_mult,
        e_note=e_note,
        earn_bad=earn_bad,
        wk_reg=wk_reg,
        wk_note=wk_note,
        atr_pct=atr_pct,
        stop_hint=stop_hint,
        levels=levels,
        mtf_mom=mtf_mom,
        zscore=zscore_res,
        vol_regime=vol_regime_res,
        trend_str=trend_str_res,
        cross_asset=cross_asset_res,
        adaptive_w=adaptive_w,
        live_price=live_price,
    )


def _compute_score(inputs: _RawInputs) -> _ScoreBundle:
    """Вся математика весов, мультипликаторов и confidence."""
    tech = inputs.tech
    mom = inputs.mom
    news_score = inputs.news_score
    intra = inputs.intra
    has_intra = inputs.has_intra
    wt, wm, wn, wi = inputs.wt, inputs.wm, inputs.wn, inputs.wi
    vol_res = inputs.vol_res
    macro_ctx = inputs.macro_ctx
    hist = inputs.hist

    # ── Classic core components ──
    if has_intra:
        core = wt * tech.score + wm * mom.score + wn * news_score + wi * intra.score
    else:
        s0 = wt + wm + wn
        if s0 <= 0:
            core = 0.0
        else:
            core = (wt / s0) * tech.score + (wm / s0) * mom.score + (wn / s0) * news_score
    raw_total = float(np.clip(core, -1.0, 1.0))

    raw_total = float(
        np.clip((1.0 - VOL_BLEND) * raw_total + VOL_BLEND * vol_res.score, -1.0, 1.0)
    )

    # ── Quant model blending ──
    # MTF momentum, z-score, trend strength вносят в итоговый score
    quant_adj = 0.0
    quant_parts: list[float] = []

    if inputs.mtf_mom is not None and abs(inputs.mtf_mom.score) > 0.01:
        quant_adj += 0.12 * inputs.mtf_mom.score
        quant_parts.append(inputs.mtf_mom.score)

    if inputs.zscore is not None and abs(inputs.zscore.composite) > 0.01:
        ca_bias = inputs.cross_asset.strategy_bias if inputs.cross_asset else "neutral"
        zs_weight = 0.10 if ca_bias == "mean-reversion" else 0.05
        quant_adj += zs_weight * inputs.zscore.composite
        quant_parts.append(inputs.zscore.composite)

    if inputs.trend_str is not None and abs(inputs.trend_str.score) > 0.01:
        ca_bias = inputs.cross_asset.strategy_bias if inputs.cross_asset else "neutral"
        tr_weight = 0.10 if ca_bias == "momentum" else 0.06
        quant_adj += tr_weight * inputs.trend_str.score
        quant_parts.append(inputs.trend_str.score)

    raw_total = float(np.clip(raw_total + quant_adj, -1.0, 1.0))

    # ── Confidence (include quant components) ──
    comps: list[float] = [tech.score, mom.score, news_score, vol_res.score]
    if has_intra:
        comps.append(intra.score)
    comps.extend(quant_parts)
    confidence = _component_confidence(comps)

    # ── Cross-asset regime risk multiplier ──
    ca_risk_mult = 1.0
    if inputs.cross_asset is not None:
        ca_risk_mult = inputs.cross_asset.risk_multiplier

    vol_al = _volume_alignment(raw_total, vol_res.score)
    liq = _liquidity_mult(hist["Volume"]) if "Volume" in hist.columns else 1.0
    raw_total = float(
        np.clip(
            raw_total * vol_al * liq * (_CONF_BASE + _CONF_SCALE * confidence),
            -1.0,
            1.0,
        )
    )

    # raw_total здесь — score после vol_al*liq*confidence, но ДО макро
    score_pre_macro = raw_total

    total = float(np.clip(raw_total * macro_ctx.dampening, -1.0, 1.0))

    # Cross-asset regime (Bridgewater): в risk-off / crisis сжимаем сигнал
    total = float(np.clip(total * ca_risk_mult, -1.0, 1.0))

    total = float(np.clip(total * inputs.e_mult, -1.0, 1.0))

    # index_tailwind_mult needs the actual total at this point
    idx_mult, idx_note, idx_hw = index_tailwind_mult(inputs.snap.symbol, total)
    total = float(np.clip(total * idx_mult, -1.0, 1.0))

    wk_reg = inputs.wk_reg
    wk_note = inputs.wk_note
    weekly_ok = weekly_aligns_direction(total, wk_reg)
    if not weekly_ok:
        total = float(np.clip(total * _WEEKLY_MISALIGN_MULT, -1.0, 1.0))

    online_note = ""
    if has_intra and intra is not None:
        if abs(total) > _INTRADAY_CONFLICT_SCORE_THR and abs(intra.score) > _INTRADAY_CONFLICT_INTRA_THR:
            if np.sign(intra.score) != np.sign(total) and np.sign(intra.score) != 0:
                total = float(np.clip(total * _INTRADAY_CONFLICT_MULT, -1.0, 1.0))
                online_note = "онлайн против дневного знака — снижение"

    ca_note = ""
    if inputs.cross_asset and inputs.cross_asset.risk_regime != "neutral":
        ca_note = f"межрынок: {inputs.cross_asset.risk_regime} (×{ca_risk_mult:.2f})"

    timing_parts = [p for p in (wk_note, inputs.e_note, idx_note, ca_note, online_note) if p]
    timing_detail = " | ".join(timing_parts) if timing_parts else "—"

    has_pat = bool(tech.pattern_summary and tech.pattern_summary.strip())
    signal_tier, tier_rationale = classify_signal_tier(
        total=total,
        confidence=confidence,
        macro_dampening=macro_ctx.dampening,
        adx14=tech.adx14,
        news_score=news_score,
        liq_mult=liq,
        vol_align_mult=vol_al,
        has_chart_pattern=has_pat,
        weekly_aligned=weekly_ok,
        earnings_window=inputs.earn_bad,
        index_headwind=idx_hw,
    )

    sent = inputs.sent
    fh_news = inputs.fh_news
    fin_boost_note = ""
    if abs(sent.fin_boost_avg) > 0.02:
        fin_boost_note = f", фин.лексикон: {sent.fin_boost_avg:+.3f}"
    news_detail = (
        f"Сентимент (взвеш. по источнику и давности): {sent.label} (compound≈{sent.compound:.3f}{fin_boost_note}), "
        f"заголовков: {sent.headlines_used}"
    )
    if fh_news:
        news_detail += f" (в т.ч. Finnhub: {len(fh_news)} шт.)"

    return _ScoreBundle(
        score_pre_macro=score_pre_macro,
        total=total,
        confidence=confidence,
        vol_al=vol_al,
        liq=liq,
        online_note=online_note,
        timing_detail=timing_detail,
        signal_tier=signal_tier,
        tier_rationale=tier_rationale,
        news_detail=news_detail,
    )


# ── Публичная точка входа ────────────────────────────────────────────────────

def _build_technical_consensus(rep: SignalReport, inputs: _RawInputs) -> str:
    """
    Сводная оценка: сколько индикаторов за рост / снижение / нейтрально.
    Аналог консенсуса аналитиков, но на основе технических данных.
    """
    buy = 0
    sell = 0
    neutral = 0
    signals: list[str] = []

    # RSI
    rsi = inputs.tech.rsi14
    if rsi < 30:
        buy += 1
        signals.append("RSI перепродан")
    elif rsi > 70:
        sell += 1
        signals.append("RSI перекуплен")
    else:
        neutral += 1

    # MACD
    if inputs.tech.macd_bullish:
        buy += 1
        signals.append("MACD бычий")
    else:
        sell += 1
        signals.append("MACD медвежий")

    # Цена vs SMA50
    if inputs.tech.above_sma50:
        buy += 1
        signals.append("выше SMA50")
    else:
        sell += 1
        signals.append("ниже SMA50")

    # Импульс
    if inputs.mom.score > 0.1:
        buy += 1
    elif inputs.mom.score < -0.1:
        sell += 1
    else:
        neutral += 1

    # Объём
    if inputs.vol_res.score > 0.1:
        buy += 1
    elif inputs.vol_res.score < -0.1:
        sell += 1
    else:
        neutral += 1

    # Новости
    if inputs.news_score > 0.1:
        buy += 1
    elif inputs.news_score < -0.1:
        sell += 1
    else:
        neutral += 1

    # Интрадей
    if inputs.intra and abs(inputs.intra.score) > 0.05:
        if inputs.intra.score > 0:
            buy += 1
        else:
            sell += 1
    else:
        neutral += 1

    # Квант-модели
    if inputs.mtf_mom and abs(inputs.mtf_mom.score) > 0.05:
        if inputs.mtf_mom.score > 0:
            buy += 1
        else:
            sell += 1
    if inputs.trend_str and abs(inputs.trend_str.score) > 0.05:
        if inputs.trend_str.score > 0:
            buy += 1
        else:
            sell += 1

    total = buy + sell + neutral
    if total == 0:
        return ""

    if buy > sell * 2:
        verdict = "Покупать"
    elif buy > sell:
        verdict = "Скорее покупать"
    elif sell > buy * 2:
        verdict = "Продавать"
    elif sell > buy:
        verdict = "Скорее продавать"
    else:
        verdict = "Нейтрально"

    key_signals = ", ".join(signals[:3])
    return (
        f"Индикаторы: ▲ рост {buy} | ● нейтрально {neutral} | ▼ снижение {sell} — "
        f"{verdict} ({key_signals})"
    )


def build_report(
    symbol: str,
    finnhub_api_key: str | None = None,
    use_finnhub_ws: bool = False,
    ws_seconds: float = 8.0,
    volume_tape_ws: bool = False,
    fast_mode: bool = False,
) -> SignalReport:
    """
    Построить полный отчёт по тикеру.

    Args:
        symbol: Тикер для анализа
        finnhub_api_key: API ключ Finnhub (опционально)
        use_finnhub_ws: Использовать WebSocket для real-time данных
        ws_seconds: Длительность сбора данных через WebSocket
        volume_tape_ws: Использовать ленту сделок для анализа объёма
        fast_mode: Быстрый режим - пропустить новости и real-time данные
    """
    key = finnhub_api_key or os.environ.get("FINNHUB_API_KEY") or os.environ.get("FINNHUB_TOKEN")

    inputs = _gather_inputs(symbol, key, use_finnhub_ws, ws_seconds, volume_tape_ws, fast_mode)
    bundle = _compute_score(inputs)

    risk = (
        "Не инвестиционный совет. Модели: AQR MTF momentum, DE Shaw z-score, "
        "Bridgewater regime/vol-target, Kelly sizing, circuit breaker. "
        "Ориентир стопа ≈1.5×ATR. Проверяйте по SSA_SIGNAL_LOG."
    )

    # ── Institutional position sizing ──
    cur_vol = inputs.vol_regime.current_vol if inputs.vol_regime else 0.0
    ca_rm = inputs.cross_asset.risk_multiplier if inputs.cross_asset else 1.0
    pos_size_res = compute_position_size(
        confidence=bundle.confidence,
        signal_strength=bundle.total,
        current_vol_annual=cur_vol,
        regime_risk_mult=ca_rm,
    )

    # Quant score: contribution of new models to final score
    quant_sc = 0.0
    if inputs.mtf_mom:
        quant_sc += inputs.mtf_mom.score * 0.4
    if inputs.zscore:
        quant_sc += inputs.zscore.composite * 0.2
    if inputs.trend_str:
        quant_sc += inputs.trend_str.score * 0.4
    quant_sc = float(np.clip(quant_sc, -1.0, 1.0))

    rep = SignalReport(
        symbol=inputs.snap.symbol,
        company=inputs.snap.company_name,
        instrument_label=inputs.profile.label,
        verdict=_verdict_from_score(bundle.total, bundle.confidence),
        score=bundle.total,
        score_before_macro=bundle.score_pre_macro,
        technical_score=inputs.tech.score,
        momentum_score=inputs.mom.score,
        news_score=inputs.news_score,
        technical_detail=inputs.tech.details,
        momentum_detail=inputs.mom.details,
        news_detail=bundle.news_detail,
        intraday_score=inputs.intra.score if inputs.intra else None,
        intraday_detail=inputs.intra.detail if inputs.intra else None,
        macro_summary=inputs.macro_ctx.summary,
        macro_dampening=inputs.macro_ctx.dampening,
        volume_score=inputs.vol_res.score,
        volume_detail=inputs.vol_res.detail,
        risk_note=risk,
        confidence=bundle.confidence,
        adx14=inputs.tech.adx14,
        regime_label=inputs.tech.regime,
        pattern_summary=inputs.tech.pattern_summary,
        signal_tier=bundle.signal_tier,
        tier_rationale=bundle.tier_rationale,
        atr_pct=inputs.atr_pct,
        ref_price=float(inputs.live_price or inputs.snap.last_close),
        timing_detail=bundle.timing_detail,
        stop_hint_pct=inputs.stop_hint,
        weekly_regime=inputs.wk_reg,
        online_hint=inputs.online_hint,
        levels_detail=inputs.levels.detail,
        trade_plan=None,
        mtf_momentum_detail=inputs.mtf_mom.detail if inputs.mtf_mom else "",
        zscore_detail=inputs.zscore.detail if inputs.zscore else "",
        vol_regime_detail=inputs.vol_regime.detail if inputs.vol_regime else "",
        trend_detail=inputs.trend_str.detail if inputs.trend_str else "",
        cross_asset_detail=inputs.cross_asset.detail if inputs.cross_asset else "",
        position_size_detail=pos_size_res.detail,
        quant_score=quant_sc,
    )

    has_pat = bool(inputs.tech.pattern_summary and inputs.tech.pattern_summary.strip())
    tp = build_trade_plan(
        score=bundle.total,
        ref_price=rep.ref_price,
        atr_pct=inputs.atr_pct,
        signal_tier=bundle.signal_tier,
        adx14=inputs.tech.adx14,
        symbol=inputs.snap.symbol,
        confidence=bundle.confidence,
        has_pattern=has_pat,
        nearest_support=inputs.levels.nearest_support,
        nearest_resistance=inputs.levels.nearest_resistance,
        institutional_size_pct=pos_size_res.final_pct,
        vol_regime=inputs.vol_regime.regime if inputs.vol_regime else "normal",
    )
    rep.trade_plan = tp

    # ── Аналитика Wall Street (только US тикеры, Finnhub бесплатный) ──
    if key and not inputs.snap.symbol.endswith(".ME"):
        try:
            rec = fetch_recommendation_trends(inputs.snap.symbol, api_key=key)
            if rec:
                rep.analyst_detail = rec.detail
        except Exception:
            pass
        try:
            earn = fetch_earnings_surprise(inputs.snap.symbol, api_key=key)
            if earn:
                rep.earnings_detail = earn.detail
        except Exception:
            pass

    # ── Технический консенсус (для всех тикеров, включая РФ) ──
    rep.analyst_detail = _build_technical_consensus(rep, inputs) + (
        ("\n" + rep.analyst_detail) if rep.analyst_detail else ""
    )

    log_p = log_path_from_env()
    if log_p:
        rec = build_record_from_report(rep, rep.ref_price, inputs.snap.currency)
        rec.update(trade_plan_to_dict(tp))
        rec["quant_score"] = quant_sc
        rec["mtf_mom_score"] = inputs.mtf_mom.score if inputs.mtf_mom else 0.0
        rec["zscore_composite"] = inputs.zscore.composite if inputs.zscore else 0.0
        rec["trend_score"] = inputs.trend_str.score if inputs.trend_str else 0.0
        rec["vol_regime"] = inputs.vol_regime.regime if inputs.vol_regime else "unknown"
        rec["vol_regime_scalar"] = inputs.vol_regime.risk_scalar if inputs.vol_regime else 1.0
        rec["cross_asset_regime"] = inputs.cross_asset.risk_regime if inputs.cross_asset else "unknown"
        rec["cross_asset_mult"] = ca_rm
        rec["position_size_final"] = pos_size_res.final_pct
        append_signal_record(log_p, rec)
    return rep
