"""
Stock Signal Analyzer — REST API (FastAPI).

Масштабируемый API layer для multi-tenant использования.
Telegram бот и другие клиенты обращаются сюда вместо прямого вызова engine.

Запуск:
  uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers 2

Переменные окружения:
  API_RATE_LIMIT_PER_MIN  — лимит запросов на пользователя (по умолчанию 30)
  API_WORKERS             — количество воркеров uvicorn (по умолчанию 2)
  API_SECRET_KEY          — секрет для JWT (опционально, для auth)
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from pathlib import Path
from typing import Dict, Optional

# Добавить корень проекта
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import stenv

stenv.load_project_env()

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from stock_signal_analyzer.cache import cache_analyze_key, get_cache
from stock_signal_analyzer.config_validator import validate_api_config
from stock_signal_analyzer.engine import SignalReport, build_report
from stock_signal_analyzer.live_price import fetch_live_price
from stock_signal_analyzer.market_data import fetch_snapshot_with_meta
from stock_signal_analyzer.rate_limiter import is_allowed
from stock_signal_analyzer.trade_plan import trade_plan_to_dict
from stock_signal_analyzer.universe import RU_BLUE_CHIPS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("api")

app = FastAPI(
    title="Stock Signal Analyzer API",
    version="1.0.0",
    description="Multi-factor stock signal analysis API",
)

_allowed_origins = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
if not _allowed_origins:
    _allowed_origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

_API_SECRET_KEY = os.environ.get("API_SECRET_KEY", "").strip()

if _API_SECRET_KEY:
    @app.middleware("http")
    async def api_key_middleware(request: Request, call_next):
        if request.url.path in ("/health", "/health/detailed", "/docs", "/openapi.json", "/redoc"):
            return await call_next(request)
        auth_header = request.headers.get("X-API-Key", "")
        if not auth_header or auth_header != _API_SECRET_KEY:
            return JSONResponse(
                status_code=401,
                content={"error": "Invalid or missing API key", "header": "X-API-Key"},
            )
        return await call_next(request)


# ── Rate Limiting ────────────────────────────────────────────────────────────

_RATE_LIMIT = int(os.environ.get("API_RATE_LIMIT_PER_MIN", "30"))
_SUB_RATE_LIMIT = 10


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    client = request.client.host if request.client else "unknown"
    if not is_allowed(client, limit=_RATE_LIMIT):
        return JSONResponse(
            status_code=429,
            content={"error": "Rate limit exceeded", "limit": _RATE_LIMIT},
        )
    response = await call_next(request)
    return response


# ── Models ───────────────────────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    symbol: str = Field(
        ...,
        description="Тикер (AAPL, SBER.ME)",
        max_length=20,
        pattern=r"^[A-Za-z0-9\-\.]{1,20}$",
    )
    fast_mode: bool = Field(False, description="Быстрый режим (без новостей)")
    use_finnhub_ws: bool = Field(False, description="WebSocket Finnhub")


class QuoteResponse(BaseModel):
    symbol: str
    company: str
    last_close: float
    live_price: Optional[float] = None
    change_pct: Optional[float] = None
    currency: str
    instrument_type: str


class SignalResponse(BaseModel):
    symbol: str
    company: str
    score: float
    signal_tier: str
    direction: str
    confidence: float
    verdict: str
    technical_score: float
    momentum_score: float
    news_score: float
    volume_score: float
    trade_plan: Optional[Dict] = None
    macro_dampening: float
    regime: str
    adx14: float
    atr_pct: Optional[float] = None


class HealthResponse(BaseModel):
    status: str
    version: str
    uptime_sec: float


_start_time = time.time()


def _get_live_price(symbol: str) -> float | None:
    """Получить актуальную цену из real-time источников."""
    return fetch_live_price(symbol)


# ── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(
        status="ok",
        version="1.0.0",
        uptime_sec=round(time.time() - _start_time, 1),
    )


@app.get("/health/detailed")
async def health_detailed():
    """Детальный health check всех компонентов."""
    from stock_signal_analyzer.scheduler import get_health_state, run_health_check
    loop = asyncio.get_running_loop()
    components = await loop.run_in_executor(None, run_health_check)
    scheduler_state = get_health_state()
    return {
        "status": "ok",
        "uptime_sec": round(time.time() - _start_time, 1),
        "components": components,
        "scheduler": scheduler_state,
    }


def _validate_symbol_path(symbol: str) -> str:
    sym = symbol.strip().upper()
    if not sym or ".." in sym or "/" in sym or "\\" in sym or len(sym) > 20:
        raise HTTPException(status_code=400, detail="Invalid symbol")
    return sym


@app.get("/quote/{symbol}", response_model=QuoteResponse)
async def get_quote(symbol: str):
    """Быстрая котировка по тикеру."""
    symbol = _validate_symbol_path(symbol)
    loop = asyncio.get_running_loop()
    try:
        snap, _info, profile = await loop.run_in_executor(
            None, lambda: fetch_snapshot_with_meta(symbol)
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception:
        raise HTTPException(status_code=500, detail="Internal error fetching quote")

    # Real-time price
    live_price = None
    try:
        live_price = await loop.run_in_executor(None, lambda: _get_live_price(snap.symbol))
    except Exception:
        pass

    change_pct = None
    if live_price and snap.last_close > 0:
        change_pct = round((live_price / snap.last_close - 1.0) * 100, 3)

    return QuoteResponse(
        symbol=snap.symbol,
        company=snap.company_name,
        last_close=snap.last_close,
        live_price=live_price,
        change_pct=change_pct,
        currency=snap.currency,
        instrument_type=profile.label,
    )


def _report_to_dict(report: SignalReport) -> dict:
    tp_dict = None
    if report.trade_plan and report.trade_plan.direction != "none":
        tp_dict = trade_plan_to_dict(report.trade_plan)
    return {
        "symbol": report.symbol,
        "company": report.company,
        "score": round(report.score, 4),
        "signal_tier": report.signal_tier,
        "direction": report.trade_plan.direction if report.trade_plan else "none",
        "confidence": round(report.confidence, 3),
        "verdict": report.verdict,
        "technical_score": round(report.technical_score, 4),
        "momentum_score": round(report.momentum_score, 4),
        "news_score": round(report.news_score, 4),
        "volume_score": round(report.volume_score, 4),
        "trade_plan": tp_dict,
        "macro_dampening": round(report.macro_dampening, 3),
        "regime": report.regime_label,
        "adx14": round(report.adx14, 1),
        "atr_pct": round(report.atr_pct, 3) if report.atr_pct else None,
    }


@app.post("/analyze", response_model=SignalResponse)
async def analyze(req: AnalyzeRequest):
    """Полный анализ тикера с торговым планом."""
    cache = get_cache()
    key = cache_analyze_key(req.symbol, req.fast_mode, req.use_finnhub_ws)
    cached = cache.get(key)
    if cached is not None:
        log.debug("Cache hit for %s", req.symbol)
        return cached

    loop = asyncio.get_running_loop()
    try:
        report = await loop.run_in_executor(
            None,
            lambda: build_report(
                req.symbol,
                use_finnhub_ws=req.use_finnhub_ws,
                fast_mode=req.fast_mode,
            ),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors())
    except Exception:
        log.exception("analyze error")
        raise HTTPException(status_code=500, detail="Internal analysis error")

    result = _report_to_dict(report)
    cache.set(key, result, ttl=300)
    return result


@app.get("/analyze/{symbol}", response_model=SignalResponse)
async def analyze_get(symbol: str, fast: bool = False):
    """GET-вариант анализа (удобно для браузера)."""
    symbol = _validate_symbol_path(symbol)
    return await analyze(AnalyzeRequest(symbol=symbol, fast_mode=fast))


# ── Subscription & Stats endpoints ──────────────────────────────────────────

@app.get("/subscription/{telegram_id}")
async def get_subscription(telegram_id: int, request: Request):
    """Информация о подписке пользователя."""
    client = request.client.host if request.client else "unknown"
    if not is_allowed(client, limit=10):
        raise HTTPException(status_code=429, detail="Rate limit exceeded for subscription endpoint")
    from stock_signal_analyzer.subscriptions import get_tier_limits, get_user_tier
    tier = get_user_tier(telegram_id)
    limits = get_tier_limits(tier)
    return {
        "telegram_id": telegram_id,
        "tier": tier,
        "limits": {
            "daily_analyses": limits.daily_analyses,
            "markets": limits.markets,
            "llm_sentiment": limits.llm_sentiment,
            "autocollect": limits.autocollect,
            "max_watchlist": limits.max_watchlist,
        },
    }


@app.get("/stats")
async def get_stats():
    """Общая статистика системы."""
    loop = asyncio.get_running_loop()

    def _gather_stats():
        stats = {}
        try:
            from stock_signal_analyzer.db import Outcome, Signal, User, get_session
            with get_session() as session:
                stats["total_users"] = session.query(User).count()
                stats["total_signals"] = session.query(Signal).count()
                stats["total_outcomes"] = session.query(Outcome).count()
        except Exception:
            stats["db"] = "unavailable"

        try:
            from stock_signal_analyzer.llm_learning import load_learning_state
            state = load_learning_state()
            if state:
                stats["learning"] = {
                    "outcomes_analyzed": state.total_outcomes_analyzed,
                    "win_rate": round(state.win_rate * 100, 1),
                    "last_updated": state.last_updated,
                }
        except Exception:
            pass

        return stats

    stats = await loop.run_in_executor(None, _gather_stats)
    return stats


@app.get("/learning/report")
async def learning_report():
    """Отчёт об обучении системы."""
    from stock_signal_analyzer.llm_learning import load_learning_state
    state = load_learning_state()
    if not state:
        return {"status": "no_data", "message": "Обучение ещё не проводилось"}
    return {
        "total_outcomes": state.total_outcomes_analyzed,
        "win_rate": round(state.win_rate * 100, 1),
        "avg_win_pct": round(state.avg_win_pct, 2),
        "avg_loss_pct": round(state.avg_loss_pct, 2),
        "weight_adjustments": state.weight_adjustments,
        "win_patterns": state.win_patterns,
        "loss_patterns": state.loss_patterns,
        "recommendations": state.recommendations,
        "ic_scores": state.ic_scores,
        "last_updated": state.last_updated,
    }


class TradingViewAlert(BaseModel):
    symbol: Optional[str] = Field(
        None,
        description="Тикер (AAPL, BTCUSDT)",
        max_length=20,
        pattern=r"^[A-Za-z0-9\:\-\.]{1,20}$",
    )
    ticker: Optional[str] = Field(
        None,
        description="Альтернативное поле тикера",
        max_length=20,
        pattern=r"^[A-Za-z0-9\:\-\.]{1,20}$",
    )
    price: Optional[float] = Field(None, description="Цена сигнала")
    close: Optional[float] = Field(None, description="Альтернативное поле цены")
    direction: Optional[str] = Field(None, description="Направление (long/short/buy/sell)")
    side: Optional[str] = Field(None, description="Альтернативное поле направления")
    action: Optional[str] = Field(None, description="Действие (buy/sell/alert)")
    message: Optional[str] = Field(None, description="Сообщение алерта", max_length=500)
    description: Optional[str] = Field(None, description="Описание стратегии", max_length=500)
    strategy: Optional[str] = Field(None, description="Название стратегии TradingView", max_length=100)
    interval: Optional[str] = Field(None, description="Таймфрейм (1h, 4h, 1d)", max_length=10)
    timestamp: Optional[str] = Field(None, description="Время алерта ISO", max_length=30)
    time: Optional[str] = Field(None, description="Альтернативное поле времени", max_length=30)
    secret: Optional[str] = Field(None, description="Секрет для валидации (опционально)", max_length=100)
    volume: Optional[float] = Field(None, description="Объём на свече")
    exchange: Optional[str] = Field(None, description="Биржа", max_length=50)


class TradingViewResponse(BaseModel):
    status: str
    symbol: str
    external_direction: Optional[str] = None
    external_price: Optional[float] = None
    ssa_score: float
    ssa_tier: str
    ssa_direction: str
    ssa_confidence: float
    trade_plan: Optional[Dict] = None
    ssa_verdict: str
    signal_id: Optional[str] = None
    message: Optional[str] = None


_TV_WEBHOOK_SECRET = os.environ.get("TV_WEBHOOK_SECRET", "").strip()


def _normalize_tv_symbol(raw: str) -> str:
    """Нормализовать TradingView символ для Yahoo Finance."""
    sym = raw.upper().strip()
    # Убрать префикс биржи (BINANCE:BTCUSDT → BTC-USD или BTCUSDT)
    if ":" in sym:
        sym = sym.split(":")[-1]
    # Крипто-пары: BTCUSDT → BTC-USD для Yahoo
    if sym.endswith("USDT") and len(sym) > 4:
        base = sym[:-4]
        return f"{base}-USD"
    if sym.endswith("USD") and not sym.endswith("-USD") and len(sym) > 3:
        return f"{sym}-USD" if sym != "USD" else sym
    # Российские тикеры: только известные MOEX голубые фишки → .ME
    if sym.isalpha() and len(sym) <= 5 and sym in RU_BLUE_CHIPS:
        return f"{sym}.ME"
    return sym


def _extract_direction(alert: TradingViewAlert) -> str | None:
    """Извлечь нормализованное направление из алерта."""
    for field in (alert.direction, alert.side, alert.action):
        if not field:
            continue
        val = field.lower().strip()
        if val in ("buy", "long", "bull", "bullish"):
            return "long"
        if val in ("sell", "short", "bear", "bearish"):
            return "short"
    return None


@app.post("/webhook/tradingview", response_model=TradingViewResponse)
async def webhook_tradingview(alert: TradingViewAlert):
    """
    Принимать алерты из TradingView, усиливать SSA-анализом.

    TradingView webhook настройка:
      URL: https://your-host/webhook/tradingview
      Message: {"symbol":"{{ticker}}","price":{{close}},"side":"{{strategy.order.action}}","interval":"{{interval}}","time":"{{time}}"}

    Если TV_WEBHOOK_SECRET задан — проверяется поле secret в JSON.
    """
    # Validate secret if configured
    if _TV_WEBHOOK_SECRET:
        if not alert.secret or alert.secret != _TV_WEBHOOK_SECRET:
            raise HTTPException(status_code=401, detail="Invalid webhook secret")

    symbol = alert.symbol or alert.ticker
    if not symbol:
        raise HTTPException(status_code=400, detail="Missing symbol/ticker in payload")

    sym = _normalize_tv_symbol(symbol)
    ext_dir = _extract_direction(alert)
    ext_price = alert.price or alert.close

    log.info("TradingView alert: %s (normalized: %s) dir=%s price=%s", symbol, sym, ext_dir, ext_price)

    loop = asyncio.get_running_loop()
    try:
        report = await loop.run_in_executor(
            None,
            lambda: build_report(sym, fast_mode=True),
        )
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Symbol not supported: {sym}")
    except Exception:
        log.exception("TradingView webhook analysis error for %s", sym)
        raise HTTPException(status_code=500, detail="Analysis engine error")

    tp_dict = None
    if report.trade_plan and report.trade_plan.direction != "none":
        tp_dict = trade_plan_to_dict(report.trade_plan)

    ssa_dir = report.trade_plan.direction if report.trade_plan else "none"

    # Store in signal log for walk-forward validation
    try:
        from stock_signal_analyzer.signal_log import (
            append_signal_record,
            build_record_from_report,
            log_path_from_env,
            make_signal_id,
        )
        path = log_path_from_env()
        if path:
            record = build_record_from_report(report, ref_price=report.ref_price, currency="USD")
            record["external_source"] = "tradingview"
            record["external_direction"] = ext_dir
            record["external_price"] = ext_price
            record["external_interval"] = alert.interval
            record["external_strategy"] = alert.strategy
            record["external_message"] = alert.message or alert.description
            signal_id = make_signal_id(report.symbol, record["ts_utc"])
            record["signal_id"] = signal_id
            append_signal_record(path, record)
    except Exception:
        log.warning("Failed to log TradingView signal", exc_info=True)

    # Build mismatch message if directions differ
    message = None
    if ext_dir and ssa_dir != "none" and ext_dir != ssa_dir:
        message = (
            f"⚠️ TradingView сигнал ({ext_dir}) противоречит SSA ({ssa_dir}). "
            f"SSA score={report.score:+.2f}, confidence={report.confidence:.2f}. "
            "Рекомендуем воздержаться или перепроверить."
        )
    elif ext_dir and ssa_dir != "none" and ext_dir == ssa_dir:
        message = (
            f"✅ Сигналы совпадают: {ext_dir}. "
            f"SSA score={report.score:+.2f}, tier={report.signal_tier}."
        )

    return TradingViewResponse(
        status="ok",
        symbol=sym,
        external_direction=ext_dir,
        external_price=ext_price,
        ssa_score=round(report.score, 4),
        ssa_tier=report.signal_tier,
        ssa_direction=ssa_dir,
        ssa_confidence=round(report.confidence, 3),
        trade_plan=tp_dict,
        ssa_verdict=report.verdict,
        signal_id=signal_id if path else None,
        message=message,
    )


# ── Startup event ────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup_event():
    """Инициализация при старте API."""
    validate_api_config()
    # Миграции Alembic (или fallback на init_db)
    try:
        import subprocess
        result = subprocess.run(
            ["alembic", "upgrade", "head"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            log.info("Alembic migrations applied")
        else:
            log.warning("Alembic failed (%s), falling back to init_db", result.stderr.strip()[:200])
            from stock_signal_analyzer.db import init_db
            init_db()
            log.info("Database tables initialized (fallback)")
    except Exception as e:
        log.warning("Database not available: %s", e)

    # Запустить scheduler (если не Celery mode)
    try:
        from stock_signal_analyzer.scheduler import start_apscheduler
        start_apscheduler()
        log.info("Scheduler started")
    except Exception as e:
        log.warning("Scheduler failed to start: %s", e)
