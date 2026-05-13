#!/usr/bin/env python3
"""
Утилита ретроспективного пересчёта pnl_pct в outcomes.jsonl.

Для записей где entry_price и exit_price известны, но pnl_pct = null или 0.0
(были записаны до фикса outcome_tracker.py).

Использование:
    python3 scripts/fix_pnl.py [--dry-run] [--file /path/to/outcomes.jsonl]
"""
import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from datetime import datetime, timezone

# Дефолтные константы из outcome_tracker.py
_SLIPPAGE_PCT = 0.001    # 0.1%
_COMMISSION_PCT = 0.0015  # 0.15%


def calc_pnl(entry: float, exit_: float, direction: str,
             slippage: float = _SLIPPAGE_PCT,
             commission: float = _COMMISSION_PCT) -> float:
    if entry <= 0 or exit_ <= 0:
        return 0.0
    if direction == "long":
        gross_pct = (exit_ - entry) / entry * 100
    else:
        gross_pct = (entry - exit_) / entry * 100
    cost_pct = (slippage * 2 + commission * 2) * 100
    return gross_pct - cost_pct


def main():
    parser = argparse.ArgumentParser(description="Пересчёт pnl_pct в outcomes.jsonl")
    parser.add_argument("--dry-run", action="store_true", help="Только показать, не писать")
    parser.add_argument("--file", default=None, help="Путь к outcomes.jsonl")
    args = parser.parse_args()

    # Поиск файла
    if args.file:
        path = Path(args.file)
    else:
        candidates = [
            Path("/var/lib/stock_signal_analyzer/outcomes.jsonl"),
            Path(os.environ.get("SSA_DATA_DIR", "")) / "outcomes.jsonl",
            Path("data/outcomes.jsonl"),
        ]
        path = next((p for p in candidates if p.exists()), None)
        if path is None:
            print("❌ outcomes.jsonl не найден. Укажите --file /path/to/outcomes.jsonl")
            sys.exit(1)

    print(f"📂 Файл: {path}")
    print(f"{'🔍 DRY RUN — изменения НЕ сохранятся' if args.dry_run else '✏️  Режим записи'}\n")

    records = []
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"⚠️  Строка {i}: JSON ошибка — {e}")

    total = len(records)
    fixed = 0
    skipped_no_prices = 0
    skipped_open = 0
    already_ok = 0

    for rec in records:
        outcome = rec.get("outcome", "")
        if outcome == "open":
            skipped_open += 1
            continue

        entry = rec.get("entry_price")
        exit_ = rec.get("exit_price")
        direction = rec.get("direction", "long")
        pnl = rec.get("pnl_pct")

        # Пропускаем если уже есть нормальный pnl
        if pnl is not None and pnl != 0.0:
            already_ok += 1
            continue

        if not entry or not exit_:
            skipped_no_prices += 1
            continue

        try:
            new_pnl = calc_pnl(float(entry), float(exit_), direction or "long")
        except (TypeError, ValueError) as e:
            print(f"⚠️  {rec.get('signal_id', '?')}: ошибка расчёта — {e}")
            skipped_no_prices += 1
            continue

        old_pnl = rec.get("pnl_pct")
        rec["pnl_pct"] = new_pnl
        rec["outcome_pnl"] = new_pnl
        rec["pnl_recalculated_at"] = datetime.now(timezone.utc).isoformat()
        fixed += 1

        print(f"  {rec.get('symbol','?'):10s} {outcome:10s} "
              f"entry={entry:.4f} exit={exit_:.4f} dir={direction or 'long':5s} "
              f"pnl: {str(old_pnl):>8} → {new_pnl:+.2f}%")

    print(f"\n📊 Итого: {total} записей")
    print(f"  ✅ Пересчитано:    {fixed}")
    print(f"  ✔️  Уже были OK:   {already_ok}")
    print(f"  ⏭️  Open (пропуск): {skipped_open}")
    print(f"  ❓ Нет цен:        {skipped_no_prices}")

    if fixed == 0:
        print("\nНечего исправлять." if not args.dry_run else "\nDRY RUN: нечего исправлять.")
        return

    if args.dry_run:
        print("\nDRY RUN: файл не изменён.")
        return

    # Атомарная запись через tmp
    tmp = path.with_suffix(".tmp")
    backup = path.with_suffix(".bak")

    with open(tmp, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    shutil.copy2(path, backup)
    tmp.replace(path)
    print(f"\n✅ Записано. Бэкап: {backup}")


if __name__ == "__main__":
    main()
