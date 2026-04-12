"""Сканирование сильных сигналов по рынку вне списка пользователя."""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from .engine import SignalReport, build_report
from .market_segments import full_scan_universe
from .user_store import normalize_symbol

log = logging.getLogger(__name__)


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
            r = build_report(sym)
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
