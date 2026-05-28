"""
Screener logic — вынесено из api/main.py для переиспользования в API и Telegram.

Использование:
    from stock_signal_analyzer.screener import run_screen
    result = run_screen(market="us", min_score=-1.0, max_results=10, fast_mode=True)
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError

from .engine import SignalReport, build_report
from .trade_plan import trade_plan_to_dict
from .universe import RU_BLUE_CHIPS, US_BLUE_CHIPS

log = logging.getLogger("screener")

_SCREEN_UNIVERSES: dict[str, list[str]] = {
    "us": sorted(US_BLUE_CHIPS),
    "ru": sorted([f"{s}.ME" for s in RU_BLUE_CHIPS]),
    "all": sorted(US_BLUE_CHIPS) + sorted([f"{s}.ME" for s in RU_BLUE_CHIPS]),
}

_screen_cache: dict[str, tuple[float, dict]] = {}


def _get_screen_cache_key(market: str, min_score: float, max_results: int, fast_mode: bool) -> str:
    return f"screen:{market}:{min_score}:{max_results}:{fast_mode}"


def _run_screen_single(symbol: str, fast_mode: bool) -> SignalReport | None:
    """Analyze one ticker for the screener."""
    try:
        return build_report(symbol, fast_mode=fast_mode)
    except Exception:
        log.debug("Screen skip %s", symbol, exc_info=True)
        return None


def _report_to_screen_item(report: SignalReport) -> dict:
    """Convert SignalReport to plain dict for API/Telegram."""
    tp = report.trade_plan
    tp_dict = None
    if tp and tp.direction != "none":
        tp_dict = trade_plan_to_dict(tp)
    return {
        "symbol": report.symbol,
        "company": report.company,
        "score": round(report.score, 4),
        "signal_tier": report.signal_tier,
        "direction": tp.direction if tp else "none",
        "confidence": round(report.confidence, 3),
        "verdict": report.verdict,
        "technical_score": round(report.technical_score, 4),
        "momentum_score": round(report.momentum_score, 4),
        "news_score": round(report.news_score, 4),
        "volume_score": round(report.volume_score, 4),
        "trade_plan": tp_dict,
        "adx14": round(report.adx14, 1),
        "atr_pct": round(report.atr_pct, 3) if report.atr_pct else None,
        "ml_score": round(report.ml_score, 4) if report.ml_score is not None else None,
    }


def run_screen(
    market: str = "all",
    min_score: float = -1.0,
    max_results: int = 20,
    fast_mode: bool = True,
    cache_ttl: int = 300,
    max_workers: int = 8,
    total_timeout: int = 120,
    per_future_timeout: int = 25,
) -> dict:
    """Проскринить вселенную тикеров и вернуть отранжированные сигналы.

    Returns plain dict compatible with ScreenResponse:
        {"market": str, "screened_at": str, "count": int, "results": list[dict]}
    """
    cache_key = _get_screen_cache_key(market, min_score, max_results, fast_mode)
    now = time.time()
    cached = _screen_cache.get(cache_key)
    if cached and (now - cached[0]) < cache_ttl:
        return cached[1]

    universe = _SCREEN_UNIVERSES.get(market, _SCREEN_UNIVERSES["all"])
    results: list[dict] = []

    workers = min(max_workers, len(universe)) if universe else 1

    executor = ThreadPoolExecutor(max_workers=workers)
    futures = {
        executor.submit(_run_screen_single, sym, fast_mode): sym
        for sym in universe
    }
    try:
        for future in as_completed(futures, timeout=total_timeout):
            report = future.result(timeout=per_future_timeout)
            if report is None or report.score < min_score:
                continue
            results.append(_report_to_screen_item(report))
    except TimeoutError:
        unfinished = sum(1 for f in futures if not f.done())
        log.warning("Screen total timeout reached, %d unfinished", unfinished)
        for f in futures:
            f.cancel()
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    results.sort(key=lambda x: x["score"], reverse=True)
    results = results[:max_results]

    response = {
        "market": market,
        "screened_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "count": len(results),
        "results": results,
    }
    _screen_cache[cache_key] = (now, response)
    return response
