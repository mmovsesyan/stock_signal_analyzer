#!/usr/bin/env python3
"""Add last_notify_detail column to users table if missing."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, text


def migrate() -> None:
    data_dir = os.environ.get("STOCK_SIGNAL_DATA", "/var/lib/stock_signal_analyzer")
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    db_path = os.path.join(data_dir, "stock_signals.db")
    url = os.environ.get("DATABASE_URL", f"sqlite:///{db_path}")
    engine = create_engine(url)

    with engine.connect() as conn:
        if url.startswith("sqlite"):
            result = conn.execute(text("PRAGMA table_info(users)"))
            columns = [row[1] for row in result]
            if "last_notify_detail" not in columns:
                conn.execute(text("ALTER TABLE users ADD COLUMN last_notify_detail TEXT DEFAULT '{}'"))
                conn.commit()
                print("Added last_notify_detail column (SQLite)")
            else:
                print("last_notify_detail already exists (SQLite)")
        else:
            conn.execute(
                text(
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_notify_detail JSONB DEFAULT '{}'"
                )
            )
            conn.commit()
            print("Added last_notify_detail column (PostgreSQL)")


if __name__ == "__main__":
    try:
        migrate()
    except Exception as exc:
        print(f"Migration failed: {exc}", file=sys.stderr)
        sys.exit(1)
