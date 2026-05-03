"""Finnhub: REST-котировки и новости; короткий WebSocket по сделкам (нужен API-ключ, бесплатный tier)."""

from __future__ import annotations

import json
import math
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import requests

from .news_feeds import NewsItem
from .retry_utils import retry_with_backoff

# Подавить логирование WebSocket (содержит токен в URL)
import logging as _logging
_logging.getLogger("websocket").setLevel(_logging.WARNING)

FINNHUB_BASE = "https://finnhub.io/api/v1"

_rate_lock = threading.Lock()
_rate_window: deque[float] = deque()
_RATE_LIMIT = int(os.environ.get("FINNHUB_RATE_LIMIT", "55"))
_RATE_PERIOD = 60.0


def _rate_wait() -> None:
    """Блокирует поток, если выбран лимит запросов Finnhub за окно (free tier: 60/мин)."""
    with _rate_lock:
        now = time.monotonic()
        while _rate_window and _rate_window[0] < now - _RATE_PERIOD:
            _rate_window.popleft()
        if len(_rate_window) >= _RATE_LIMIT:
            sleep_for = _rate_window[0] + _RATE_PERIOD - now + 0.1
            if sleep_for > 0:
                time.sleep(sleep_for)
            now = time.monotonic()
            while _rate_window and _rate_window[0] < now - _RATE_PERIOD:
                _rate_window.popleft()
        _rate_window.append(time.monotonic())


def _token() -> str | None:
    return os.environ.get("FINNHUB_API_KEY") or os.environ.get("FINNHUB_TOKEN")


@dataclass
class FinnhubQuote:
    symbol: str
    current: float | None
    prev_close: float | None
    change_pct: float | None
    detail: str


@retry_with_backoff(max_retries=3, initial_delay=1.0, retry_on=(requests.RequestException,))
def fetch_quote(symbol: str, api_key: str | None = None, timeout: float = 12.0) -> FinnhubQuote:
    key = api_key or _token()
    if not key:
        return FinnhubQuote(
            symbol=symbol,
            current=None,
            prev_close=None,
            change_pct=None,
            detail="Нет FINNHUB_API_KEY.",
        )
    sym = symbol.strip().upper()
    _rate_wait()
    r = requests.get(
        f"{FINNHUB_BASE}/quote",
        params={"symbol": sym, "token": key},
        timeout=timeout,
    )
    r.raise_for_status()
    q: dict[str, Any] = r.json()
    c = q.get("c")
    pc = q.get("pc")
    cur = float(c) if c not in (None, 0, "") else None
    prev = float(pc) if pc not in (None, "") else None
    chp = None
    if cur is not None and prev not in (None, 0):
        chp = (cur / prev - 1.0) * 100.0
    detail = (
        f"Finnhub quote: c={cur:.4f} pc={prev:.4f}"
        if cur is not None and prev is not None
        else str(q)[:120]
    )
    return FinnhubQuote(
        symbol=sym,
        current=cur,
        prev_close=prev,
        change_pct=chp,
        detail=detail,
    )


@retry_with_backoff(max_retries=3, initial_delay=1.0, retry_on=(requests.RequestException,))
def fetch_company_news(
    symbol: str,
    api_key: str | None = None,
    limit: int = 25,
    timeout: float = 12.0,
) -> list[NewsItem]:
    key = api_key or _token()
    if not key:
        return []
    sym = symbol.strip().upper()
    to_d = date.today()
    from_d = to_d - timedelta(days=7)
    _rate_wait()
    r = requests.get(
        f"{FINNHUB_BASE}/company-news",
        params={
            "symbol": sym,
            "from": from_d.isoformat(),
            "to": to_d.isoformat(),
            "token": key,
        },
        timeout=timeout,
    )
    r.raise_for_status()
    raw = r.json()
    if not isinstance(raw, list):
        return []
    out: list[NewsItem] = []
    for it in raw[:limit]:
        if not isinstance(it, dict):
            continue
        head = (it.get("headline") or it.get("summary") or "").strip()
        if not head:
            continue
        link = it.get("url") or ""
        src = it.get("source") or "Finnhub"
        pub_ts: float | None = None
        raw_dt = it.get("datetime")
        if raw_dt is not None:
            try:
                pub_ts = float(raw_dt)
                if pub_ts > 1e12:
                    pub_ts /= 1000.0
            except (TypeError, ValueError):
                pub_ts = None
        out.append(
            NewsItem(title=head[:500], link=str(link), source=str(src), published_ts=pub_ts)
        )
    return out


def microstructure_score_from_ws(
    symbol: str,
    api_key: str | None = None,
    duration_sec: float = 8.0,
    max_prices: int = 400,
) -> tuple[float, str]:
    """
    Подключается к WebSocket Finnhub, копит цены сделок ~duration_sec секунд,
    оценивает краткий наклон (tanh от разницы первой/последней половины).
    Возвращает (score -1..+1, пояснение).
    """
    key = api_key or _token()
    if not key:
        return 0.0, "WebSocket: нет ключа."
    try:
        import websocket  # type: ignore
    except ImportError:
        return 0.0, "WebSocket: установите пакет websocket-client."

    sym = symbol.strip().upper()
    prices: list[float] = []
    lock = threading.Lock()
    done = threading.Event()

    def on_message(_ws, message: str) -> None:
        try:
            msg = json.loads(message)
        except json.JSONDecodeError:
            return
        if msg.get("type") != "trade":
            return
        for row in msg.get("data") or []:
            p = row.get("p")
            if isinstance(p, (int, float)):
                with lock:
                    prices.append(float(p))
                    if len(prices) >= max_prices:
                        done.set()

    def on_open(ws) -> None:
        ws.send(json.dumps({"type": "subscribe", "symbol": sym}))

    url = f"wss://ws.finnhub.io?token={key}"
    ws_app = websocket.WebSocketApp(url, on_open=on_open, on_message=on_message)
    wst = threading.Thread(target=lambda: ws_app.run_forever(ping_interval=20, ping_timeout=10), daemon=True)
    wst.start()
    deadline = time.monotonic() + float(duration_sec)
    while time.monotonic() < deadline and not done.is_set():
        time.sleep(0.2)
    try:
        ws_app.close()
    except Exception:
        pass
    wst.join(timeout=3.0)

    with lock:
        pts = list(prices)
    if len(pts) < 6:
        return 0.0, f"WebSocket: мало сделок ({len(pts)}) за окно — нейтрально."
    mid = len(pts) // 2
    a = sum(pts[:mid]) / mid
    b = sum(pts[mid:]) / (len(pts) - mid)
    base = (a + b) / 2.0 or 1.0
    rel = (b - a) / base
    score = max(-1.0, min(1.0, math.tanh(rel * 500.0)))
    return score, f"WebSocket: сделок {len(pts)}, краткий тренд сделок rel={rel*10000:.2f} bps"


def fetch_tape_imbalance_ws(
    symbol: str,
    api_key: str | None = None,
    duration_sec: float = 8.0,
    max_trades: int = 800,
) -> tuple[float | None, str]:
    """
    Прокси «покупки vs продажи» по правилу тика (tick test) на потоке сделок Finnhub.
    Не является официальным разбиением биржи; для точного split нужен платный tape/L2.
    """
    key = api_key or _token()
    if not key:
        return None, "Лента сделок: нет ключа."
    try:
        import websocket  # type: ignore
    except ImportError:
        return None, "Лента сделок: установите websocket-client."

    sym = symbol.strip().upper()
    trades: list[dict[str, Any]] = []
    lock = threading.Lock()
    done = threading.Event()

    def on_message(_ws, message: str) -> None:
        try:
            msg = json.loads(message)
        except json.JSONDecodeError:
            return
        if msg.get("type") != "trade":
            return
        for row in msg.get("data") or []:
            p = row.get("p")
            t = row.get("t")
            vol = row.get("v", 1)
            if not isinstance(p, (int, float)):
                continue
            try:
                vv = float(vol) if vol not in (None, "") else 1.0
            except (TypeError, ValueError):
                vv = 1.0
            with lock:
                trades.append({"p": float(p), "v": max(vv, 1e-9), "t": t})
                if len(trades) >= max_trades:
                    done.set()

    def on_open(ws) -> None:
        ws.send(json.dumps({"type": "subscribe", "symbol": sym}))

    url = f"wss://ws.finnhub.io?token={key}"
    ws_app = websocket.WebSocketApp(url, on_open=on_open, on_message=on_message)
    wst = threading.Thread(target=lambda: ws_app.run_forever(ping_interval=20, ping_timeout=10), daemon=True)
    wst.start()
    deadline = time.monotonic() + float(duration_sec)
    while time.monotonic() < deadline and not done.is_set():
        time.sleep(0.2)
    try:
        ws_app.close()
    except Exception:
        pass
    wst.join(timeout=3.0)

    with lock:
        rows = list(trades)
    if len(rows) < 15:
        return None, f"Лента: мало сделок ({len(rows)}) для оценки дисбаланса."

    rows.sort(key=lambda x: (x.get("t") is None, x.get("t") or 0))
    buy_v = 0.0
    sell_v = 0.0
    prev_p: float | None = None
    for r in rows:
        p = r["p"]
        vv = r["v"]
        if prev_p is None:
            buy_v += vv * 0.5
            sell_v += vv * 0.5
        elif p > prev_p:
            buy_v += vv
        elif p < prev_p:
            sell_v += vv
        else:
            buy_v += vv * 0.5
            sell_v += vv * 0.5
        prev_p = p
    tot = buy_v + sell_v
    imb = (buy_v - sell_v) / (tot + 1e-12)
    score = float(max(-1.0, min(1.0, imb)))
    return score, (
        f"Лента сделок (tick rule): сделок={len(rows)}, buy_vol≈{buy_v:.0f}, sell_vol≈{sell_v:.0f}, "
        f"imbalance={imb:+.3f}"
    )


# ── Аналитика Wall Street ────────────────────────────────────────────────────

@dataclass
class AnalystConsensus:
    """Консенсус рекомендаций аналитиков."""
    strong_buy: int
    buy: int
    hold: int
    sell: int
    strong_sell: int
    total: int
    period: str
    detail: str


@dataclass
class EarningsSurprise:
    """Последний EPS surprise."""
    period: str
    actual: float | None
    estimate: float | None
    surprise_pct: float | None
    detail: str


def fetch_recommendation_trends(
    symbol: str,
    api_key: str | None = None,
    timeout: float = 10.0,
) -> AnalystConsensus | None:
    """Рекомендации аналитиков Wall Street (бесплатный endpoint Finnhub)."""
    key = api_key or _token()
    if not key:
        return None
    sym = symbol.strip().upper()
    if sym.endswith(".ME"):
        return None  # Finnhub не поддерживает Мосбиржу
    _rate_wait()
    try:
        r = requests.get(
            f"{FINNHUB_BASE}/stock/recommendation",
            params={"symbol": sym, "token": key},
            timeout=timeout,
        )
        r.raise_for_status()
        data = r.json()
    except Exception:
        return None
    if not isinstance(data, list) or not data:
        return None
    # Берём самый свежий период
    latest = data[0]
    sb = int(latest.get("strongBuy", 0))
    b = int(latest.get("buy", 0))
    h = int(latest.get("hold", 0))
    s = int(latest.get("sell", 0))
    ss = int(latest.get("strongSell", 0))
    total = sb + b + h + s + ss
    period = str(latest.get("period", ""))
    if total == 0:
        return None
    positive_pct = (sb + b) / total * 100
    if positive_pct >= 70:
        consensus = "Покупать"
    elif positive_pct >= 50:
        consensus = "Скорее покупать"
    elif positive_pct <= 20:
        consensus = "Продавать"
    elif positive_pct <= 40:
        consensus = "Скорее продавать"
    else:
        consensus = "Держать"
    detail = (
        f"Активно покупать: {sb} | Покупать: {b} | Держать: {h} | Продавать: {s} | Активно продавать: {ss} — "
        f"Консенсус: {consensus} ({positive_pct:.0f}% позитивных)"
    )
    return AnalystConsensus(
        strong_buy=sb, buy=b, hold=h, sell=s, strong_sell=ss,
        total=total, period=period, detail=detail,
    )


def fetch_earnings_surprise(
    symbol: str,
    api_key: str | None = None,
    timeout: float = 10.0,
) -> EarningsSurprise | None:
    """Последний EPS surprise (бесплатный endpoint Finnhub, 4 квартала)."""
    key = api_key or _token()
    if not key:
        return None
    sym = symbol.strip().upper()
    if sym.endswith(".ME"):
        return None
    _rate_wait()
    try:
        r = requests.get(
            f"{FINNHUB_BASE}/stock/earnings",
            params={"symbol": sym, "token": key, "limit": 1},
            timeout=timeout,
        )
        r.raise_for_status()
        data = r.json()
    except Exception:
        return None
    if not isinstance(data, list) or not data:
        return None
    latest = data[0]
    actual = latest.get("actual")
    estimate = latest.get("estimate")
    surprise_pct = latest.get("surprisePercent")
    period = str(latest.get("period", ""))
    if actual is None and estimate is None:
        return None
    parts = []
    if actual is not None:
        parts.append(f"прибыль на акцию: ${actual:.2f}")
    if estimate is not None:
        parts.append(f"ожидали: ${estimate:.2f}")
    if surprise_pct is not None:
        if surprise_pct > 0:
            parts.append(f"лучше прогноза на {surprise_pct:.1f}% ✅")
        elif surprise_pct < 0:
            parts.append(f"хуже прогноза на {abs(surprise_pct):.1f}% ❌")
        else:
            parts.append("совпал с прогнозом")
    detail = f"Последний квартальный отчёт ({period}): " + ", ".join(parts)
    return EarningsSurprise(
        period=period,
        actual=float(actual) if actual is not None else None,
        estimate=float(estimate) if estimate is not None else None,
        surprise_pct=float(surprise_pct) if surprise_pct is not None else None,
        detail=detail,
    )
