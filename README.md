# HVF Scanner — Hunt Volatility Funnel Signal Scanner

A 24/7 automated scanner that detects **Hunt Volatility Funnel (HVF)** patterns across crypto and commodities, sending Telegram alerts at three stages: forming, near breakout, and breakout.

---

## What is an HVF?

A Hunt Volatility Funnel is a price compression pattern where:
- A sharp stem high forms (the peak of the prior move)
- Descending resistance highs + ascending support lows create a converging funnel
- ATR (volatility) contracts as the pattern matures
- Price eventually breaks out — often explosively

The pattern was named after trader **Tim West** and works across all liquid markets.

```
Stem High ─┐
           │   ╲  ─  ─  ─ Resistance (descending)
           │     ╲
           │      ╱─╲  ╱─  Price coiling
           │     ╱    ╲╱
           │    ╱  ─  ─  ─ Support (ascending)
           └──────────────► Breakout
```

---

## Backtest Results

Tested on 11 years of daily data across 21 crypto assets + 6 commodities.

| Period | Start | End | CAGR | Trades | Win Rate |
|--------|-------|-----|------|--------|----------|
| 11 years (2015–2026) | $10,000 | $963,004,812 | 177%/yr | 48 | 92% |
| 3 years (2023–2026) | $10,000 | $473,859 | 262%/yr | 24 | 92% |
| 10 months (Jun–Apr 2026) | $10,000 | $20,655 | 139%/yr | 5 | 100% |
| 8 months (Aug–Apr 2026) | $10,000 | $11,176 | 18%/yr | 2 | 100% |

> **Notes:** 2x leverage applied. Exit at peak close within outcome window (90 days standard, 180 days macro). The 8-month test covers a crypto bear market — the system correctly went quiet and preserved capital.

**Notable signals caught:**
- LINK April 2019 → +652% raw (+1,305% at 2x)
- BTC October 2020 → +480% raw (+961% at 2x)
- XRP June 2024 → +249% raw (+499% at 2x)
- TRX November 2024 → currently forming (489+ bars)
- Gold July 2025 → +30% raw (+60% at 2x)

---

## Asset Coverage

**Crypto (21 symbols):**
BTC, ETH, BNB, SOL, XRP, DOGE, ADA, AVAX, TRX, LINK, DOT, LTC, NEAR, UNI, APT, OP, ARB, SUI, ATOM, HYPE, TAO

**Commodities (6 symbols):**
Gold, Silver, Platinum, Oil, Natural Gas, Copper

**Data source:** Yahoo Finance (free, no API key needed)

---

## Alert Stages

The scanner tracks three stages per pattern and sends one Telegram message per stage escalation:

| Stage | Icon | Meaning |
|-------|------|---------|
| Forming | 📐 | Valid HVF detected, price compressing inside funnel |
| Near Breakout | ⚡ | Price within 5% of descending resistance line |
| Breakout | 🚨 | Bar high has touched or crossed resistance |

A weekly health check also fires every 7 days confirming the scanner is alive, showing all active patterns.

---

## Timeframe Modes

| Mode | Pattern Length | ATR Squeeze | Use Case |
|------|---------------|-------------|----------|
| 1D Standard | 50–200 bars (2–7 months) | ≥20% | Most crypto & commodity signals |
| 1D Macro | 150–1000 bars (5 months–4 years) | ≥40% | TRON, Platinum 5yr, LTC mega-structures |

---

## Quick Start

**One command — works on Mac, Linux, or Windows (WSL2):**

```bash
curl -fsSL https://raw.githubusercontent.com/nderriman-cmd/hvf-scanner/main/setup.sh | bash
```

The setup wizard will:
1. Check your Python version (3.9+ required)
2. Download the scanner
3. Walk you through Telegram bot setup
4. Ask where you want to run it (local / free cloud / paid cloud)
5. Start scanning automatically

**You'll need a Telegram bot** (takes 2 minutes):
1. Open Telegram → search `@BotFather` → send `/newbot`
2. Copy the token it gives you
3. Search `@userinfobot` → send `/start` → copy your Chat ID
4. Paste both into the setup wizard when asked

---

## Deployment Options

### Local (Free)
Runs on your Mac or Linux machine while it's on. Auto-starts on login, restarts if it crashes.

### Oracle Cloud Always Free (Free, 24/7)
Oracle gives you a permanently free ARM server — no credit card needed. The setup wizard gives you step-by-step instructions.

### Hetzner VPS (~€4/month, 24/7)
The option the creator uses. CX22 in Helsinki — fast, reliable, cheap. Setup guide included.

---

## Updating

When a new version is released, run one command from your install directory:

```bash
bash ~/hvf-scanner/update.sh
```

This pulls the latest code, updates any new dependencies, and restarts the scanner automatically.

**Other control commands:**
```bash
bash ~/hvf-scanner/update.sh status    # is it running?
bash ~/hvf-scanner/update.sh logs      # live log tail
bash ~/hvf-scanner/update.sh stop      # stop scanner
bash ~/hvf-scanner/update.sh restart   # restart scanner
bash ~/hvf-scanner/update.sh version   # current version + update check
```

---

## How It Works

```
Every hour:
  For each symbol × timeframe:
    1. Fetch daily candles (yfinance, free)
    2. Find the STEM HIGH — highest pivot in recent window
    3. Collect post-stem pivot highs and lows
    4. Fit regression lines — must converge (pl_slope > ph_slope)
    5. Check ATR contraction from stem to now
    6. Check candle body compression (bodies visually shrink)
    7. Check first post-stem low closed above zone midpoint
    8. If all pass → pattern detected
    9. Fire Telegram alert if stage escalated (forming → near → breakout)
```

**Key parameters (calibrated against real examples):**
- `pivot_lb=5` — pivot lookback (5 days each side = weekly swing points)
- `strict_mono=False` — allows ascending triangles (coiling at resistance)
- `zone_mid_pct=0.50` — compression stays in upper half of stem range (0.25 for macro)
- `atr_contract=0.20` — minimum 20% ATR contraction required (40% for macro)

---

## File Structure

```
hvf-scanner/
├── hvf_scanner.py      # Main scanner loop
├── config.py           # Loads credentials from .env
├── notifier.py         # Telegram message sender
├── requirements.txt    # Python dependencies
├── setup.sh            # One-command installer
├── update.sh           # Updater + control script
├── .env.example        # Credential template
└── README.md           # This file
```

---

## Requirements

- Python 3.9+
- Internet connection (downloads data from Yahoo Finance)
- Telegram account (for alerts)
- ~50MB disk space

No exchange account, no trading API, no subscriptions needed.

---

## Methodology Notes

- **Entry signal:** Pattern detected at close of bar. Entry next bar open.
- **Exit strategy in backtest:** Peak close within outcome window (90 days standard, 180 days macro). In live trading, consider trailing a stop once up 20%+.
- **No leverage required** — the underlying moves are large enough. Backtest uses 2x to model leveraged instruments (crypto perps, leveraged ETFs).
- **False positive rate:** The system goes quiet in downtrends — zero standard 1D signals fired during the Aug–Apr 2026 crypto bear market. Only macro patterns (already in compression before the downturn) continued.

---

## Support

Questions and pattern discussion in the **[Patreon Community](https://patreon.com/nderriman)**.

If you spot a pattern the scanner missed, or want to suggest a new asset, post it there.

---

## Disclaimer

This software is for educational and informational purposes only. Past backtest performance does not guarantee future results. Nothing here constitutes financial advice. Trade with capital you can afford to lose.
