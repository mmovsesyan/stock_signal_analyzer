#!/usr/bin/env python3
"""Add last_notify_detail column to users table if missing."""
from __future__ import annotations

import os
import sys

from sqlalchemy import create_engine, text


def migrate() -> None:
    url = os.environ.get("DATABASE_URL", "sqlite:///data/stock_signals.db")
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
