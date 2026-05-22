"""
Celery tasks — асинхронная обработка тяжёлых задач.

Бот и API кладут задачи в Redis очередь, workers обрабатывают параллельно.
Это позволяет обслуживать 50-100 юзеров без блокировки.

Переменные окружения:
  CELERY_BROKER_URL  — Redis URL (по умолчанию redis://localhost:6379/0)
  CELERY_RESULT_BACKEND — Redis URL для результатов

Запуск worker:
  celery -A stock_signal_analyzer.tasks worker --loglevel=info --concurrency=4

Запуск beat (периодические задачи):
  celery -A stock_signal_analyzer.tasks beat --loglevel=info
"""

from __future__ import annotations

import logging
import os
from datetime import timedelta

from celery import Celery
from celery.schedules import crontab

import stenv
stenv.load_project_env()

_log = logging.getLogger(__name__)

BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")

app = Celery(
    "stock_signal_analyzer",
    broker=BROKER_URL,
    backend=RESULT_BACKEND,
)

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_soft_time_limit=120,  # 2 мин soft limit
    task_time_limit=180,       # 3 мин hard limit
    worker_max_tasks_per_child=100,  # перезапуск worker после 100 задач (memory leak prevention)
    task_acks_late=True,       # подтверждение после выполнения (retry при crash)
    worker_prefetch_multiplier=1,  # по одной задаче за раз (fair scheduling)
)

# Периодические задачи
app.conf.beat_schedule = {
    "outcome-tracker": {
        "task": "stock_signal_analyzer.tasks.run_outcome_tracker",
        "schedule": timedelta(hours=1),
    },
    "llm-learning": {
        "task": "stock_signal_analyzer.tasks.run_learning",
        "schedule": timedelta(hours=6),
    },
    "collect-signals": {
        "task": "stock_signal_analyzer.tasks.run_collect_all",
        "schedule": timedelta(hours=4),
    },
    "scan-all-signals": {
        "task": "stock_signal_analyzer.tasks.scan_all_signals",
        "schedule": timedelta(minutes=30),
    },
}


# ── Tasks ────────────────────────────────────────────────────────────────────


@app.task(bind=True, max_retries=2, default_retry_delay=30)
def analyze_ticker(self, symbol: str, user_id: int | None = None, fast_mode: bool = False) -> dict:
    """
    Полный анализ тикера. Возвращает dict с результатами.
    Используется ботом и API вместо прямого вызова engine.
    """
    try:
        from .engine import build_report
        from .user_settings import load_prefs

        filter_type = "balanced"
        if user_id is not None:
            try:
                prefs = load_prefs(user_id)
                filter_type = prefs.signal_filter_type
            except Exception:
                pass

        report = build_report(symbol, fast_mode=fast_mode, filter_type=filter_type, user_id=user_id)

        # Сохранить в БД (если доступна)
        try:
            from .signal_log import build_record_from_report
            record = build_record_from_report(report, report.ref_price, "USD")
            if user_id is not None:
                record["user_id"] = user_id
            _save_signal_to_db(record, user_id)
        except Exception:
            pass

        # Вернуть основные данные (сериализуемые)
        from .trade_plan import trade_plan_to_dict
        tp_dict = None
        if report.trade_plan and report.trade_plan.direction != "none":
            tp_dict = trade_plan_to_dict(report.trade_plan)

        return {
            "symbol": report.symbol,
            "company": report.company,
            "score": round(report.score, 4),
            "signal_tier": report.signal_tier,
            "confidence": round(report.confidence, 3),
            "verdict": report.verdict,
            "direction": report.trade_plan.direction if report.trade_plan else "none",
            "technical_score": round(report.technical_score, 4),
            "momentum_score": round(report.momentum_score, 4),
            "news_score": round(report.news_score, 4),
            "volume_score": round(report.volume_score, 4),
            "trade_plan": tp_dict,
            "macro_dampening": round(report.macro_dampening, 3),
            "regime": report.regime_label,
            "adx14": round(report.adx14, 1),
            "atr_pct": round(report.atr_pct, 3) if report.atr_pct else None,
            "ref_price": round(report.ref_price, 4),
        }
    except Exception as exc:
        _log.exception("analyze_ticker failed for %s", symbol)
        self.retry(exc=exc)


@app.task(bind=True, max_retries=1)
def run_outcome_tracker(self) -> dict:
    """Проверить исходы открытых сигналов."""
    try:
        from .outcome_tracker import OutcomeTracker
        tracker = OutcomeTracker()
        tracker.check_all_outcomes()
        stats = tracker.get_statistics()
        return {"status": "ok", "stats": stats}
    except Exception as exc:
        _log.exception("outcome_tracker failed")
        self.retry(exc=exc)


@app.task(bind=True, max_retries=1)
def run_learning(self) -> dict:
    """Запустить цикл обучения (IC + LLM)."""
    try:
        from .llm_learning import run_learning_cycle, format_learning_report
        state = run_learning_cycle()
        return {
            "status": "ok",
            "outcomes_analyzed": state.total_outcomes_analyzed,
            "win_rate": state.win_rate,
            "adjustments": state.weight_adjustments,
        }
    except Exception as exc:
        _log.exception("learning failed")
        self.retry(exc=exc)


@app.task(bind=True, max_retries=1, soft_time_limit=60, time_limit=120)
def run_collect_all(self) -> dict:
    """Массовый сбор сигналов — отправляет каждый тикер в отдельную Celery-задачу.

    Не блокирует worker: запускает group и сразу возвращает task_id группы.
    Результат можно проверить через Celery result backend.
    """
    try:
        from celery import group
        from .universe import RU_BLUE_CHIPS, US_BLUE_CHIPS

        symbols = list(US_BLUE_CHIPS)[:15] + [f"{s}.ME" for s in list(RU_BLUE_CHIPS)[:15]]

        job = group(analyze_ticker.s(sym, fast_mode=True) for sym in symbols)
        result = job.apply_async()

        _log.info("collect_all: queued %d tickers (group_id=%s)", len(symbols), result.id)
        return {"status": "ok", "queued": len(symbols), "group_task_id": result.id}
    except Exception as exc:
        _log.exception("collect_all failed")
        self.retry(exc=exc)


@app.task
def analyze_batch(symbols: list[str], user_id: int | None = None) -> list[dict]:
    """Анализ нескольких тикеров (для dashboard)."""
    results = []
    for sym in symbols:
        try:
            result = analyze_ticker.apply(args=[sym, user_id, True]).get(timeout=60)
            results.append(result)
        except Exception as e:
            results.append({"symbol": sym, "error": str(e)})
    return results


# ── Helpers ──────────────────────────────────────────────────────────────────


def _save_signal_to_db(record: dict, user_id: int | None) -> None:
    """Сохранить сигнал в PostgreSQL."""
    try:
        from .db import get_session, Signal, User as DbUser
        db_user_id = None
        if user_id is not None:
            with get_session(read_only=True) as session:
                user = session.query(DbUser).filter_by(telegram_id=user_id).first()
                if user:
                    db_user_id = user.id
        with get_session() as session:
            sig = Signal(
                user_id=db_user_id,
                symbol=record.get("symbol", ""),
                score=record.get("score", 0),
                score_before_macro=record.get("score_before_macro"),
                confidence=record.get("confidence"),
                signal_tier=record.get("signal_tier"),
                direction=record.get("direction"),
                technical_score=record.get("technical_score"),
                momentum_score=record.get("momentum_score"),
                news_score=record.get("news_score"),
                volume_score=record.get("volume_score"),
                intraday_score=record.get("intraday_score"),
                ref_price=record.get("ref_price"),
                tp_entry=record.get("tp_entry"),
                tp_stop=record.get("tp_stop"),
                tp_target1=record.get("tp_target1"),
                tp_target2=record.get("tp_target2"),
                tp_max_hold_days=record.get("tp_max_hold_days"),
                adx14=record.get("adx14"),
                atr_pct=record.get("atr_pct"),
                macro_dampening=record.get("macro_dampening"),
                regime=record.get("regime"),
                weekly_regime=record.get("weekly_regime"),
            )
            session.add(sig)
    except Exception as e:
        _log.debug("DB save failed: %s", e)


@app.task(bind=True, max_retries=1, default_retry_delay=60, soft_time_limit=900, time_limit=1200)
def scan_all_signals(self):
    """Асинхронное сканирование всех RU и US тикеров с сохранением в кэш."""
    from stock_signal_analyzer.outside_signals import scan_all_regions
    try:
        scan_all_regions([])
    except Exception as exc:
        log.warning("scan_all_signals failed, retrying...", exc_info=True)
        raise self.retry(exc=exc)
