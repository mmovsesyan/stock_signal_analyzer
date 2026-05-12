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
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional

# Добавить корень проекта
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import stenv
stenv.load_project_env()

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from stock_signal_analyzer.engine import build_report, SignalReport
from stock_signal_analyzer.market_data import fetch_snapshot_with_meta
from stock_signal_analyzer.trade_plan import trade_plan_to_dict
from stock_signal_analyzer.live_price import fetch_live_price
from stock_signal_analyzer.config_validator import validate_telegram_config, validate_api_config

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
_rate_store: dict[str, list[float]] = defaultdict(list)


def _check_rate_limit(client_id: str) -> bool:
    now = time.time()
    calls = _rate_store[client_id]
    _rate_store[client_id] = [t for t in calls if now - t < 60]
    if not _rate_store[client_id]:
        del _rate_store[client_id]
        return True
    if len(_rate_store[client_id]) >= _RATE_LIMIT:
        return False
    _rate_store[client_id].append(now)
    return True


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    client = request.client.host if request.client else "unknown"
    if not _check_rate_limit(client):
        return JSONResponse(
            status_code=429,
            content={"error": "Rate limit exceeded", "limit": _RATE_LIMIT},
        )
    response = await call_next(request)
    return response


# ── Models ───────────────────────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    symbol: str = Field(..., description="Тикер (AAPL, SBER.ME)")
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
        version="2.0.0",
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


@app.get("/quote/{symbol}", response_model=QuoteResponse)
async def get_quote(symbol: str):
    """Быстрая котировка по тикеру."""
    loop = asyncio.get_running_loop()
    try:
        snap, _info, profile = await loop.run_in_executor(
            None, lambda: fetch_snapshot_with_meta(symbol)
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
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


@app.post("/analyze", response_model=SignalResponse)
async def analyze(req: AnalyzeRequest):
    """Полный анализ тикера с торговым планом."""
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
    except Exception as e:
        log.exception("analyze error")
        raise HTTPException(status_code=500, detail="Internal analysis error")

    tp_dict = None
    if report.trade_plan and report.trade_plan.direction != "none":
        tp_dict = trade_plan_to_dict(report.trade_plan)

    return SignalResponse(
        symbol=report.symbol,
        company=report.company,
        score=round(report.score, 4),
        signal_tier=report.signal_tier,
        direction=report.trade_plan.direction if report.trade_plan else "none",
        confidence=round(report.confidence, 3),
        verdict=report.verdict,
        technical_score=round(report.technical_score, 4),
        momentum_score=round(report.momentum_score, 4),
        news_score=round(report.news_score, 4),
        volume_score=round(report.volume_score, 4),
        trade_plan=tp_dict,
        macro_dampening=round(report.macro_dampening, 3),
        regime=report.regime_label,
        adx14=round(report.adx14, 1),
        atr_pct=round(report.atr_pct, 3) if report.atr_pct else None,
    )


@app.get("/analyze/{symbol}", response_model=SignalResponse)
async def analyze_get(symbol: str, fast: bool = False):
    """GET-вариант анализа (удобно для браузера)."""
    return await analyze(AnalyzeRequest(symbol=symbol, fast_mode=fast))


# ── Subscription & Stats endpoints ──────────────────────────────────────────


@app.get("/subscription/{telegram_id}")
async def get_subscription(telegram_id: int):
    """Информация о подписке пользователя."""
    from stock_signal_analyzer.subscriptions import get_user_tier, get_tier_limits, format_subscription_info
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
            from stock_signal_analyzer.db import get_session, User, Signal, Outcome
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


# ── Startup event ────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup_event():
    """Инициализация при старте API."""
    validate_api_config()
    # Создать таблицы если БД доступна
    try:
        from stock_signal_analyzer.db import init_db
        init_db()
        log.info("Database tables initialized")
    except Exception as e:
        log.warning("Database not available: %s", e)

    # Запустить scheduler (если не Celery mode)
    try:
        from stock_signal_analyzer.scheduler import start_apscheduler
        start_apscheduler()
        log.info("Scheduler started")
    except Exception as e:
        log.warning("Scheduler failed to start: %s", e)
