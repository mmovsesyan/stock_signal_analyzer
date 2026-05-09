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

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("api")

app = FastAPI(
    title="Stock Signal Analyzer API",
    version="1.0.0",
    description="Multi-factor stock signal analysis API",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Rate Limiting ────────────────────────────────────────────────────────────

_RATE_LIMIT = int(os.environ.get("API_RATE_LIMIT_PER_MIN", "30"))
_rate_store: dict[str, list[float]] = defaultdict(list)


def _check_rate_limit(client_id: str) -> bool:
    now = time.time()
    calls = _rate_store[client_id]
    _rate_store[client_id] = [t for t in calls if now - t < 60]
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


# ── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(
        status="ok",
        version="1.0.0",
        uptime_sec=round(time.time() - _start_time, 1),
    )


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

    return QuoteResponse(
        symbol=snap.symbol,
        company=snap.company_name,
        last_close=snap.last_close,
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
