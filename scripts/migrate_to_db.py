#!/usr/bin/env python3
"""
Миграция данных из JSON файлов в PostgreSQL.

Переносит:
- telegram_users.json → таблица users + watchlist
- signals.jsonl → таблица signals
- outcomes.jsonl → таблица outcomes

Использование:
  python scripts/migrate_to_db.py

Требует: DATABASE_URL в .env или окружении.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import stenv
stenv.load_project_env()

from stock_signal_analyzer.db import (
    get_session, init_db,
    User, Watchlist, Signal, Outcome, NotifyLog,
)


def migrate_users() -> int:
    """Перенести пользователей из telegram_users.json."""
    data_dir = os.environ.get("STOCK_SIGNAL_DATA", "data")
    users_file = Path(data_dir) / "telegram_users.json"

    if not users_file.exists():
        # Попробовать в корне проекта
        users_file = Path("data") / "telegram_users.json"
    if not users_file.exists():
        print("  telegram_users.json не найден, пропускаю")
        return 0

    with open(users_file, encoding="utf-8") as f:
        raw = json.load(f)

    users_data = raw.get("users", {})
    count = 0

    with get_session() as session:
        for uid_str, prefs in users_data.items():
            try:
                telegram_id = int(uid_str)
            except ValueError:
                continue

            # Проверить, не существует ли уже
            existing = session.query(User).filter_by(telegram_id=telegram_id).first()
            if existing:
                continue

            user = User(
                telegram_id=telegram_id,
                notify_strong_outside=prefs.get("notify_strong_outside", True),
                strong_threshold=prefs.get("strong_threshold", 0.35),
                notify_cooldown_sec=prefs.get("notify_cooldown_sec", 86400),
                use_default_tickers=prefs.get("use_default_tickers", True),
            )
            session.add(user)
            session.flush()  # Получить user.id

            # Watchlist
            for sym in prefs.get("watchlist", []):
                wl = Watchlist(user_id=user.id, symbol=sym.strip().upper())
                session.add(wl)

            count += 1

    return count


def migrate_signals() -> int:
    """Перенести сигналы из signals.jsonl."""
    log_path = os.environ.get("SSA_SIGNAL_LOG")
    if not log_path:
        log_path = "data/signals.jsonl"
    if not Path(log_path).exists():
        print(f"  {log_path} не найден, пропускаю")
        return 0

    count = 0
    with get_session() as session:
        with open(log_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue

                sig = Signal(
                    symbol=r.get("symbol", ""),
                    score=r.get("score", 0),
                    score_before_macro=r.get("score_before_macro"),
                    confidence=r.get("confidence"),
                    signal_tier=r.get("signal_tier"),
                    direction=r.get("direction"),
                    technical_score=r.get("technical_score"),
                    momentum_score=r.get("momentum_score"),
                    news_score=r.get("news_score"),
                    volume_score=r.get("volume_score"),
                    intraday_score=r.get("intraday_score"),
                    ref_price=r.get("ref_price"),
                    tp_entry=r.get("tp_entry"),
                    tp_stop=r.get("tp_stop"),
                    tp_target1=r.get("tp_target1"),
                    tp_target2=r.get("tp_target2"),
                    tp_max_hold_days=r.get("tp_max_hold_days"),
                    adx14=r.get("adx14"),
                    atr_pct=r.get("atr_pct"),
                    macro_dampening=r.get("macro_dampening"),
                    regime=r.get("regime"),
                    weekly_regime=r.get("weekly_regime"),
                )
                # Parse timestamp
                ts = r.get("ts_utc")
                if ts:
                    try:
                        sig.created_at = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    except (ValueError, TypeError):
                        pass

                session.add(sig)
                count += 1

    return count


def migrate_outcomes() -> int:
    """Перенести outcomes из outcomes.jsonl."""
    data_dir = os.environ.get("STOCK_SIGNAL_DATA", "data")
    outcomes_file = Path(data_dir) / "outcomes.jsonl"
    if not outcomes_file.exists():
        print(f"  {outcomes_file} не найден, пропускаю")
        return 0

    count = 0
    with get_session() as session:
        with open(outcomes_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if r.get("outcome") == "open":
                    continue

                # Найти соответствующий сигнал по symbol + entry_date
                symbol = r.get("symbol", "")
                entry_date = r.get("entry_date", "")
                if not symbol or not entry_date:
                    continue

                sig = session.query(Signal).filter(
                    Signal.symbol == symbol,
                ).order_by(Signal.created_at.desc()).first()

                if not sig:
                    continue

                # Проверить, нет ли уже outcome
                existing = session.query(Outcome).filter_by(signal_id=sig.id).first()
                if existing:
                    continue

                outcome = Outcome(
                    signal_id=sig.id,
                    outcome=r.get("outcome", ""),
                    exit_price=r.get("exit_price"),
                    pnl_pct=r.get("pnl_pct"),
                    hold_days=r.get("hold_days"),
                )
                exit_date = r.get("exit_date")
                if exit_date:
                    try:
                        outcome.exit_date = datetime.fromisoformat(exit_date.replace("Z", "+00:00"))
                    except (ValueError, TypeError):
                        pass

                session.add(outcome)
                count += 1

    return count


def main():
    print("=== Миграция данных в PostgreSQL ===")
    print(f"  DATABASE_URL: {os.environ.get('DATABASE_URL', 'не задан')}")
    print()

    print("Создаю таблицы...")
    init_db()
    print("  ✓ Таблицы созданы")
    print()

    print("Миграция пользователей...")
    n = migrate_users()
    print(f"  ✓ Перенесено: {n} пользователей")

    print("Миграция сигналов...")
    n = migrate_signals()
    print(f"  ✓ Перенесено: {n} сигналов")

    print("Миграция outcomes...")
    n = migrate_outcomes()
    print(f"  ✓ Перенесено: {n} outcomes")

    print()
    print("=== Миграция завершена ===")


if __name__ == "__main__":
    main()
