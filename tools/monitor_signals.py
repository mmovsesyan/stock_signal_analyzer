#!/usr/bin/env python3
"""
Signal collection monitoring dashboard.

Shows real-time progress of signal collection:
- Total signals collected
- Breakdown by tier (A, B, C)
- Unique symbols covered
- Collection rate (signals per day)
- Estimated time to reach 50+ signals

Usage:
    python tools/monitor_signals.py
"""

import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import stenv
stenv.load_project_env()


def get_signal_log_path():
    """Get signal log path from environment."""
    return os.environ.get("SSA_SIGNAL_LOG") or os.environ.get("SIGNAL_LOG_JSONL")


def load_signals(log_path):
    """Load all signals from log file."""
    if not os.path.exists(log_path):
        return []

    signals = []
    with open(log_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                signal = json.loads(line)
                signals.append(signal)
            except json.JSONDecodeError:
                continue

    return signals


def parse_timestamp(ts_str):
    """Parse ISO timestamp to datetime."""
    try:
        # Handle both formats: with and without 'Z'
        if ts_str.endswith('Z'):
            ts_str = ts_str[:-1] + '+00:00'
        return datetime.fromisoformat(ts_str)
    except:
        return None


def calculate_statistics(signals):
    """Calculate collection statistics."""
    if not signals:
        return None

    stats = {
        'total': len(signals),
        'by_tier': defaultdict(int),
        'by_direction': defaultdict(int),
        'symbols': set(),
        'dates': [],
    }

    for signal in signals:
        # Count by tier
        tier = signal.get('signal_tier', '?')
        stats['by_tier'][tier] += 1

        # Count by direction
        direction = signal.get('direction', 'neutral')
        stats['by_direction'][direction] += 1

        # Collect symbols
        symbol = signal.get('symbol')
        if symbol:
            stats['symbols'].add(symbol)

        # Collect dates
        ts = signal.get('ts_utc')
        if ts:
            dt = parse_timestamp(ts)
            if dt:
                stats['dates'].append(dt)

    # Calculate date range and rate
    if stats['dates']:
        stats['dates'].sort()
        stats['first_date'] = stats['dates'][0]
        stats['last_date'] = stats['dates'][-1]

        days = (stats['last_date'] - stats['first_date']).total_seconds() / 86400
        if days > 0:
            stats['signals_per_day'] = stats['total'] / days
        else:
            stats['signals_per_day'] = stats['total']  # All in one day
    else:
        stats['first_date'] = None
        stats['last_date'] = None
        stats['signals_per_day'] = 0

    return stats


def print_dashboard(stats, signals, log_path):
    """Print formatted dashboard."""
    print("\n" + "=" * 70)
    print("  📊 SIGNAL COLLECTION DASHBOARD")
    print("=" * 70)

    if not stats:
        print("\n⚠️  No signals found.")
        print(f"\nLog file: {log_path}")
        if not os.path.exists(log_path):
            print("Status: File does not exist yet")
        else:
            print("Status: File exists but is empty")
        print("\nStart collecting signals:")
        print("  - Via Telegram bot: /collect")
        print("  - Via Python: from stock_signal_analyzer.engine import build_report; build_report('AAPL')")
        return

    # File info
    print(f"\n📁 Log File: {log_path}")
    if os.path.exists(log_path):
        size_kb = os.path.getsize(log_path) / 1024
        print(f"   Size: {size_kb:.1f} KB")

    # Total signals
    print(f"\n📈 Total Signals: {stats['total']}")

    # Progress to 50 signals
    target = 50
    if stats['total'] >= target:
        print(f"   ✅ Target reached! ({stats['total']}/{target})")
        print(f"   Ready for backtesting: python tools/backtest.py {log_path} --min-tier A")
    else:
        remaining = target - stats['total']
        progress = stats['total'] / target * 100
        bar_length = 30
        filled = int(bar_length * progress / 100)
        bar = '█' * filled + '░' * (bar_length - filled)
        print(f"   Progress: [{bar}] {progress:.1f}%")
        print(f"   Remaining: {remaining} signals to reach {target}")

    # By tier
    print(f"\n🏆 By Tier:")
    for tier in ['A', 'B', 'C']:
        count = stats['by_tier'].get(tier, 0)
        pct = count / stats['total'] * 100 if stats['total'] > 0 else 0
        print(f"   {tier}: {count:3d} ({pct:5.1f}%)")

    # By direction
    print(f"\n📊 By Direction:")
    for direction in ['long', 'short', 'neutral']:
        count = stats['by_direction'].get(direction, 0)
        pct = count / stats['total'] * 100 if stats['total'] > 0 else 0
        print(f"   {direction.capitalize():8s}: {count:3d} ({pct:5.1f}%)")

    # Symbols
    print(f"\n🎯 Unique Symbols: {len(stats['symbols'])}")
    if len(stats['symbols']) <= 20:
        symbols_list = ', '.join(sorted(stats['symbols']))
        print(f"   {symbols_list}")
    else:
        symbols_preview = ', '.join(sorted(list(stats['symbols']))[:20])
        print(f"   {symbols_preview}, ... (+{len(stats['symbols']) - 20} more)")

    # Date range and rate
    if stats['first_date'] and stats['last_date']:
        print(f"\n📅 Collection Period:")
        print(f"   First: {stats['first_date'].strftime('%Y-%m-%d %H:%M UTC')}")
        print(f"   Last:  {stats['last_date'].strftime('%Y-%m-%d %H:%M UTC')}")

        days = (stats['last_date'] - stats['first_date']).total_seconds() / 86400
        print(f"   Duration: {days:.1f} days")

        if stats['signals_per_day'] > 0:
            print(f"\n⚡ Collection Rate: {stats['signals_per_day']:.1f} signals/day")

            # Estimate time to reach 50
            if stats['total'] < target:
                remaining = target - stats['total']
                days_needed = remaining / stats['signals_per_day']
                print(f"   Estimated time to {target} signals: {days_needed:.1f} days")

    # Recent signals
    print(f"\n🕐 Recent Signals (last 5):")
    recent = signals[-5:]
    for signal in reversed(recent):
        symbol = signal.get('symbol', '?')
        tier = signal.get('signal_tier', '?')
        score = signal.get('score', 0)
        direction = signal.get('direction', 'neutral')
        ts = signal.get('ts_utc', '')

        dt = parse_timestamp(ts)
        time_str = dt.strftime('%m-%d %H:%M') if dt else ts[:16]

        direction_icon = '📈' if direction == 'long' else ('📉' if direction == 'short' else '➡️')
        print(f"   {time_str} | {symbol:8s} | {tier} | {direction_icon} {score:+.3f}")

    print("\n" + "=" * 70)


def main():
    log_path = get_signal_log_path()

    if not log_path:
        print("\n⚠️  SSA_SIGNAL_LOG environment variable not set.")
        print("\nSet it to enable signal logging:")
        print('  export SSA_SIGNAL_LOG="/var/lib/stock_signal_analyzer/signals.jsonl"')
        print("\nOr use SIGNAL_LOG_JSONL as an alternative.")
        return 1

    signals = load_signals(log_path)
    stats = calculate_statistics(signals)
    print_dashboard(stats, signals, log_path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
