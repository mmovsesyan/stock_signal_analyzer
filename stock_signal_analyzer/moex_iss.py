"""Почти онлайн-котировки с MOEX ISS (бесплатно, опрос REST; не WebSocket биржи)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable, TypeVar

import requests

from .retry_utils import retry_with_backoff

_log = logging.getLogger(__name__)
T = TypeVar("T")


@retry_with_backoff(max_retries=2, initial_delay=0.5, backoff_factor=2.0,
                    retry_on=(requests.RequestException,))
def _moex_get(url: str, params: dict[str, Any], timeout: float) -> requests.Response:
    """HTTP GET к MOEX ISS с retry (transient network errors, 502/503)."""
    r = requests.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r


def fetch_moex_history(secid: str, days: int = 365, timeout: float = 15.0) -> "pd.DataFrame | None":
    """
    Дневные свечи с MOEX ISS (бесплатно, без токена).

    Возвращает DataFrame с колонками Open, High, Low, Close, Volume
    или None если данных нет.
    """
    import pandas as pd
    from datetime import date, timedelta

    sid = secid.replace(".ME", "").strip().upper()
    if not sid or len(sid) > 12 or not sid.isalnum():
        return None

    d_from = (date.today() - timedelta(days=days)).isoformat()
    d_to = date.today().isoformat()

    # MOEX ISS отдаёт максимум 100 строк за запрос, нужна пагинация
    all_rows: list[list] = []
    cols: list[str] = []
    start = 0

    for _ in range(20):  # макс 20 страниц = 2000 свечей
        url = (
            "https://iss.moex.com/iss/history/engines/stock/markets/shares"
            f"/boards/TQBR/securities/{sid}.json"
        )
        params = {
            "iss.meta": "off",
            "history.columns": "TRADEDATE,OPEN,HIGH,LOW,CLOSE,VOLUME",
            "from": d_from,
            "till": d_to,
            "start": str(start),
        }
        try:
            r = _moex_get(url, params=params, timeout=timeout)
            data = r.json()
        except Exception:
            break

        h_cols, h_rows = _table_dict(data, "history")
        if not h_rows:
            break
        if not cols:
            cols = h_cols
        all_rows.extend(h_rows)
        # Если вернулось меньше 100 строк — это последняя страница
        if len(h_rows) < 100:
            break
        start += 100

    if not all_rows or not cols:
        return None

    raw = pd.DataFrame(all_rows, columns=cols)

    # Преобразовать в стандартный OHLCV формат
    df = pd.DataFrame()
    df["Open"] = pd.to_numeric(raw["OPEN"], errors="coerce")
    df["High"] = pd.to_numeric(raw["HIGH"], errors="coerce")
    df["Low"] = pd.to_numeric(raw["LOW"], errors="coerce")
    df["Close"] = pd.to_numeric(raw["CLOSE"], errors="coerce")
    df["Volume"] = pd.to_numeric(raw["VOLUME"], errors="coerce")
    df.index = pd.to_datetime(raw["TRADEDATE"])
    df.index.name = "Date"

    # Убрать строки с нулевыми ценами (выходные, нет торгов)
    df = df[(df["Close"] > 0) & (df["Open"] > 0)].dropna()

    if df.empty:
        return None

    return df


@dataclass
class MoexQuote:
    secid: str
    last: float | None
    change_pct_from_prev: float | None
    detail: str


@dataclass
class MoexVolumeToday:
    secid: str
    voltoday: float | None
    valtoday: float | None
    numtrades: float | None
    detail: str


def _table_dict(payload: dict, table: str) -> tuple[list[str], list]:
    block = payload.get(table) or {}
    cols = block.get("columns") or []
    rows = block.get("data") or []
    return cols, rows


def fetch_tqbr_quote(secid: str, timeout: float = 12.0) -> MoexQuote:
    """Последняя цена и изменение к предыдущему закрытию на режиме TQBR."""
    sid = secid.replace(".ME", "").strip().upper()
    # Валидация: только буквы и цифры, макс 12 символов (защита от path traversal / SSRF)
    if not sid or len(sid) > 12 or not sid.isalnum():
        return MoexQuote(secid=sid, last=None, change_pct_from_prev=None, detail="Невалидный SECID.")
    url = (
        f"https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/securities/{sid}.json"
    )
    params = {
        "iss.meta": "off",
        "securities.columns": "SECID",
        "marketdata.columns": "SECID,LAST,LASTTOPREVPRICE",
    }
    r = _moex_get(url, params=params, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    m_cols, m_rows = _table_dict(data, "marketdata")
    if not m_rows:
        return MoexQuote(
            secid=sid,
            last=None,
            change_pct_from_prev=None,
            detail="Нет строки marketdata (возможно неверный SECID или выходной).",
        )
    rm = None
    for row in m_rows:
        m = {c: row[i] for i, c in enumerate(m_cols) if i < len(row)}
        if m.get("SECID", sid) == sid:
            rm = m
            break
    if rm is None:
        return MoexQuote(
            secid=sid,
            last=None,
            change_pct_from_prev=None,
            detail=f"SECID {sid} не найден в ответе MOEX TQBR.",
        )
    last = rm.get("LAST")
    ch = rm.get("LASTTOPREVPRICE")
    if isinstance(ch, (int, float)):
        chp = float(ch)
    else:
        chp = None
    try:
        last_f = float(last) if last not in (None, "") else None
    except (TypeError, ValueError):
        last_f = None
    detail = f"MOEX TQBR: LAST={last_f}, LASTTOPREVPRICE={chp}"
    return MoexQuote(secid=sid, last=last_f, change_pct_from_prev=chp, detail=detail)


def fetch_tqbr_volume_today(secid: str, timeout: float = 12.0) -> MoexVolumeToday:
    """Сегодняшний объём в бумагах (VOLTODAY), оборот (VALTODAY), число сделок (NUMTRADES)."""
    sid = secid.replace(".ME", "").strip().upper()
    if not sid or len(sid) > 12 or not sid.isalnum():
        return MoexVolumeToday(secid=sid, voltoday=None, valtoday=None, numtrades=None, detail="Невалидный SECID.")
    url = (
        f"https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/securities/{sid}.json"
    )
    params = {
        "iss.meta": "off",
        "securities.columns": "SECID",
        "marketdata.columns": "SECID,VOLTODAY,VALTODAY,NUMTRADES",
    }
    r = _moex_get(url, params=params, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    m_cols, m_rows = _table_dict(data, "marketdata")
    if not m_rows:
        return MoexVolumeToday(
            secid=sid,
            voltoday=None,
            valtoday=None,
            numtrades=None,
            detail="Нет marketdata.",
        )
    rm = None
    for row in m_rows:
        m = {c: row[i] for i, c in enumerate(m_cols) if i < len(row)}
        if m.get("SECID", sid) == sid:
            rm = m
            break
    if rm is None:
        return MoexVolumeToday(
            secid=sid,
            voltoday=None,
            valtoday=None,
            numtrades=None,
            detail=f"SECID {sid} не найден в ответе MOEX TQBR.",
        )

    def _f(x: object) -> float | None:
        if x is None or x == "":
            return None
        try:
            return float(x)
        except (TypeError, ValueError):
            return None

    vo = _f(rm.get("VOLTODAY"))
    va = _f(rm.get("VALTODAY"))
    nt = _f(rm.get("NUMTRADES"))
    detail = f"VOLTODAY={vo}, VALTODAY={va}, NUMTRADES={nt}"
    return MoexVolumeToday(secid=sid, voltoday=vo, valtoday=va, numtrades=nt, detail=detail)
