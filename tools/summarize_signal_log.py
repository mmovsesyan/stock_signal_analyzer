#!/usr/bin/env python3
"""Сводка по JSONL из SSA_SIGNAL_LOG: число записей по классу A/B/C."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter


def main() -> int:
    p = argparse.ArgumentParser(description="Сводка signal log (JSONL).")
    p.add_argument("path", help="Файл .jsonl")
    args = p.parse_args()
    tiers: Counter[str] = Counter()
    n = 0
    try:
        with open(args.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                tiers[str(row.get("signal_tier", "?"))] += 1
                n += 1
    except OSError as e:
        print(e, file=sys.stderr)
        return 1
    print(f"Записей: {n}")
    for k in sorted(tiers.keys()):
        print(f"  {k}: {tiers[k]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
