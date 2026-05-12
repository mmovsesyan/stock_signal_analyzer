"""
Unified Scheduler — централизованное управление всеми периодическими задачами.

Объединяет все автоматические процессы в одном месте:
- Outcome tracking (каждый час)
- LLM Learning (каждые 6 часов)
- Signal collection (каждые 4 часа)
- DB cleanup (раз в сутки)
- Health monitoring (каждые 5 минут)

Может работать:
1. Через Celery Beat (docker/production)
2. Через APScheduler (standalone, в процессе бота)
3. Через systemd timers (VPS без Docker)

Переменные окружения:
  SCHEDULER_MODE — celery | apscheduler | disabled (по умолчанию apscheduler)
  OUTCOME_INTERVAL_SEC — интервал outcome tracker (3600)
  LEARN_INTERVAL_SEC — интервал обучения (21600)
  COLLECT_INTERVAL_SEC — интервал сбора (14400)
  CLEANUP_INTERVAL_SEC — интервал очистки (86400)
  HEALTH_CHECK_INTERVAL_SEC — интервал health check (300)
"""

from __future__ import annotations

import logging
import os
import time
import threading
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import text

_log = logging.getLogger(__name__)

SCHEDULER_MODE = os.environ.get("SCHEDULER_MODE", "apscheduler")

# Интервалы (секунды)
OUTCOME_INTERVAL = int(os.environ.get("OUTCOME_INTERVAL_SEC", "3600"))
LEARN_INTERVAL = int(os.environ.get("LEARN_INTERVAL_SEC", "21600"))
COLLECT_INTERVAL = int(os.environ.get("COLLECT_INTERVAL_SEC", "14400"))
CLEANUP_INTERVAL = int(os.environ.get("CLEANUP_INTERVAL_SEC", "86400"))
HEALTH_INTERVAL = int(os.environ.get("HEALTH_CHECK_INTERVAL_SEC", "300"))


# ── Health State ─────────────────────────────────────────────────────────────

_health_state: dict[str, Any] = {
    "last_outcome_check": None,
    "last_learning": None,
    "last_collect": None,
    "last_cleanup": None,
    "errors": [],
    "uptime_start": time.time(),
}


def get_health_state() -> dict[str, Any]:
    """Получить состояние здоровья системы."""
    state = dict(_health_state)
    state["uptime_sec"] = time.time() - state.pop("uptime_start", time.time())
    return state


# ── Task Runners ─────────────────────────────────────────────────────────────


def run_outcome_check() -> dict[str, Any]:
    """Проверить исходы открытых сигналов."""
    _log.info("Scheduler: running outcome check...")
    try:
        from .outcome_tracker import OutcomeTracker
        tracker = OutcomeTracker()
        tracker.check_all_outcomes()
        stats = tracker.get_statistics()
        _health_state["last_outcome_check"] = datetime.now(timezone.utc).isoformat()
        _log.info("Scheduler: outcome check done (total=%d)", stats.get("total_signals", 0))
        return stats
    except Exception as e:
        _log.exception("Scheduler: outcome check failed")
        _health_state["errors"].append(f"outcome: {e}")
        _health_state["errors"] = _health_state["errors"][-10:]  # keep last 10
        return {"error": str(e)}


def run_learning_cycle() -> dict[str, Any]:
    """Запустить цикл обучения."""
    _log.info("Scheduler: running learning cycle...")
    try:
        from .llm_learning import run_learning_cycle as _run
        state = _run()
        _health_state["last_learning"] = datetime.now(timezone.utc).isoformat()
        _log.info("Scheduler: learning done (outcomes=%d, wr=%.1f%%)",
                  state.total_outcomes_analyzed, state.win_rate * 100)
        return {
            "outcomes": state.total_outcomes_analyzed,
            "win_rate": state.win_rate,
            "adjustments": state.weight_adjustments,
        }
    except Exception as e:
        _log.exception("Scheduler: learning failed")
        _health_state["errors"].append(f"learning: {e}")
        _health_state["errors"] = _health_state["errors"][-10:]
        return {"error": str(e)}


def run_signal_collection() -> dict[str, Any]:
    """Массовый сбор сигналов."""
    _log.info("Scheduler: running signal collection...")
    try:
        from .universe import RU_BLUE_CHIPS, US_BLUE_CHIPS
        from .engine import build_report
        from .signal_log import build_record_from_report, append_signal_record, log_path_from_env

        symbols = list(US_BLUE_CHIPS)[:15] + [f"{s}.ME" for s in list(RU_BLUE_CHIPS)[:15]]
        collected = 0
        errors = 0

        for sym in symbols:
            try:
                report = build_report(sym, fast_mode=True)
                record = build_record_from_report(report, report.ref_price, "USD")
                append_signal_record(log_path_from_env(), record)
                collected += 1
            except Exception:
                errors += 1

        _health_state["last_collect"] = datetime.now(timezone.utc).isoformat()
        _log.info("Scheduler: collection done (%d collected, %d errors)", collected, errors)
        return {"collected": collected, "errors": errors}
    except Exception as e:
        _log.exception("Scheduler: collection failed")
        _health_state["errors"].append(f"collect: {e}")
        _health_state["errors"] = _health_state["errors"][-10:]
        return {"error": str(e)}


def run_db_cleanup() -> dict[str, Any]:
    """Очистка старых данных в БД."""
    _log.info("Scheduler: running DB cleanup...")
    try:
        from .db import get_session, Signal, NotifyLog
        from datetime import timedelta

        cutoff_signals = datetime.now(timezone.utc) - timedelta(days=90)
        cutoff_notify = datetime.now(timezone.utc) - timedelta(days=7)

        with get_session() as session:
            # Удалить старые notify_log (>7 дней)
            deleted_notify = session.query(NotifyLog).filter(
                NotifyLog.notified_at < cutoff_notify
            ).delete()

            # Не удаляем сигналы — они нужны для обучения
            # Но можно удалить очень старые без outcomes
            # (оставляем на будущее)

        _health_state["last_cleanup"] = datetime.now(timezone.utc).isoformat()
        _log.info("Scheduler: cleanup done (notify_log: %d deleted)", deleted_notify)
        return {"notify_deleted": deleted_notify}
    except Exception as e:
        _log.debug("Scheduler: cleanup skipped (DB not available): %s", e)
        return {"skipped": True}


def run_health_check() -> dict[str, Any]:
    """Проверка здоровья всех компонентов."""
    status: dict[str, Any] = {"timestamp": datetime.now(timezone.utc).isoformat()}

    # Ollama
    try:
        from .llm_sentiment import ollama_available
        status["ollama"] = ollama_available()
    except Exception:
        status["ollama"] = False

    # Polygon
    try:
        from .polygon_data import polygon_available
        status["polygon"] = polygon_available()
    except Exception:
        status["polygon"] = False

    # DB
    try:
        from .db import get_session
        with get_session() as session:
            session.execute(text("SELECT 1"))
        status["database"] = True
    except Exception:
        status["database"] = False

    # Signal log
    from .signal_log import log_path_from_env
    import os as _os
    log_path = log_path_from_env()
    status["signal_log"] = _os.path.exists(log_path) if log_path else False

    _log.debug("Health: %s", status)
    return status


# ── APScheduler integration ──────────────────────────────────────────────────


_scheduler_thread: threading.Thread | None = None
_scheduler_stop = threading.Event()


def start_apscheduler() -> None:
    """Запустить APScheduler в фоновом потоке."""
    global _scheduler_thread

    if SCHEDULER_MODE == "disabled":
        _log.info("Scheduler disabled")
        return

    if SCHEDULER_MODE == "celery":
        _log.info("Scheduler: using Celery Beat (not starting APScheduler)")
        return

    def _run():
        _log.info("APScheduler started")
        intervals = [
            (OUTCOME_INTERVAL, run_outcome_check, "outcome"),
            (LEARN_INTERVAL, run_learning_cycle, "learning"),
            (COLLECT_INTERVAL, run_signal_collection, "collect"),
            (CLEANUP_INTERVAL, run_db_cleanup, "cleanup"),
            (HEALTH_INTERVAL, run_health_check, "health"),
        ]

        last_run: dict[str, float] = {name: 0.0 for _, _, name in intervals}

        while not _scheduler_stop.is_set():
            now = time.time()
            for interval, func, name in intervals:
                if interval <= 0:
                    continue
                if now - last_run[name] >= interval:
                    try:
                        func()
                    except Exception as e:
                        _log.exception("Scheduler task %s failed", name)
                    last_run[name] = now
            _scheduler_stop.wait(timeout=30)  # Check every 30s

        _log.info("APScheduler stopped")

    _scheduler_thread = threading.Thread(target=_run, daemon=True, name="scheduler")
    _scheduler_thread.start()


def stop_apscheduler() -> None:
    """Остановить APScheduler."""
    _scheduler_stop.set()
    if _scheduler_thread:
        _scheduler_thread.join(timeout=5)
