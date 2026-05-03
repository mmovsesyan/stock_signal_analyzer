#!/usr/bin/env python3
"""
Verification script for monetization infrastructure.

Tests all components end-to-end:
- Signal generation and logging
- Signal filter
- Outcome tracker
- Backtester

Usage:
    python tools/verify_monetization.py
"""

import os
import sys
import tempfile
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import stenv
stenv.load_project_env()


def print_header(text: str):
    print(f"\n{'=' * 60}")
    print(f"  {text}")
    print('=' * 60)


def print_check(name: str, passed: bool, details: str = ""):
    status = "✓" if passed else "✗"
    color = "\033[92m" if passed else "\033[91m"
    reset = "\033[0m"
    print(f"{color}{status}{reset} {name}")
    if details:
        print(f"  {details}")


def check_imports():
    """Check that all monetization modules can be imported."""
    print_header("1. Checking Imports")

    checks = []

    try:
        from stock_signal_analyzer.signal_filter import (
            get_balanced_filter,
            get_conservative_filter,
            get_aggressive_filter,
            should_trade_signal,
        )
        checks.append(("Signal Filter", True, "All filter presets available"))
    except Exception as e:
        checks.append(("Signal Filter", False, str(e)))

    try:
        from stock_signal_analyzer.outcome_tracker import OutcomeTracker
        checks.append(("Outcome Tracker", True, "Module loaded"))
    except Exception as e:
        checks.append(("Outcome Tracker", False, str(e)))

    try:
        from stock_signal_analyzer.engine import build_report
        checks.append(("Signal Engine", True, "build_report available"))
    except Exception as e:
        checks.append(("Signal Engine", False, str(e)))

    try:
        from stock_signal_analyzer.signal_log import (
            log_path_from_env,
            append_signal_record,
        )
        checks.append(("Signal Logging", True, "Logging functions available"))
    except Exception as e:
        checks.append(("Signal Logging", False, str(e)))

    for name, passed, details in checks:
        print_check(name, passed, details)

    return all(passed for _, passed, _ in checks)


def check_environment():
    """Check environment variables."""
    print_header("2. Checking Environment Variables")

    checks = []

    ssa_log = os.environ.get("SSA_SIGNAL_LOG") or os.environ.get("SIGNAL_LOG_JSONL")
    if ssa_log:
        checks.append(("SSA_SIGNAL_LOG", True, f"Set to: {ssa_log}"))
    else:
        checks.append(("SSA_SIGNAL_LOG", False, "Not set (signals won't be logged)"))

    stock_data = os.environ.get("STOCK_SIGNAL_DATA")
    if stock_data:
        checks.append(("STOCK_SIGNAL_DATA", True, f"Set to: {stock_data}"))
    else:
        checks.append(("STOCK_SIGNAL_DATA", False, "Not set (outcome tracker will use default)"))

    collect_interval = os.environ.get("COLLECT_INTERVAL_SEC")
    if collect_interval:
        checks.append(("COLLECT_INTERVAL_SEC", True, f"Auto-collection every {collect_interval}s"))
    else:
        checks.append(("COLLECT_INTERVAL_SEC", False, "Auto-collection disabled"))

    for name, passed, details in checks:
        print_check(name, passed, details)

    return any(passed for _, passed, _ in checks if "SSA_SIGNAL_LOG" in _)


def test_signal_generation():
    """Test signal generation and logging."""
    print_header("3. Testing Signal Generation")

    from stock_signal_analyzer.engine import build_report
    from stock_signal_analyzer.signal_log import log_path_from_env

    # Use a temporary file for testing
    with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
        test_log = f.name

    original_log = os.environ.get("SSA_SIGNAL_LOG")
    os.environ["SSA_SIGNAL_LOG"] = test_log

    try:
        print("  Generating test signal for AAPL...")
        report = build_report("AAPL", fast_mode=True)

        print_check("Signal Generation", True, f"Score: {report.score:.3f}, Tier: {report.signal_tier}")

        # Check if signal was logged
        if os.path.exists(test_log):
            with open(test_log, 'r') as f:
                lines = f.readlines()

            if lines:
                import json
                signal = json.loads(lines[0])
                print_check("Signal Logging", True, f"Logged to {test_log}")
                print(f"    Symbol: {signal.get('symbol')}")
                print(f"    Direction: {signal.get('direction')}")
                print(f"    Entry: ${signal.get('entry_price', 0):.2f}")
                return True, test_log, signal
            else:
                print_check("Signal Logging", False, "File created but empty")
                return False, test_log, None
        else:
            print_check("Signal Logging", False, "Log file not created")
            return False, test_log, None

    except Exception as e:
        print_check("Signal Generation", False, str(e))
        return False, test_log, None

    finally:
        if original_log:
            os.environ["SSA_SIGNAL_LOG"] = original_log
        else:
            os.environ.pop("SSA_SIGNAL_LOG", None)


def test_signal_filter(report):
    """Test signal filter on generated signal."""
    print_header("4. Testing Signal Filter")

    from stock_signal_analyzer.signal_filter import (
        filter_signal_with_reason,
        get_conservative_filter,
        get_balanced_filter,
        get_aggressive_filter,
    )

    try:
        # Test all three filter presets
        filters = [
            ("Conservative", get_conservative_filter()),
            ("Balanced", get_balanced_filter()),
            ("Aggressive", get_aggressive_filter()),
        ]

        for name, filter_obj in filters:
            result = filter_obj.filter(report)
            status = "PASS" if result.should_trade else "REJECT"
            print_check(
                f"{name} Filter",
                True,
                f"{status} - Score: {result.score:.0f}/100 - {result.reason}"
            )

        return True

    except Exception as e:
        print_check("Signal Filter", False, str(e))
        return False


def test_outcome_tracker(test_log):
    """Test outcome tracker can read signals."""
    print_header("5. Testing Outcome Tracker")

    from stock_signal_analyzer.outcome_tracker import OutcomeTracker

    try:
        # Create temporary outcomes file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
            outcomes_file = f.name

        tracker = OutcomeTracker(
            signals_log_path=test_log,
            outcomes_path=outcomes_file
        )

        print_check("Outcome Tracker Init", True, "Initialized successfully")

        # Try to load signals
        signals = tracker._load_open_signals()
        print_check("Load Signals", True, f"Found {len(signals)} open signal(s)")

        # Get statistics (should be empty for new tracker)
        stats = tracker.get_statistics()
        print_check("Statistics", True, "Can generate statistics")

        # Cleanup
        os.unlink(outcomes_file)

        return True

    except Exception as e:
        print_check("Outcome Tracker", False, str(e))
        return False


def test_backtester(test_log):
    """Test backtester can process signals."""
    print_header("6. Testing Backtester")

    try:
        # Check if backtest.py exists and can be imported
        backtest_path = Path(__file__).parent / "backtest.py"
        if not backtest_path.exists():
            print_check("Backtester", False, "backtest.py not found")
            return False

        print_check("Backtester File", True, "backtest.py exists")

        # Try to read and parse the test log
        import json
        signals = []
        with open(test_log, 'r') as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        signal = json.loads(line)
                        signals.append(signal)
                    except json.JSONDecodeError:
                        pass

        print_check("Signal Format", True, f"Can parse {len(signals)} signal(s)")

        # Verify signal has required fields for backtesting
        if signals:
            signal = signals[0]
            # Check for trade plan fields (prefixed with tp_)
            required_fields = ['symbol', 'direction', 'tp_entry', 'tp_stop', 'tp_target1']
            has_all = all(field in signal for field in required_fields)

            if has_all:
                print_check("Backtest Ready", True, "Signal format compatible with backtester")
            else:
                missing = [f for f in required_fields if f not in signal]
                print_check("Backtest Ready", False, f"Missing fields: {missing}")
        else:
            print_check("Backtest Ready", True, "Backtester ready (no signals to validate)")

        return True

    except Exception as e:
        print_check("Backtester", False, str(e))
        return False


def print_summary(all_passed: bool):
    """Print final summary."""
    print_header("Summary")

    if all_passed:
        print("\n✅ All checks passed! Monetization infrastructure is ready.")
        print("\nNext steps:")
        print("1. Set SSA_SIGNAL_LOG environment variable (if not set)")
        print("2. Start collecting signals:")
        print("   - Via Telegram bot: /collect")
        print("   - Via command line: python -c 'from stock_signal_analyzer.engine import build_report; build_report(\"AAPL\")'")
        print("3. Monitor progress: python tools/monitor_signals.py")
        print("4. After 50+ signals: python tools/backtest.py signals.jsonl --min-tier A")
        print("\nSee QUICK_START_MONETIZATION.md for detailed instructions.")
    else:
        print("\n⚠️  Some checks failed. Review the errors above and fix them.")
        print("\nCommon issues:")
        print("- Missing environment variables: Set SSA_SIGNAL_LOG")
        print("- Import errors: Run 'pip install -r requirements.txt'")
        print("- API errors: Check your API keys (FINNHUB_API_KEY, etc.)")


def main():
    print("\n" + "=" * 60)
    print("  MONETIZATION INFRASTRUCTURE VERIFICATION")
    print("=" * 60)
    print("\nThis script verifies that all monetization components work together.")

    results = []

    # 1. Check imports
    results.append(check_imports())

    # 2. Check environment
    results.append(check_environment())

    # 3. Test signal generation
    signal_ok, test_log, signal_data = test_signal_generation()
    results.append(signal_ok)

    if signal_ok and signal_data:
        # 4. Test signal filter
        from stock_signal_analyzer.engine import build_report
        report = build_report("AAPL", fast_mode=True)
        results.append(test_signal_filter(report))

        # 5. Test outcome tracker
        results.append(test_outcome_tracker(test_log))

        # 6. Test backtester
        results.append(test_backtester(test_log))

        # Cleanup test log
        try:
            os.unlink(test_log)
        except Exception:
            pass

    # Print summary
    all_passed = all(results)
    print_summary(all_passed)

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
