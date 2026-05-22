"""Сканирование сильных сигналов по рынку вне списка пользователя."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from typing import Any

from .engine import SignalReport, build_report
from .market_segments import full_scan_universe
from .user_store import normalize_symbol

log = logging.getLogger(__name__)

_CACHE_PATH = os.path.join(os.environ.get("STOCK_SIGNAL_DATA", "/data"), "last_scan_signals.json")
_OFFSET_PATH = os.path.join(os.environ.get("STOCK_SIGNAL_DATA", "/data"), "scan_offset.json")
_CACHE_LOCK = threading.Lock()


def outside_scan_limit() -> int:
    """
    Сколько тикеров из пула full_scan_universe() реально анализировать за один проход.

    По умолчанию — весь пул (~120). Уменьшите через OUTSIDE_SCAN_MAX (например 20),
    если нужна быстрая проверка или экономия API/времени.
    """
    raw = os.environ.get("OUTSIDE_SCAN_MAX", "120")
    try:
        n = int(raw.strip())
    except ValueError:
        n = 120
    return max(1, min(n, 500))


@dataclass
class CachedSignal:
    """Минимальное представление сигнала для кэша/уведомлений."""
    symbol: str
    score: float
    signal_tier: str
    direction: str
    verdict: str


def _ru_us_pools(watchlist: set[str]) -> tuple[list[str], list[str]]:
    """Разделить universe на RU (.ME) и US (остальные), исключая watchlist."""
    all_tickers = [s for s in full_scan_universe() if normalize_symbol(s) not in watchlist]
    ru = [s for s in all_tickers if s.upper().endswith(".ME")]
    us = [s for s in all_tickers if not s.upper().endswith(".ME")]
    return ru, us


def _load_offset() -> int:
    """Загрузить текущий offset для US тикеров (round-robin)."""
    try:
        with open(_OFFSET_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("us_offset", 0)
    except Exception:
        return 0


def _save_offset(offset: int) -> None:
    """Сохранить offset для US тикеров."""
    os.makedirs(os.path.dirname(_OFFSET_PATH), exist_ok=True)
    with open(_OFFSET_PATH, "w", encoding="utf-8") as f:
        json.dump({"us_offset": offset}, f)


def _get_us_slice(us_tickers: list[str], chunk_size: int = 10) -> tuple[list[str], int]:
    """Вернуть slice US тикеров и новый offset."""
    offset = _load_offset()
    if not us_tickers:
        return [], 0
    total = len(us_tickers)
    start = offset % total
    end = min(start + chunk_size, total)
    chunk = us_tickers[start:end]
    # Если дошли до конца — wrap around и берём остаток
    if len(chunk) < chunk_size and total > chunk_size:
        chunk += us_tickers[: chunk_size - len(chunk)]
    new_offset = (offset + chunk_size) % total
    return chunk, new_offset


def _scan_region(tickers: list[str], max_workers: int = 3) -> list[CachedSignal]:
    """Сканировать один регион, вернуть список CachedSignal."""
    results: list[CachedSignal] = []

    def _one(sym: str) -> CachedSignal | None:
        try:
            r = build_report(sym, fast_mode=True)
            return CachedSignal(
                symbol=sym,
                score=float(r.score) if r.score == r.score else 0.0,
                signal_tier=str(getattr(r, "signal_tier", "?")),
                direction=str(getattr(r.trade_plan, "direction", "neutral")) if r.trade_plan else "neutral",
                verdict=str(getattr(r, "verdict", "")),
            )
        except Exception as e:
            log.warning("scan_region: ошибка по %s: %s", sym, e)
            return None

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = [ex.submit(_one, s) for s in tickers]
        for fut in as_completed(futs):
            cs = fut.result()
            if cs is not None:
                results.append(cs)

    results.sort(key=lambda x: abs(x.score), reverse=True)
    return results


def scan_all_regions(watchlist: list[str]) -> dict[str, Any]:
    """
    Сканировать все RU тикеры + slice US тикеров (round-robin),
    сохранить результат в JSON-кэш.
    Возвращает dict с ru/us списками.
    """
    wl = {normalize_symbol(x) for x in watchlist}
    ru_tickers, us_tickers = _ru_us_pools(wl)
    us_slice, new_offset = _get_us_slice(us_tickers, chunk_size=10)
    log.info("scan_all_regions: RU=%d US(slice=%d/%d) offset=%d", len(ru_tickers), len(us_slice), len(us_tickers), new_offset)

    ru_results: list[CachedSignal] = []
    us_results: list[CachedSignal] = []

    with ThreadPoolExecutor(max_workers=2) as ex:
        ru_future = ex.submit(_scan_region, ru_tickers, 3)
        us_future = ex.submit(_scan_region, us_slice, 2)
        ru_results = ru_future.result()
        us_results = us_future.result()

    # Merge with existing cache (append new US, replace RU)
    existing_ru: list[dict] = []
    existing_us: list[dict] = []
    if os.path.exists(_CACHE_PATH):
        try:
            with _CACHE_LOCK:
                with open(_CACHE_PATH, "r", encoding="utf-8") as f:
                    old = json.load(f)
                existing_ru = old.get("ru", [])
                existing_us = old.get("us", [])
        except Exception:
            pass

    # Remove old US tickers that are in current slice (update)
    us_symbols_now = {c.symbol for c in us_results}
    existing_us = [x for x in existing_us if x.get("symbol") not in us_symbols_now]
    existing_us += [asdict(c) for c in us_results]

    # Replace RU completely
    payload = {
        "timestamp": time.time(),
        "ru": [asdict(c) for c in ru_results],
        "us": existing_us,
    }

    os.makedirs(os.path.dirname(_CACHE_PATH), exist_ok=True)
    with _CACHE_LOCK:
        with open(_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    _save_offset(new_offset)
    log.info("scan_all_regions: saved %d RU + %d US signals to %s", len(ru_results), len(existing_us), _CACHE_PATH)
    return payload


def get_cached_signals(watchlist: list[str], *, min_score: float = 0.0) -> list[tuple[str, CachedSignal]]:
    """
    Прочитать кэш последнего сканирования, исключить watchlist,
    вернуть отсортированные сигналы (symbol, CachedSignal).
    """
    if not os.path.exists(_CACHE_PATH):
        log.warning("get_cached_signals: кэш не найден (%s)", _CACHE_PATH)
        return []

    wl = {normalize_symbol(x) for x in watchlist}

    with _CACHE_LOCK:
        try:
            with open(_CACHE_PATH, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception:
            log.warning("get_cached_signals: не удалось прочитать кэш", exc_info=True)
            return []

    age_sec = time.time() - payload.get("timestamp", 0)
    if age_sec > 3600 * 4:
        log.warning("get_cached_signals: кэш устарел (%.0f мин)", age_sec / 60)

    raw = payload.get("ru", []) + payload.get("us", [])
    signals: list[tuple[str, CachedSignal]] = []
    for item in raw:
        sym = item.get("symbol", "")
        if normalize_symbol(sym) in wl:
            continue
        cs = CachedSignal(
            symbol=sym,
            score=item.get("score", 0.0),
            signal_tier=item.get("signal_tier", "?"),
            direction=item.get("direction", "neutral"),
            verdict=item.get("verdict", ""),
        )
        if abs(cs.score) >= min_score:
            signals.append((sym, cs))

    signals.sort(key=lambda x: abs(x[1].score), reverse=True)
    log.info("get_cached_signals: %d signals (cache age %.0f min)", len(signals), age_sec / 60)
    return signals


def scan_strong_outside_watchlist(
    watchlist: list[str],
    threshold: float,
    max_symbols: int | None = None,
    max_workers: int = 3,
) -> list[tuple[str, SignalReport]]:
    """
    Берёт ограниченный пул ликвидных бумаг (РФ/иностранные/дивидендные), исключает watchlist,
    строит полный отчёт; возвращает пары (тикер, отчёт), где |score| >= threshold.

    Раньше по умолчанию обрабатывались только первые 14 тикеров — из-за этого «рынок вне списка»
    почти не покрывался. Сейчас по умолчанию берётся весь пул (см. outside_scan_limit).

    Чтобы вывести прогноз по всем просканированным бумагам без отсечения по силе сигнала,
    передайте threshold=0.0.
    """
    limit = outside_scan_limit() if max_symbols is None else max(1, max_symbols)
    wl = {normalize_symbol(x) for x in watchlist}
    pool = [s for s in full_scan_universe() if normalize_symbol(s) not in wl][:limit]

    strong: list[tuple[str, SignalReport]] = []

    def _one(sym: str) -> tuple[str, SignalReport | None]:
        try:
            r = build_report(sym, fast_mode=True)
            return sym, r
        except Exception as e:
            log.warning("scan: ошибка по %s: %s", sym, e)
            return sym, None

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = [ex.submit(_one, s) for s in pool]
        for fut in as_completed(futs):
            sym, rep = fut.result()
            if rep is None:
                continue
            if abs(rep.score) >= threshold:
                strong.append((sym, rep))

    strong.sort(key=lambda x: abs(x[1].score), reverse=True)
    return strong
