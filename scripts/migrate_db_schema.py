#!/usr/bin/env python3
"""Добавить недостающие колонки в таблицу users (schema migration).

Использование:
    python scripts/migrate_db_schema.py
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from sqlalchemy import text
from stock_signal_analyzer.db import _engine, db_available

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
_log = logging.getLogger(__name__)


COLUMNS = [
    ("signal_filter_type", "VARCHAR(20) DEFAULT 'balanced'"),
    ("language", "VARCHAR(10) DEFAULT 'ru'"),
    ("auto_collect", "BOOLEAN DEFAULT FALSE"),
    ("receive_learning_report", "BOOLEAN DEFAULT FALSE"),
    ("notify_drawdown", "BOOLEAN DEFAULT TRUE"),
    ("daily_digest", "BOOLEAN DEFAULT FALSE"),
    ("max_watchlist_size", "INTEGER DEFAULT 30"),
    ("autocollect_tickers", "JSON DEFAULT '[]'"),
]


def migrate() -> None:
    if not db_available():
        _log.error("БД недоступна")
        sys.exit(1)

    with _engine.connect() as conn:
        for col_name, col_def in COLUMNS:
            try:
                conn.execute(text(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col_name} {col_def}"))
                _log.info("Колонка %s добавлена (или уже существует)", col_name)
            except Exception as e:
                _log.warning("Ошибка добавления %s: %s", col_name, e)
        conn.commit()

    _log.info("Миграция схемы завершена")


if __name__ == "__main__":
    migrate()
