"""Краткосрочный балл из онлайн-котировок (MOEX ISS / Finnhub REST / WS)."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .finnhub_live import (
    FinnhubQuote,
    fetch_quote,
    microstructure_score_from_ws,
)
from .moex_iss import MoexQuote, fetch_tqbr_quote
from .tbank_invest import fetch_quote_and_volume_context
from .tbank_invest import volume_alignment_adjustment
from .tinkoff_quotes import tinkoff_available


def _score_from_pct(pct: float | None) -> float:
    if pct is None:
        return 0.0
    return max(-1.0, min(1.0, math.tanh(float(pct) / 4.0)))


@dataclass
class IntradayBundle:
    score: float
    detail: str
    source: str


@dataclass
class _IntradayCtx:
    finnhub_api_key: str | None
    use_finnhub_ws: bool
    ws_seconds: float
    yahoo_last_daily_close: float | None


class _IntradayProvider:
    """Базовый класс провайдера внутридневных данных."""

    def can_handle(self, symbol: str, ctx: _IntradayCtx) -> bool:  # noqa: ARG002
        return False

    def fetch(self, symbol: str, ctx: _IntradayCtx) -> IntradayBundle | None:  # noqa: ARG002
        return None


class _MoexProvider(_IntradayProvider):
    """Провайдер для .ME тикеров через MOEX ISS."""

    def can_handle(self, symbol: str, ctx: _IntradayCtx) -> bool:
        return symbol.strip().upper().endswith(".ME")

    def fetch(self, symbol: str, ctx: _IntradayCtx) -> IntradayBundle | None:
        yahoo_last_daily_close = ctx.yahoo_last_daily_close
        sym_u = symbol.strip().upper()
        try:
            mq = fetch_tqbr_quote(sym_u)
        except Exception:
            return None

        pct: float | None = None
        ch = mq.change_pct_from_prev
        if mq.last is not None and ch is not None:
            if abs(ch) <= 25.0:
                pct = float(ch)
            else:
                prev = float(mq.last) - float(ch)
                if prev != 0:
                    pct = (float(mq.last) / prev - 1.0) * 100.0
        if pct is None and mq.last is not None and yahoo_last_daily_close:
            try:
                pct = (mq.last / float(yahoo_last_daily_close) - 1.0) * 100.0
            except (TypeError, ValueError, ZeroDivisionError):
                pct = None

        sc = _score_from_pct(pct)
        detail = f"{mq.detail} | intraday_score={sc:+.3f}"
        return IntradayBundle(score=sc, detail=detail, source="MOEX ISS (опрос)")


class _TinkoffProvider(_IntradayProvider):
    """Провайдер для .ME тикеров через Tinkoff/TBank API (одна gRPC-сессия)."""

    def can_handle(self, symbol: str, ctx: _IntradayCtx) -> bool:
        return symbol.strip().upper().endswith(".ME") and tinkoff_available()

    def fetch(self, symbol: str, ctx: _IntradayCtx) -> IntradayBundle | None:
        yahoo_last_daily_close = ctx.yahoo_last_daily_close
        sym_u = symbol.strip().upper()

        if not yahoo_last_daily_close:
            return None
        try:
            tq, vol_ctx = fetch_quote_and_volume_context(
                sym_u, yahoo_last_daily_close=yahoo_last_daily_close,
            )
            if tq is None or tq.last_price <= 0:
                return None
            pct_t = (tq.last_price / float(yahoo_last_daily_close) - 1.0) * 100.0
            sc = _score_from_pct(pct_t)
            tink_line = f"{tq.detail} | score={sc:+.3f}"

            if vol_ctx is not None and vol_ctx.last_vs_vwap_pct is not None:
                adj = volume_alignment_adjustment(pct_t, vol_ctx.last_vs_vwap_pct)
                sc = max(-1.0, min(1.0, sc + adj))
                tink_line = f"{tink_line} | {vol_ctx.detail} | adj={adj:+.3f}"

            detail = f"{tink_line} | intraday_score={sc:+.3f}"
            return IntradayBundle(score=sc, detail=detail, source="Т-Инвестиции API")
        except Exception:
            return None


class _FinnhubProvider(_IntradayProvider):
    """Провайдер для US тикеров через Finnhub REST/WS."""

    def can_handle(self, symbol: str, ctx: _IntradayCtx) -> bool:
        sym_u = symbol.strip().upper()
        return not sym_u.endswith(".ME") and bool(ctx.finnhub_api_key)

    def fetch(self, symbol: str, ctx: _IntradayCtx) -> IntradayBundle | None:
        finnhub_api_key = ctx.finnhub_api_key
        use_finnhub_ws = ctx.use_finnhub_ws
        ws_seconds = ctx.ws_seconds
        sym_u = symbol.strip().upper()

        if not finnhub_api_key:
            return None
        try:
            fq = fetch_quote(sym_u, api_key=finnhub_api_key)
        except Exception:
            return None

        sc_rest = _score_from_pct(fq.change_pct)
        parts = [f"Finnhub REST: {fq.detail}, score={sc_rest:+.3f}"]
        sc_ws: float | None = None
        if use_finnhub_ws:
            w, d = microstructure_score_from_ws(sym_u, api_key=finnhub_api_key, duration_sec=ws_seconds)
            sc_ws = w
            parts.append(d)

        if sc_ws is not None:
            sc = max(-1.0, min(1.0, 0.55 * sc_rest + 0.45 * sc_ws))
        else:
            sc = sc_rest

        return IntradayBundle(score=sc, detail=" | ".join(parts), source="Finnhub REST/WS")


class _PolygonProvider(_IntradayProvider):
    """Провайдер для US тикеров через Polygon.io (prev close / snapshot)."""

    def can_handle(self, symbol: str, ctx: _IntradayCtx) -> bool:
        sym_u = symbol.strip().upper()
        if sym_u.endswith(".ME"):
            return False
        try:
            from .polygon_data import polygon_available
            return polygon_available()
        except ImportError:
            return False

    def fetch(self, symbol: str, ctx: _IntradayCtx) -> IntradayBundle | None:
        sym_u = symbol.strip().upper()
        try:
            from .polygon_data import fetch_snapshot
            pq = fetch_snapshot(sym_u)
            if pq.last_price is None:
                return None
            sc = _score_from_pct(pq.change_pct)
            detail = f"{pq.detail} | score={sc:+.3f}"
            return IntradayBundle(score=sc, detail=detail, source="Polygon.io")
        except Exception:
            return None


# Упорядоченный список провайдеров
_PROVIDERS: list[_IntradayProvider] = [
    _MoexProvider(),
    _TinkoffProvider(),
    _FinnhubProvider(),
    _PolygonProvider(),
]


def _blend_results(results: list[IntradayBundle]) -> IntradayBundle:
    """Смешивает результаты нескольких провайдеров с равными весами."""
    if len(results) == 1:
        r = results[0]
        return IntradayBundle(score=r.score, detail=r.detail, source=r.source)

    sc = max(-1.0, min(1.0, sum(r.score for r in results) / len(results)))
    sources = " + ".join(r.source for r in results)
    # Собираем детали, убирая дублирующийся intraday_score из составных частей
    detail_parts: list[str] = []
    for r in results:
        # Убираем финальный «intraday_score=…» из вложенных деталей при бленде
        part = r.detail.rsplit(" | intraday_score=", 1)[0]
        detail_parts.append(part)
    detail = " | ".join(detail_parts) + f" | blend={sc:+.3f}"
    return IntradayBundle(score=sc, detail=detail, source=sources)


def build_intraday(
    symbol: str,
    finnhub_api_key: str | None,
    use_finnhub_ws: bool,
    ws_seconds: float,
    yahoo_last_daily_close: float | None,
) -> IntradayBundle | None:
    """
    Российские тикеры Yahoo обычно с суффиксом .ME — тянем MOEX ISS.
    Иначе при наличии ключа — Finnhub REST (+ опционально WebSocket по сделкам US).

    Обходит список провайдеров, собирает результаты от всех подходящих,
    при нескольких результатах — смешивает (blend).
    """
    ctx = _IntradayCtx(
        finnhub_api_key=finnhub_api_key,
        use_finnhub_ws=use_finnhub_ws,
        ws_seconds=ws_seconds,
        yahoo_last_daily_close=yahoo_last_daily_close,
    )

    results: list[IntradayBundle] = []
    for provider in _PROVIDERS:
        if provider.can_handle(symbol, ctx):
            result = provider.fetch(symbol, ctx)
            if result is not None:
                results.append(result)

    if not results:
        return None

    return _blend_results(results)
