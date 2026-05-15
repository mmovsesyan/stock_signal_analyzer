"""
Клиент котировок Т-Инвестиции (Т-Банк): официальный SDK или совместимый пакет.

Официально: https://developer.tbank.ru/invest/sdk/python_sdk/faq_python/
  pip install t-tech-investments --index-url https://opensource.tbank.ru/api/v4/projects/238/packages/pypi/simple

Репозиторий и примеры: https://opensource.tbank.ru/invest/invest-python

Обычно доступен модуль ``tinkoff.invest`` (как в invest-python). Если его нет — ставьте
``pip install tinkoff-investments`` с PyPI (совместимый клиент).

Токен: настройки Т-Инвестиций → выпуск токена T-Invest API (переменные TINKOFF_INVEST_TOKEN / TINKOFF_TOKEN).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence


def _token() -> str | None:
    return os.environ.get("TINKOFF_INVEST_TOKEN") or os.environ.get("TINKOFF_TOKEN")


def _get_client_class() -> tuple[type[Any] | None, str]:
    """Возвращает (Client, имя_пакета) или (None, причина)."""
    # Официальный SDK t-tech-investments (pip install -r requirements-tbank.txt)
    try:
        from t_tech.invest import Client
        return Client, "t_tech.invest"
    except ImportError:
        pass
    # Совместимый пакет tinkoff-investments (pip install tinkoff-investments)
    try:
        from tinkoff.invest import Client
        return Client, "tinkoff.invest"
    except ImportError:
        pass
    return None, "нет пакета: pip install -r requirements-tbank.txt или pip install tinkoff-investments"


def _quotation_to_float(q: Any) -> float:
    if q is None:
        return float("nan")
    try:
        from t_tech.invest.utils import quotation_to_decimal
        return float(quotation_to_decimal(q))
    except ImportError:
        pass
    try:
        from tinkoff.invest.utils import quotation_to_decimal
        return float(quotation_to_decimal(q))
    except (ImportError, Exception):
        pass
    units = int(getattr(q, "units", 0) or 0)
    nano = int(getattr(q, "nano", 0) or 0)
    return units + nano / 1e9


def _normalize_ticker(symbol: str) -> str:
    s = symbol.strip().upper()
    if s.endswith(".ME"):
        return s[:-3]
    return s


@dataclass
class TbankQuote:
    ticker: str
    name: str
    last_price: float
    currency: str
    figi: str
    instrument_uid: str
    detail: str


@dataclass
class TbankVolumeContext:
    """
    Интрадей-контекст по свечам API (без торговли): сессионный VWAP и упрощённый POC.

    Идея из примеров роботов с объёмным профилем на портале T-API
    (см. https://developer.tbank.ru/invest/sdk/python_sdk/robots): якорная цена по объёму
    и реакция цены относительно неё, без полноценного market profile.
    """

    vwap: float
    poc: float
    last_vs_vwap_pct: float | None
    n_candles: int
    total_volume: float
    detail: str


def sdk_install_hint() -> str:
    return (
        "Официальный SDK: pip install -r requirements-tbank.txt "
        "(см. https://developer.tbank.ru/invest/sdk/python_sdk/faq_python/). "
        "Альтернатива: pip install tinkoff-investments"
    )


def tbank_sdk_available() -> bool:
    if not _token():
        return False
    cls, _ = _get_client_class()
    return cls is not None


def tbank_volume_context_enabled() -> bool:
    """Доп. запрос свечей для VWAP/POC: отключите ``SSA_TBANK_VOLUME=0``."""
    v = os.environ.get("SSA_TBANK_VOLUME", "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _find_instrument(client: Any, ticker: str) -> Any | None:
    """Ищет инструмент по тикеру, возвращает акцию с доступными торгами (TQBR приоритет)."""
    found = client.instruments.find_instrument(query=ticker)
    if not found.instruments:
        return None
    # Фильтруем: только акции (share), доступные для торговли, предпочтительно TQBR
    matches = [
        x for x in found.instruments
        if getattr(x, "ticker", "").upper() == ticker
        and getattr(x, "instrument_type", "") == "share"
        and getattr(x, "api_trade_available_flag", False)
    ]
    if not matches:
        return None
    return next(
        (m for m in matches if getattr(m, "class_code", "") == "TQBR"),
        matches[0]
    )


def fetch_last_price_tbank(symbol: str, token: str | None = None) -> TbankQuote | None:
    """
    Последняя цена: FindInstrument + GetLastPrices (как в invest-python).
    """
    tok = token or _token()
    if not tok:
        return None
    client_cls, _src = _get_client_class()
    if client_cls is None:
        return None

    t = _normalize_ticker(symbol)
    with client_cls(tok) as client:
        inst = _find_instrument(client, t)
        if inst is None:
            return None
        uid = getattr(inst, "uid", None) or ""
        figi = getattr(inst, "figi", "") or ""
        name = getattr(inst, "name", "") or t

        if uid:
            lp = client.market_data.get_last_prices(instrument_id=[uid])
        elif figi:
            lp = client.market_data.get_last_prices(figi=[figi])
        else:
            return None
        if not lp.last_prices:
            return None
        p0 = lp.last_prices[0]
        price = _quotation_to_float(getattr(p0, "price", None))
        if price != price or price <= 0:
            return None
        cur = str(getattr(inst, "currency", "") or "rub").upper()
        detail = f"Т-Инвестиции API: {name}, last={price:.4f} {cur}"
        return TbankQuote(
            ticker=t,
            name=name,
            last_price=float(price),
            currency=cur,
            figi=figi,
            instrument_uid=uid,
            detail=detail,
        )


def fetch_quote_and_volume_context(
    symbol: str,
    token: str | None = None,
    *,
    yahoo_last_daily_close: float | None = None,
    hours_back: int = 8,
) -> tuple[TbankQuote | None, TbankVolumeContext | None]:
    """
    Одна gRPC-сессия: котировка + (опционально) свечи для VWAP/POC.
    Экономит ~100–300 мс по сравнению с двумя раздельными вызовами.
    """
    tok = token or _token()
    if not tok:
        return None, None
    client_cls, _ = _get_client_class()
    if client_cls is None:
        return None, None

    t = _normalize_ticker(symbol)

    CandleInterval = None
    if tbank_volume_context_enabled():
        try:
            from t_tech.invest import CandleInterval as CI
            CandleInterval = CI
        except ImportError:
            try:
                from tinkoff.invest import CandleInterval as CI
                CandleInterval = CI
            except ImportError:
                pass

    with client_cls(tok) as client:
        inst = _find_instrument(client, t)
        if inst is None:
            return None, None
        uid = getattr(inst, "uid", None) or ""
        figi = getattr(inst, "figi", "") or ""
        name = getattr(inst, "name", "") or t

        if uid:
            lp = client.market_data.get_last_prices(instrument_id=[uid])
        elif figi:
            lp = client.market_data.get_last_prices(figi=[figi])
        else:
            return None, None
        if not lp.last_prices:
            return None, None
        p0 = lp.last_prices[0]
        price = _quotation_to_float(getattr(p0, "price", None))
        if price != price or price <= 0:
            return None, None
        cur = str(getattr(inst, "currency", "") or "rub").upper()
        detail = f"Т-Инвестиции API: {name}, last={price:.4f} {cur}"
        quote = TbankQuote(
            ticker=t, name=name, last_price=float(price),
            currency=cur, figi=figi, instrument_uid=uid, detail=detail,
        )

        vol_ctx: TbankVolumeContext | None = None
        if CandleInterval is not None:
            dt_to = datetime.now(timezone.utc)
            dt_from = dt_to - timedelta(hours=max(1, int(hours_back)))
            try:
                candles = _get_candles_for_instrument(
                    client,
                    instrument_uid=uid, figi=figi,
                    dt_from=dt_from, dt_to=dt_to,
                    interval=CandleInterval.CANDLE_INTERVAL_5_MIN,
                )
                stats = _vwap_and_poc_from_candles(candles)
                if stats is not None:
                    vwap, poc, tot_vol = stats
                    last_vs: float | None = None
                    ref = yahoo_last_daily_close if yahoo_last_daily_close else float(price)
                    if ref and ref == ref and vwap > 0:
                        last_vs = (float(price) / vwap - 1.0) * 100.0
                    vd = (
                        f"Т-Инвестиции: объёмный контекст ({name}): VWAP={vwap:.4f}, POC≈{poc:.4f}, "
                        f"свечей={len(candles)}, vol={tot_vol:.0f}"
                    )
                    if last_vs is not None:
                        vd += f", last vs VWAP={last_vs:+.2f}%"
                    vol_ctx = TbankVolumeContext(
                        vwap=vwap, poc=poc, last_vs_vwap_pct=last_vs,
                        n_candles=len(candles), total_volume=tot_vol, detail=vd,
                    )
            except Exception:
                pass

    return quote, vol_ctx


def _candle_ohlcv(c: Any) -> tuple[float, float, float, float, float, float]:
    """OHLC, объём и типичная цена (H+L+C)/3 для одной свечи."""
    o = _quotation_to_float(getattr(c, "open", None))
    h = _quotation_to_float(getattr(c, "high", None))
    low = _quotation_to_float(getattr(c, "low", None))
    cl = _quotation_to_float(getattr(c, "close", None))
    vol = float(getattr(c, "volume", 0) or 0)
    if vol < 0:
        vol = 0.0
    typical = (h + low + cl) / 3.0 if (h == h and low == low and cl == cl) else cl
    if typical != typical:
        typical = cl if cl == cl else o
    return o, h, low, cl, vol, typical


def _vwap_and_poc_from_candles(candles: Sequence[Any]) -> tuple[float, float, float] | None:
    """
    VWAP по типичной цене; POC — цена центра бина с макс. объёмом (гистограмма по типичной цене).
    Возвращает (vwap, poc, total_volume) или None.
    """
    if not candles:
        return None
    num = 0.0
    den = 0.0
    lows: list[float] = []
    highs: list[float] = []
    typicals: list[float] = []
    vols: list[float] = []
    for c in candles:
        _o, h, low, _cl, vol, typ = _candle_ohlcv(c)
        if vol <= 0 or typ != typ:
            continue
        num += typ * vol
        den += vol
        lows.append(low)
        highs.append(h)
        typicals.append(typ)
        vols.append(vol)
    if den <= 0 or not typicals:
        return None
    vwap = num / den
    lo = min(min(lows), min(typicals))
    hi = max(max(highs), max(typicals))
    if hi <= lo:
        poc = vwap
    else:
        n_bins = max(5, min(32, len(typicals)))
        width = (hi - lo) / n_bins
        if width <= 0:
            poc = vwap
        else:
            counts = [0.0] * n_bins
            for typ, vol in zip(typicals, vols):
                idx = int((typ - lo) / width)
                if idx >= n_bins:
                    idx = n_bins - 1
                elif idx < 0:
                    idx = 0
                counts[idx] += vol
            best_i = max(range(n_bins), key=lambda i: counts[i])
            poc = lo + (best_i + 0.5) * width
    return vwap, poc, den


def _get_candles_for_instrument(
    client: Any,
    *,
    instrument_uid: str,
    figi: str,
    dt_from: datetime,
    dt_to: datetime,
    interval: Any,
) -> list[Any]:
    """Вызывает market_data.get_candles с instrument_id или figi в зависимости от версии SDK."""
    md = client.market_data
    attempts: list[dict[str, Any]] = []
    if instrument_uid:
        attempts.append(
            {"instrument_id": instrument_uid, "from_": dt_from, "to": dt_to, "interval": interval}
        )
    if figi:
        attempts.append({"figi": figi, "from_": dt_from, "to": dt_to, "interval": interval})
    if not attempts:
        return []
    last_err: Exception | None = None
    for kwargs in attempts:
        try:
            resp = md.get_candles(**kwargs)
            return list(getattr(resp, "candles", []) or [])
        except TypeError as e:
            last_err = e
            continue
        except Exception as e:
            last_err = e
            break
    if last_err is not None:
        raise last_err
    return []


def fetch_session_volume_context(
    symbol: str,
    token: str | None = None,
    *,
    last_price: float | None = None,
    hours_back: int = 8,
) -> TbankVolumeContext | None:
    """
    Свечи за последние ``hours_back`` часов (UTC), интервал 5 мин — VWAP и упрощённый POC.

    Нужен токен и установленный ``tinkoff.invest``. Без сделок и счёта: только рыночные данные.
    """
    tok = token or _token()
    if not tok:
        return None
    client_cls, _ = _get_client_class()
    if client_cls is None:
        return None
    try:
        from t_tech.invest import CandleInterval
    except ImportError:
        try:
            from tinkoff.invest import CandleInterval
        except ImportError:
            return None

    t = _normalize_ticker(symbol)
    dt_to = datetime.now(timezone.utc)
    dt_from = dt_to - timedelta(hours=max(1, int(hours_back)))

    with client_cls(tok) as client:
        found = client.instruments.find_instrument(query=t)
        if not found.instruments:
            return None
        inst = None
        for x in found.instruments:
            if getattr(x, "ticker", "").upper() == t:
                inst = x
                break
        if inst is None:
            inst = found.instruments[0]
        uid = getattr(inst, "uid", None) or ""
        figi = getattr(inst, "figi", "") or ""
        name = getattr(inst, "name", "") or t

        try:
            candles = _get_candles_for_instrument(
                client,
                instrument_uid=uid,
                figi=figi,
                dt_from=dt_from,
                dt_to=dt_to,
                interval=CandleInterval.CANDLE_INTERVAL_5_MIN,
            )
        except Exception:
            return None

    stats = _vwap_and_poc_from_candles(candles)
    if stats is None:
        return None
    vwap, poc, tot_vol = stats
    last_vs: float | None = None
    if last_price is not None and last_price == last_price and vwap > 0:
        last_vs = (float(last_price) / vwap - 1.0) * 100.0

    detail = (
        f"Т-Инвестиции: объёмный контекст ({name}): VWAP={vwap:.4f}, POC≈{poc:.4f}, "
        f"свечей={len(candles)}, vol={tot_vol:.0f}"
    )
    if last_vs is not None:
        detail += f", last vs VWAP={last_vs:+.2f}%"

    return TbankVolumeContext(
        vwap=vwap,
        poc=poc,
        last_vs_vwap_pct=last_vs,
        n_candles=len(candles),
        total_volume=tot_vol,
        detail=detail,
    )


def fetch_daily_history(
    symbol: str,
    token: str | None = None,
    days: int = 365,
) -> "pd.DataFrame | None":
    """
    Дневные свечи из T-Bank API → DataFrame с колонками Open, High, Low, Close, Volume.
    Используется как fallback, когда Yahoo Finance не отдаёт данные (РФ-тикеры).
    """
    import pandas as pd

    tok = token or _token()
    if not tok:
        return None
    client_cls, _ = _get_client_class()
    if client_cls is None:
        return None
    try:
        from t_tech.invest import CandleInterval
    except ImportError:
        try:
            from tinkoff.invest import CandleInterval
        except ImportError:
            return None

    t = _normalize_ticker(symbol)
    dt_to = datetime.now(timezone.utc)
    dt_from = dt_to - timedelta(days=days)

    with client_cls(tok) as client:
        inst = _find_instrument(client, t)
        if inst is None:
            return None
        uid = getattr(inst, "uid", None) or ""
        figi = getattr(inst, "figi", "") or ""
        name = getattr(inst, "name", "") or t

        try:
            candles = _get_candles_for_instrument(
                client,
                instrument_uid=uid, figi=figi,
                dt_from=dt_from, dt_to=dt_to,
                interval=CandleInterval.CANDLE_INTERVAL_DAY,
            )
        except Exception:
            return None

    if not candles:
        return None

    rows = []
    for c in candles:
        o, h, low, cl, vol, _ = _candle_ohlcv(c)
        ts = getattr(c, "time", None)
        if ts is None:
            continue
        if o != o or cl != cl:
            continue
        rows.append({
            "Open": o, "High": h, "Low": low, "Close": cl,
            "Volume": vol, "Date": ts,
        })

    if not rows:
        return None

    df = pd.DataFrame(rows)
    df["Date"] = pd.to_datetime(df["Date"], utc=True)
    df = df.set_index("Date").sort_index()
    df.attrs["company_name"] = name
    df.attrs["currency"] = str(getattr(inst, "currency", "") or "rub").upper()
    return df


def volume_alignment_adjustment(
    day_change_pct: float | None,
    last_vs_vwap_pct: float | None,
) -> float:
    """
    Небольшой сдвиг к [-1, 1]: дневное направление совпадает с положением относительно VWAP.
    """
    if day_change_pct is None or last_vs_vwap_pct is None:
        return 0.0
    if abs(day_change_pct) < 1e-6:
        return 0.0
    same = (day_change_pct > 0.0) == (last_vs_vwap_pct > 0.0)
    mag = min(1.0, abs(last_vs_vwap_pct) / 3.0)
    return (0.1 if same else -0.06) * mag
