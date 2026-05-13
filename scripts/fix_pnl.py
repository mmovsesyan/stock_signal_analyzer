#!/usr/bin/env python3
"""
Утилита ретроспективного пересчёта pnl_pct и патча signal_tier в outcomes.jsonl.

Что исправляет:
  1. pnl_pct = null/0.0 → пересчёт по entry_price/exit_price/direction
  2. signal_tier = null → ищем в signal_log.jsonl по signal_id, ставим 'C' если не найдено

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


def _load_signal_log(outcomes_path: Path) -> dict[str, dict]:
    """Загрузить signal_log.jsonl в словарь {signal_id: record}."""
    data_dir = outcomes_path.parent
    candidates = [
        data_dir / "signal_log.jsonl",
        data_dir / "signals.jsonl",
    ]
    for p in candidates:
        if p.exists():
            index = {}
            with open(p, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        sid = rec.get("signal_id") or rec.get("id")
                        if sid:
                            index[str(sid)] = rec
                    except json.JSONDecodeError:
                        pass
            print(f"📋 signal_log: загружено {len(index)} записей из {p}")
            return index
    print("⚠️  signal_log.jsonl не найден — tier будет взят из outcomes или 'unknown'")
    return {}


def main():
    parser = argparse.ArgumentParser(description="Пересчёт pnl_pct и патч tier в outcomes.jsonl")
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

    # Загрузить signal_log для патча tier
    signal_index = _load_signal_log(path)

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
    fixed_pnl = 0
    fixed_tier = 0
    fixed_direction = 0
    skipped_no_prices = 0
    skipped_open = 0
    already_ok = 0

    for rec in records:
        outcome = rec.get("outcome", "")
        if outcome == "open":
            skipped_open += 1
            continue

        changed = False

        # ── Патч signal_tier ─────────────────────────────────────────────────
        tier = rec.get("signal_tier")
        if not tier:
            sid = str(rec.get("signal_id", ""))
            sig = signal_index.get(sid, {})
            new_tier = sig.get("signal_tier") or sig.get("tier") or "unknown"
            rec["signal_tier"] = new_tier
            fixed_tier += 1
            changed = True

        # ── Патч direction ───────────────────────────────────────────────────
        direction = rec.get("direction")
        if not direction:
            sid = str(rec.get("signal_id", ""))
            sig = signal_index.get(sid, {})
            tp = sig.get("trade_plan") or {}
            new_dir = tp.get("direction") or sig.get("direction") or sig.get("tp_direction") or "long"
            rec["direction"] = new_dir
            direction = new_dir
            fixed_direction += 1
            changed = True

        # ── Пересчёт pnl_pct ────────────────────────────────────────────────
        entry = rec.get("entry_price")
        exit_ = rec.get("exit_price")
        pnl = rec.get("pnl_pct")

        if pnl is not None and pnl != 0.0:
            if not changed:
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
        fixed_pnl += 1

        print(f"  {rec.get('symbol','?'):10s} {outcome:10s} tier={rec.get('signal_tier','?'):3s} "
              f"entry={float(entry):.4f} exit={float(exit_):.4f} dir={direction or 'long':5s} "
              f"pnl: {str(old_pnl):>8} → {new_pnl:+.2f}%")

    print(f"\n📊 Итого: {total} записей")
    print(f"  ✅ PnL пересчитан:      {fixed_pnl}")
    print(f"  🏷️  Tier пропатчен:     {fixed_tier}")
    print(f"  ➡️  Direction пропатчен: {fixed_direction}")
    print(f"  ✔️  Уже были OK:        {already_ok}")
    print(f"  ⏭️  Open (пропуск):     {skipped_open}")
    print(f"  ❓ Нет цен (пропуск):   {skipped_no_prices}")

    total_changed = fixed_pnl + fixed_tier + fixed_direction
    if total_changed == 0:
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
