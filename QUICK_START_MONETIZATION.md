# 🚀 Quick Start: Monetization

**Goal:** Start collecting trading signals, prove profitability, and launch a subscription service.

**Timeline:** 2-3 months to first paying customers.

---

## 📋 Prerequisites

### 1. Environment Variables

Set these in your shell profile (`~/.bashrc`, `~/.zshrc`, or `~/.bash_profile`):

```bash
# Required: Where to store signal logs
export SSA_SIGNAL_LOG="/var/lib/stock_signal_analyzer/signals.jsonl"

# Required: Base directory for data
export STOCK_SIGNAL_DATA="/var/lib/stock_signal_analyzer"

# Optional: Auto-collect signals every 4 hours (14400 seconds)
export COLLECT_INTERVAL_SEC=14400

# Optional: Telegram bot token (for notifications)
export TELEGRAM_BOT_TOKEN="your_bot_token_here"

# Optional: API keys (if not already set)
export FINNHUB_API_KEY="your_finnhub_key"
```

**Create the data directory:**
```bash
sudo mkdir -p /var/lib/stock_signal_analyzer
sudo chown $USER /var/lib/stock_signal_analyzer
```

### 2. Verify Installation

```bash
cd /Users/mhermovsisyan/Documents/GitHub/stock_signal_analyzer
source venv/bin/activate
python tools/verify_monetization.py
```

**Expected output:** All checks should pass (✓).

If any checks fail, review the error messages and fix them before proceeding.

---

## 🎯 Phase 1: Data Collection (1-2 weeks)

You need **50-100 signals** minimum to run a meaningful backtest. There are two collection methods:

### Method 1: Telegram Bot Auto-Collection (Recommended)

**Advantages:**
- Fully automated
- Runs 24/7
- Collects signals every 4 hours
- Covers 30+ blue chip stocks automatically

**Setup:**

1. Start the Telegram bot:
```bash
cd /Users/mhermovsisyan/Documents/GitHub/stock_signal_analyzer
source venv/bin/activate
python telegram_bot.py
```

2. The bot will auto-collect signals every 4 hours (if `COLLECT_INTERVAL_SEC=14400` is set).

3. Monitor progress via Telegram:
   - `/status` - Check how many signals collected
   - `/export` - Download signals.jsonl file

**Expected rate:** ~6 signals/day = 50+ signals in ~8-10 days.

### Method 2: Manual Collection

**Advantages:**
- Full control over which symbols to analyze
- Can collect many signals quickly
- Good for testing

**Commands:**

```bash
# Collect signals for specific tickers
python -c "
from stock_signal_analyzer.engine import build_report
for symbol in ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA']:
    build_report(symbol)
    print(f'✓ {symbol}')
"

# Or via Telegram bot
# /collect AAPL MSFT GOOGL AMZN TSLA
```

**Tip:** Run this daily with different symbols to accumulate signals faster.

---

## 📊 Phase 2: Monitor Progress

Check your progress daily:

```bash
python tools/monitor_signals.py
```

**Example output:**
```
📊 SIGNAL COLLECTION DASHBOARD
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📁 Log File: /var/lib/stock_signal_analyzer/signals.jsonl
   Size: 45.2 KB

📈 Total Signals: 73
   Progress: [████████████████████████████░░] 146.0%
   ✅ Target reached! (73/50)

🏆 By Tier:
   A:  28 ( 38.4%)
   B:  32 ( 43.8%)
   C:  13 ( 17.8%)

📊 By Direction:
   Long   :  45 ( 61.6%)
   Short  :  20 ( 27.4%)
   Neutral:   8 ( 11.0%)

🎯 Unique Symbols: 24

📅 Collection Period:
   First: 2026-04-15 10:23 UTC
   Last:  2026-05-01 18:45 UTC
   Duration: 16.3 days

⚡ Collection Rate: 4.5 signals/day
```

**When you see "Target reached!"** → Proceed to Phase 3.

---

## 🧪 Phase 3: First Backtest (Day 14+)

Once you have 50+ signals, run your first backtest:

```bash
# Backtest all signals
python tools/backtest.py $SSA_SIGNAL_LOG

# Backtest only Class A signals (recommended)
python tools/backtest.py $SSA_SIGNAL_LOG --min-tier A

# Backtest with target2 (more aggressive)
python tools/backtest.py $SSA_SIGNAL_LOG --min-tier A --target 2
```

### Understanding the Results

**Example output:**
```
========================================================
  РЕЗУЛЬТАТЫ БЭКТЕСТА (73 сделок)
========================================================
  Long: 45  |  Short: 28
  Win rate:       64.4%
  Средний PnL:    +1.45%
  Средний выигрыш: +3.21%  |  Средний убыток: -1.67%
  Win/Loss ratio: 1.92
  Expectancy:     +1.450% на сделку
  Profit Factor:  2.34
  Sharpe ratio:   1.89
  Max Drawdown:   -7.23%
  Суммарный PnL:  +105.85%
```

**Key Metrics:**

| Metric | Target | What It Means |
|--------|--------|---------------|
| **Win Rate** | >60% | Percentage of profitable trades |
| **Profit Factor** | >2.0 | Total profit ÷ total loss |
| **Sharpe Ratio** | >1.5 | Risk-adjusted returns |
| **Max Drawdown** | <15% | Largest peak-to-trough decline |

**Decision Matrix:**

- ✅ **Win rate >60% AND Profit factor >2.0** → Proceed to Phase 4 (Paper Trading)
- ⚠️ **Win rate 55-60% OR Profit factor 1.5-2.0** → Optimize filters, collect more data
- ❌ **Win rate <55% OR Profit factor <1.5** → Review strategy, adjust parameters

---

## 🎯 Phase 4: Optimize Filters (Optional)

If your backtest results are marginal (55-60% win rate), try filtering for only the best signals:

```bash
# Test conservative filter (highest quality)
python tools/backtest.py $SSA_SIGNAL_LOG --min-tier A --min-confidence 0.75

# Test different ADX thresholds (trend strength)
python tools/backtest.py $SSA_SIGNAL_LOG --min-tier A --min-adx 25
```

**Filter Presets:**

| Preset | Win Rate | Signals/Month | Use Case |
|--------|----------|---------------|----------|
| **Conservative** | 65-75% | 10-20 | Highest quality, fewer trades |
| **Balanced** | 60-70% | 30-50 | Good balance (recommended) |
| **Aggressive** | 55-65% | 50-100 | More trades, lower quality |

**Implementation:**

```python
from stock_signal_analyzer.engine import build_report
from stock_signal_analyzer.signal_filter import should_trade_signal

report = build_report('AAPL')

# Only trade if it passes the balanced filter
if should_trade_signal(report, filter_type='balanced'):
    print("✓ Trade this signal")
else:
    print("✗ Skip this signal")
```

---

## 📈 Phase 5: Paper Trading (1 month)

Once backtest results are good (>60% win rate, >2.0 profit factor), start paper trading:

### Setup Outcome Tracker

The outcome tracker automatically monitors open signals and records results:

```bash
# Run once to check current signals
python -m stock_signal_analyzer.outcome_tracker

# Or set up as a cron job (runs every hour)
crontab -e
# Add this line:
0 * * * * cd /Users/mhermovsisyan/Documents/GitHub/stock_signal_analyzer && source venv/bin/activate && python -m stock_signal_analyzer.outcome_tracker
```

### Monitor Live Performance

```bash
# Check outcome statistics
python -m stock_signal_analyzer.outcome_tracker

# Example output:
=== Статистика ===
Класс A:
  Всего: 28
  Win rate: 67.9%
  Profit factor: 2.45
  Avg win: 3.12%
  Avg loss: 1.67%
  Total PnL: +52.34%
```

### Publish Results

**Transparency builds trust.** Share your results:

1. **Daily:** Post on Twitter/X with #algotrading
2. **Weekly:** Blog post or YouTube video
3. **Monthly:** Detailed performance report

**Example tweet:**
```
Week 3 Paper Trading Results 📊
- Signals: 12 (Class A only)
- Win Rate: 66.7% (8W/4L)
- Profit Factor: 2.3
- Total PnL: +18.4%

All trades tracked transparently.
#algotrading #stocksignals
```

---

## 💰 Phase 6: Launch MVP Subscription (Month 2-3)

Once you have **1-2 months of proven results**, launch your subscription service.

### Pricing Tiers

| Tier | Price | Features |
|------|-------|----------|
| **Free** | $0 | Class C signals only |
| **Basic** | $29/mo | Class B + C signals |
| **Premium** | $99/mo | All signals (A/B/C) + priority support |
| **Pro** | $299/mo | All signals + API access |

### MVP Tech Stack

**Payment:** Stripe (easiest integration)
```bash
pip install stripe
```

**Landing Page:** Simple HTML + Tailwind CSS
- Hero: "Proven 65%+ Win Rate Stock Signals"
- Results: Live equity curve
- Pricing: 3 tiers
- CTA: "Start 7-Day Free Trial"

**Delivery:** Telegram bot (already built!)
- Users subscribe via Stripe
- Bot sends signals based on tier
- `/subscribe` command to link account

### Marketing Channels

1. **Twitter/X:** Daily signal previews (Class C only)
2. **YouTube:** Weekly market analysis + signal reviews
3. **Reddit:** r/algotrading, r/stocks (be helpful, not spammy)
4. **Discord:** Free community + premium channel
5. **Blog:** SEO content (e.g., "How to Trade Stock Signals")

### Success Metrics

**Month 1 Goal:** 10 paying customers ($290-990 MRR)
**Month 3 Goal:** 50 paying customers ($1,450-4,950 MRR)
**Month 6 Goal:** 200 paying customers ($5,800-19,800 MRR)

---

## 🔧 Troubleshooting

### "No signals being logged"

**Check:**
```bash
echo $SSA_SIGNAL_LOG
# Should output: /var/lib/stock_signal_analyzer/signals.jsonl

# Test signal generation
python -c "from stock_signal_analyzer.engine import build_report; build_report('AAPL')"

# Check if file was created
ls -lh $SSA_SIGNAL_LOG
```

### "Backtest shows poor results (<55% win rate)"

**Possible causes:**
1. Not enough data (need 50+ signals)
2. Market conditions changed
3. Signals not filtered properly

**Solutions:**
- Collect more signals (100+ recommended)
- Use only Class A signals: `--min-tier A`
- Increase confidence threshold: `--min-confidence 0.75`
- Check ADX (trend strength): `--min-adx 25`

### "Outcome tracker not finding signals"

**Check:**
```bash
# Verify SSA_SIGNAL_LOG is set
echo $SSA_SIGNAL_LOG

# Check file exists and has content
wc -l $SSA_SIGNAL_LOG

# Run tracker with debug output
python -m stock_signal_analyzer.outcome_tracker
```

---

## 📚 Additional Resources

### Documentation
- `MONETIZATION_PLAN.md` - Full monetization strategy
- `MONETIZATION_COMPONENTS.md` - Technical implementation details
- `FUTURE_IMPROVEMENTS.md` - Roadmap for v1.3.0+

### Tools
- `tools/verify_monetization.py` - Verify all components work
- `tools/monitor_signals.py` - Track collection progress
- `tools/backtest.py` - Run historical backtests
- `stock_signal_analyzer/outcome_tracker.py` - Track live results
- `stock_signal_analyzer/signal_filter.py` - Filter signal quality

### Support
- GitHub Issues: Report bugs or request features
- Telegram: @your_support_channel (set this up!)

---

## 🎯 Quick Reference

```bash
# Verify setup
python tools/verify_monetization.py

# Monitor progress
python tools/monitor_signals.py

# Run backtest (when 50+ signals)
python tools/backtest.py $SSA_SIGNAL_LOG --min-tier A

# Track outcomes (paper trading)
python -m stock_signal_analyzer.outcome_tracker

# Start Telegram bot
python telegram_bot.py
```

---

## ✅ Checklist

**Week 1:**
- [ ] Set environment variables
- [ ] Run verification script (all checks pass)
- [ ] Start signal collection (bot or manual)
- [ ] Monitor daily with `monitor_signals.py`

**Week 2:**
- [ ] Reach 50+ signals
- [ ] Run first backtest
- [ ] Analyze results (win rate, profit factor)
- [ ] Optimize filters if needed

**Week 3-4:**
- [ ] Start paper trading
- [ ] Set up outcome tracker (cron job)
- [ ] Publish results daily/weekly
- [ ] Build audience (Twitter, YouTube)

**Month 2:**
- [ ] Prove 60%+ win rate over 1 month
- [ ] Create landing page
- [ ] Integrate Stripe
- [ ] Launch MVP subscription
- [ ] Get first 10 paying customers

**Month 3+:**
- [ ] Scale to 50+ customers
- [ ] Add API access (Pro tier)
- [ ] Consider copy-trading integration
- [ ] Target $5k+ MRR

---

**Ready to start?** Run the verification script:

```bash
python tools/verify_monetization.py
```

Good luck! 🚀
