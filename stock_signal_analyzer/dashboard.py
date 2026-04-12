"""Сводный отчёт по нескольким тикерам с разбивкой по сегментам."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from .engine import SignalReport, build_report
from .market_segments import primary_bucket, SECTION_TITLES
from .user_store import normalize_symbol


@dataclass
class DashboardBundle:
    sections: dict[str, list[SignalReport]]  # ru_blue, foreign_blue, dividend, other
    symbols_order: list[str]
    errors: list[str] = field(default_factory=list)


def _unique_symbols(symbols: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for s in symbols:
        n = normalize_symbol(s)
        if not n or n in seen:
            continue
        seen.add(n)
        out.append(n)
    return out


def build_dashboard(
    symbols: list[str],
    volume_tape_ws: bool = False,
    use_finnhub_ws: bool = False,
    ws_seconds: float = 8.0,
    max_workers: int = 4,
) -> DashboardBundle:
    """
    Полный отчёт по каждому тикеру (как в engine), группировка по сегментам.
    """
    syms = _unique_symbols(symbols)
    errs: list[str] = []
    reports: dict[str, SignalReport] = {}

    def _one(sym: str) -> tuple[str, SignalReport | None, str | None]:
        try:
            r = build_report(
                sym,
                volume_tape_ws=volume_tape_ws,
                use_finnhub_ws=use_finnhub_ws,
                ws_seconds=ws_seconds,
            )
            return sym, r, None
        except Exception as e:
            return sym, None, f"{sym}: {e}"

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = [
            ex.submit(_one, s)
            for s in syms
        ]
        for fut in as_completed(futs):
            sym, rep, err = fut.result()
            if err:
                errs.append(err)
            elif rep is not None:
                reports[sym] = rep

    sections: dict[str, list[SignalReport]] = {k: [] for k in ("ru_blue", "foreign_blue", "dividend", "other")}
    for sym in syms:
        rep = reports.get(sym)
        if rep is None:
            continue
        bucket = primary_bucket(sym)
        sections[bucket].append(rep)

    return DashboardBundle(
        sections=sections,
        symbols_order=syms,
        errors=errs,
    )


def section_order() -> list[str]:
    return ["ru_blue", "foreign_blue", "dividend", "other"]


def section_title(key: str) -> str:
    return SECTION_TITLES.get(key, key)
