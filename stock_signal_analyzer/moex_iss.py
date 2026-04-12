"""Почти онлайн-котировки с MOEX ISS (бесплатно, опрос REST; не WebSocket биржи)."""

from __future__ import annotations

from dataclasses import dataclass

import requests


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
    url = (
        f"https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/securities/{sid}.json"
    )
    params = {
        "iss.meta": "off",
        "securities.columns": "SECID",
        "marketdata.columns": "SECID,LAST,LASTTOPREVPRICE",
    }
    r = requests.get(url, params=params, timeout=timeout)
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
        row = m_rows[0]
        rm = {c: row[i] for i, c in enumerate(m_cols) if i < len(row)}
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
    url = (
        f"https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/securities/{sid}.json"
    )
    params = {
        "iss.meta": "off",
        "securities.columns": "SECID",
        "marketdata.columns": "SECID,VOLTODAY,VALTODAY,NUMTRADES",
    }
    r = requests.get(url, params=params, timeout=timeout)
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
        row = m_rows[0]
        rm = {c: row[i] for i, c in enumerate(m_cols) if i < len(row)}

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
