"""
Fix old signals: add trade plans for signals with tp_direction="none".

Usage:
    python3 scripts/fix_old_signals.py

This updates /var/lib/stock_signal_analyzer/signals.jsonl in-place,
adding inferred trade plans for old C-class signals so OutcomeTracker
can evaluate them and ML learning can start.
"""
from __future__ import annotations

import json
import os
import sys


def fix_signal(signal: dict) -> dict:
    """Add trade plan for signals missing tp_direction or with 'none'."""
    score = signal.get("score", 0)
    if score > 0.05:
        direction = "long"
    elif score < -0.05:
        direction = "short"
    else:
        return signal  # keep as-is for truly neutral

    ref_price = signal.get("ref_price", 100)
    if not ref_price or ref_price <= 0:
        ref_price = 100.0

    atr_pct = signal.get("atr_pct", 2.0) or 2.0
    atr_abs = ref_price * atr_pct / 100.0
    sign = 1.0 if direction == "long" else -1.0

    # Simplified plan: 1.5xATR stop, 2.5xATR target1, 4.0xATR target2
    stop_price = round(ref_price - sign * (1.5 * atr_abs), 4)
    target1 = round(ref_price + sign * (2.5 * atr_abs), 4)
    target2 = round(ref_price + sign * (4.0 * atr_abs), 4)

    signal["tp_direction"] = direction
    signal["tp_entry"] = round(ref_price, 4)
    signal["tp_stop"] = stop_price
    signal["tp_target1"] = target1
    signal["tp_target2"] = target2
    signal["tp_max_hold_days"] = 5
    signal["tp_position_size_pct"] = 10
    signal["tp_partial_exit_pct"] = 50

    # If trade_plan was present as nested dict, update it too
    tp = signal.get("trade_plan")
    if isinstance(tp, dict):
        tp["direction"] = direction
        tp["entry_price"] = round(ref_price, 4)
        tp["stop_price"] = stop_price
        tp["target1_price"] = target1
        tp["target2_price"] = target2
        tp["max_hold_days"] = 5

    return signal


def main() -> int:
    base = os.environ.get("STOCK_SIGNAL_DATA", "/var/lib/stock_signal_analyzer")
    signals_path = os.path.join(base, "signals.jsonl")

    if not os.path.exists(signals_path):
        print(f"Signals file not found: {signals_path}")
        return 1

    with open(signals_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    fixed_count = 0
    total = 0
    fixed_lines: list[str] = []

    for line in lines:
        line = line.strip()
        if not line:
            fixed_lines.append("")
            continue
        try:
            sig = json.loads(line)
        except json.JSONDecodeError:
            fixed_lines.append(line)
            continue

        total += 1
        tp_dir = sig.get("tp_direction")
        if tp_dir in ("none", None, ""):
            sig = fix_signal(sig)
            fixed_count += 1

        fixed_lines.append(json.dumps(sig, ensure_ascii=False))

    # Atomic-ish write: write to temp then rename
    tmp_path = signals_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write("\n".join(fixed_lines) + "\n")
    os.replace(tmp_path, signals_path)

    print(f"Processed {total} signals, fixed {fixed_count} with tp_direction=none")
    return 0


if __name__ == "__main__":
    sys.exit(main())
