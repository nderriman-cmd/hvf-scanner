"""
HVF (Hunt Volatility Funnel) Scanner — v3
==========================================
Scans crypto (Bybit) and commodities (yfinance) on 2H and 1D timeframes
for Hunt Volatility Funnel patterns.

Pattern definition (calibrated against real examples):
  - Gold Apr-Aug 2025 on 2H    : 141 days / ~1,700 bars
  - XRP May-Dec 2017 on 1D     : 221 bars
  - Platinum 2020-2025 on 1D   : 1,877 bars (macro)
  - Platinum Dec-Jan 2026 on 2H: 32 days / ~384 bars (short)
  - Tron Nov 2024 onwards on 1D: 497+ bars, still forming

Three alert stages (each fires ONCE per pattern, escalates upward):
  📐 FORMING     — valid HVF detected, price still compressing inside funnel
  ⚡ NEAR BREAK  — price within 5% of descending resistance line
  🚨 BREAKOUT    — bar high has touched / crossed the resistance line

Asset coverage:
  Crypto     — top 20 USDT perpetuals via Bybit
  Commodities— Gold, Silver, Platinum, Oil, Natural Gas, Copper via yfinance

Run:
  python hvf_scanner.py
"""

import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from config import config
from notifier import TelegramNotifier

# ── Try importing yfinance (commodities) ─────────────────────────────────────
try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False
    logging.warning("yfinance not installed — commodity scanning disabled. Run: pip install yfinance")

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("hvf_scanner.log"),
    ],
)
logger = logging.getLogger(__name__)

# ── Symbol lists ──────────────────────────────────────────────────────────────

# Crypto: display label → yfinance ticker
# All major pairs available on Yahoo Finance — no exchange API needed
CRYPTO_SYMBOLS = {
    "BTC":   "BTC-USD",
    "ETH":   "ETH-USD",
    "BNB":   "BNB-USD",
    "SOL":   "SOL-USD",
    "XRP":   "XRP-USD",
    "DOGE":  "DOGE-USD",
    "ADA":   "ADA-USD",
    "AVAX":  "AVAX-USD",
    "TRX":   "TRX-USD",
    "LINK":  "LINK-USD",
    "DOT":   "DOT-USD",
    "LTC":   "LTC-USD",
    "NEAR":  "NEAR-USD",
    "UNI":   "UNI-USD",
    "APT":   "APT-USD",
    "OP":    "OP-USD",
    "ARB":   "ARB-USD",
    "SUI":   "SUI-USD",
    "ATOM":  "ATOM-USD",
    "HYPE":  "HYPE-USD",
    "TAO":   "TAO-USD",
}

# Commodities: display label → yfinance ticker
COMMODITY_SYMBOLS = {
    "GOLD":    "GC=F",
    "SILVER":  "SI=F",
    "PLAT":    "PPLT",   # physical Platinum ETF — cleaner data than PL=F futures
    "OIL":     "CL=F",
    "NATGAS":  "NG=F",
    "COPPER":  "HG=F",
}

# ── Timeframe configs ─────────────────────────────────────────────────────────
# Calibrated against real HVF examples — see module docstring

TIMEFRAME_CONFIGS = {
    # Standard 1D — 2 to 7 month compression structures
    # Catches: SOL Feb 2024 (+82%), BNB 2024, Gold Apr-Aug 2025, XRP 2017 (221 bars)
    "D": {
        "label":        "1D",
        "pivot_lb":     5,      # 5 days each side — major weekly swing pivots
        "min_pat_bars": 50,     # ≥50 bars (~10 weeks) minimum compression
        "atr_contract": 0.20,   # ≥20% ATR contraction from stem
        "window":       300,    # bars of history passed to detector each scan
        "stem_window":  200,    # stem must be within last 200 daily bars (~7 months)
        "near_pct":     0.05,   # within 5% of resistance = near-breakout alert
        "cooldown_bars":30,     # ~6 weeks between signals on same pattern
        "yf_interval":  "1d",
        "yf_period":    "10y",
        "strict_mono":  False,  # allows ascending triangles (Gold Apr-Aug 2025)
        "zone_mid_pct": 0.50,   # compression stays in upper half of stem range
    },
    # Macro 1D — 5 month to 4 year mega compression structures
    # Catches: TRON 489-bar (77% squeeze), LTC 952-bar (+107%), XRP Jun 2025 (+249%)
    "D-macro": {
        "label":        "1D-macro",
        "pivot_lb":     10,     # 10-day each side — filters noise, only major pivots
        "min_pat_bars": 150,    # ≥150 bars (~5 months) — cleanly above standard 1D
        "atr_contract": 0.40,   # ≥40% squeeze — macro patterns compress deeply
        "window":       1200,   # full history window
        "stem_window":  1000,   # stem can be up to ~4 years ago
        "near_pct":     0.05,
        "cooldown_bars":150,    # one alert per mega-pattern (~6 months apart)
        "yf_interval":  "1d",
        "yf_period":    "10y",
        "strict_mono":  False,  # mandatory — long patterns oscillate mid-compression
        "zone_mid_pct": 0.25,   # allows deep retracements (TRON -52%, Plat -40%)
    },
}

POLL_SECS  = 3600       # scan every 1 hour (2H/1D candles don't need faster polling)
ALERT_FILE = "hvf_alerts.json"
MIN_PIVOTS = 3   # 3 confirmed compression pivots each side for reliable trendlines


# ── Indicators ────────────────────────────────────────────────────────────────

def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat(
        [h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def find_pivot_highs(high: pd.Series, lb: int) -> np.ndarray:
    arr, idx = high.values, []
    for i in range(lb, len(arr) - lb):
        if (all(arr[i] > arr[i - j] for j in range(1, lb + 1)) and
                all(arr[i] > arr[i + j] for j in range(1, lb + 1))):
            idx.append(i)
    return np.array(idx, dtype=int)


def find_pivot_lows(low: pd.Series, lb: int) -> np.ndarray:
    arr, idx = low.values, []
    for i in range(lb, len(arr) - lb):
        if (all(arr[i] < arr[i - j] for j in range(1, lb + 1)) and
                all(arr[i] < arr[i + j] for j in range(1, lb + 1))):
            idx.append(i)
    return np.array(idx, dtype=int)


# ── HVF detection ─────────────────────────────────────────────────────────────

def detect_hvf(df: pd.DataFrame, tf_cfg: dict):
    """
    HVF detection built around the STEM HIGH concept.

    Structure:
      1. STEM HIGH — earliest pivot high at ≥95% of the window peak.
         This is where compression begins (e.g. BNB March 15, Gold April 8).
      2. Compression zone — pivot highs/lows strictly AFTER the stem.
         Need MIN_PIVOTS of each to fit converging trendlines.
      3. ATR and body compression measured from stem → now.
      4. Three alert stages based on distance to resistance trendline.
    """
    lb            = tf_cfg["pivot_lb"]
    min_pat_bars  = tf_cfg["min_pat_bars"]
    atr_contract  = tf_cfg["atr_contract"]
    near_pct      = tf_cfg["near_pct"]
    n             = len(df)

    if n < lb * 2 + 20:
        return None

    all_ph_idx = find_pivot_highs(df["high"], lb)
    all_pl_idx = find_pivot_lows(df["low"],   lb)

    if len(all_ph_idx) < 1 or len(all_pl_idx) < MIN_PIVOTS:
        return None

    # ── 1. Find the STEM HIGH ─────────────────────────────────────────────────
    #   The HIGHEST pivot in the most recent stem_window bars.
    #   Using a fixed lookback prevents distant peaks from overshadowing the
    #   actual structure (e.g. a May rally masking an October HVF).
    stem_win    = tf_cfg.get("stem_window", n)
    stem_search = all_ph_idx[all_ph_idx >= max(0, n - stem_win)]
    if len(stem_search) == 0:
        return None

    stem_prices_arr = df["high"].iloc[stem_search].values.astype(float)
    stem_idx        = int(stem_search[np.argmax(stem_prices_arr)])

    # ── 2. Compression-zone pivots (strictly AFTER the stem) ─────────────────
    post_ph = all_ph_idx[all_ph_idx > stem_idx]
    post_pl = all_pl_idx[all_pl_idx > stem_idx]

    # Per-config pivot count — supports separate high/low minimums.
    # "min_pivots_h" / "min_pivots_l" let tight mode require 3 descending highs
    # (strong resistance) but only 2 ascending lows (accommodates wick-style lows).
    # Falls back to "min_pivots" → MIN_PIVOTS when not set.
    min_piv_h = tf_cfg.get("min_pivots_h", tf_cfg.get("min_pivots", MIN_PIVOTS))
    min_piv_l = tf_cfg.get("min_pivots_l", tf_cfg.get("min_pivots", MIN_PIVOTS))

    if len(post_ph) < min_piv_h or len(post_pl) < min_piv_l:
        return None

    # ── 2b. First post-stem low must CLOSE above the midpoint of the stem move ─
    #   Uses CLOSE at the first pivot low bar (not the wick) so a flash-crash
    #   wick that recovers above the midpoint doesn't disqualify the pattern
    #   (e.g. HYPE June 13 wick to $37.26 but close at $38.70).
    stem_high      = float(df["high"].iloc[stem_idx])
    pre_low        = float(df["low"].iloc[max(0, stem_idx - 200) : stem_idx + 1].min())
    zone_mid_pct   = tf_cfg.get("zone_mid_pct", 0.50)
    zone_mid       = pre_low + (stem_high - pre_low) * zone_mid_pct
    first_pl_close = float(df["close"].iloc[post_pl[0]])
    if first_pl_close < zone_mid:
        return None

    # ── 3-6. Trendline fitting — strict vs relaxed mode ─────────────────────
    #   strict_mono=True  (default): consecutive-pair monotonicity required
    #                                classic descending-highs / ascending-lows wedge
    #   strict_mono=False           : skips pair checks, uses ALL post-stem pivots
    #                                catches ascending triangles (coiling at resistance)
    strict_mono = tf_cfg.get("strict_mono", True)

    if strict_mono:
        ph_idx    = post_ph[-min_piv_h:]
        pl_idx    = post_pl[-min_piv_l:]
        ph_prices = df["high"].iloc[ph_idx].values.astype(float)
        pl_prices = df["low"].iloc[pl_idx].values.astype(float)
        if not all(ph_prices[j] < ph_prices[j - 1] for j in range(1, len(ph_prices))):
            return None
        if not all(pl_prices[j] > pl_prices[j - 1] for j in range(1, len(pl_prices))):
            return None
        if max(ph_idx[0], pl_idx[0]) >= min(ph_idx[-1], pl_idx[-1]):
            return None
    else:
        # Relaxed mode — use all post-stem pivots, trust the polyfit
        ph_idx    = post_ph
        pl_idx    = post_pl
        ph_prices = df["high"].iloc[ph_idx].values.astype(float)
        pl_prices = df["low"].iloc[pl_idx].values.astype(float)

    ph_slope, ph_intercept = np.polyfit(ph_idx.astype(float), ph_prices, 1)
    pl_slope, pl_intercept = np.polyfit(pl_idx.astype(float), pl_prices, 1)
    if pl_slope <= ph_slope:
        return None
    if not strict_mono and pl_slope <= 0:
        return None
    # Optional strict slope check — rejects falling channels where both
    # trendlines drift downward (common source of false positives in tight mode)
    if tf_cfg.get("strict_slopes", False):
        if ph_slope >= 0 or pl_slope <= 0:
            return None

    # ── 7. ATR contraction from stem high ────────────────────────────────────
    atr       = calc_atr(df)
    atr_start = float(atr.iloc[stem_idx])
    atr_now   = float(atr.iloc[-1])
    if atr_start == 0:
        return None
    contraction = 1.0 - (atr_now / atr_start)
    if contraction < atr_contract:
        return None

    # ── 8. Pattern long enough (from stem to now) ────────────────────────────
    cur = n - 1
    if cur - stem_idx < min_pat_bars:
        return None

    # ── 9. Candle body compression from stem to now ───────────────────────────
    #   The defining visual: candle bodies shrink toward the apex.
    #   Threshold is configurable (tight mode uses 0.22, standard uses 0.15).
    body_threshold = tf_cfg.get("body_contract", 0.15)
    pat_df     = df.iloc[stem_idx : cur + 1]
    body_sizes = (pat_df["close"] - pat_df["open"]).abs()
    third      = max(1, len(body_sizes) // 3)
    body_early = body_sizes.iloc[:third].mean()
    body_late  = body_sizes.iloc[-third:].mean()
    if body_early == 0:
        return None
    if (1.0 - body_late / body_early) < body_threshold:
        return None

    # ── Project trendlines ───────────────────────────────────────────────────
    resistance = float(ph_slope * cur + ph_intercept)
    support    = float(pl_slope * cur + pl_intercept)
    price      = float(df["close"].iloc[-1])
    bar_high   = float(df["high"].iloc[-1])

    # Price must still be inside the funnel
    if price > resistance * 1.08 or price < support * 0.92:
        return None

    dist_pct  = (resistance - price) / price   # positive = below resistance
    broke_out = bar_high >= resistance

    if broke_out:
        stage = "breakout"
    elif dist_pct <= near_pct:
        stage = "near"
    else:
        stage = "forming"

    return {
        "resistance":      resistance,
        "support":         support,
        "price":           price,
        "dist_pct":        dist_pct,
        "stage":           stage,
        "contraction_pct": contraction * 100,
        "pattern_start":   stem_idx,
        "pattern_bars":    cur - stem_idx,
    }


# ── Data fetching ─────────────────────────────────────────────────────────────

def fetch_candles(yf_ticker: str, tf_cfg: dict) -> pd.DataFrame:
    """Fetch daily OHLCV from yfinance. Works for both crypto and commodities."""
    if not YFINANCE_AVAILABLE:
        return pd.DataFrame()
    try:
        raw = yf.download(
            yf_ticker,
            period=tf_cfg["yf_period"],
            interval=tf_cfg["yf_interval"],
            progress=False,
            auto_adjust=True,
        )
        if raw.empty:
            return pd.DataFrame()

        raw = raw.rename(columns=str.lower)
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        raw = raw[["open", "high", "low", "close", "volume"]].dropna()

        raw = raw.reset_index()
        raw = raw.rename(columns={"Datetime": "timestamp", "Date": "timestamp",
                                   "index": "timestamp"})
        if "timestamp" not in raw.columns:
            raw = raw.rename(columns={raw.columns[0]: "timestamp"})

        window = tf_cfg["window"]
        return raw.iloc[-window:].reset_index(drop=True)
    except Exception as exc:
        logger.warning("yfinance fetch failed for %s: %s", yf_ticker, exc)
        return pd.DataFrame()


# ── Alert state ───────────────────────────────────────────────────────────────

def load_state() -> dict:
    if Path(ALERT_FILE).exists():
        with open(ALERT_FILE) as f:
            return json.load(f)
    return {}


def save_state(state: dict) -> None:
    with open(ALERT_FILE, "w") as f:
        json.dump(state, f, indent=2)


STAGE_RANK = {"forming": 1, "near": 2, "breakout": 3}


def state_key(symbol: str, tf_label: str, pattern_start: int) -> str:
    bucket = (pattern_start // 5) * 5
    return f"{symbol}_{tf_label}_{bucket}"


# ── Messages ──────────────────────────────────────────────────────────────────

def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# ── Weekly health check ───────────────────────────────────────────────────────

HEALTH_CHECK_FILE = "hvf_health.json"
HEALTH_CHECK_SECS = 7 * 24 * 3600   # every 7 days


def load_health() -> dict:
    if Path(HEALTH_CHECK_FILE).exists():
        with open(HEALTH_CHECK_FILE) as f:
            return json.load(f)
    return {"last_check": 0, "scans_since_start": 0, "signals_since_start": 0}


def save_health(h: dict) -> None:
    with open(HEALTH_CHECK_FILE, "w") as f:
        json.dump(h, f, indent=2)


def build_health_message(state: dict, health: dict) -> str:
    """Build weekly health check Telegram message."""
    # Count active patterns by stage from state file
    active = {"forming": [], "near": [], "breakout": []}
    for key, info in state.items():
        parts = key.split("_")
        if len(parts) >= 2:
            symbol = parts[0]
            stage  = info["stage"] if isinstance(info, dict) else info
            if stage in active:
                active[stage].append(symbol)

    forming_list  = ", ".join(sorted(set(active["forming"])))  or "none"
    near_list     = ", ".join(sorted(set(active["near"])))     or "none"
    breakout_list = ", ".join(sorted(set(active["breakout"]))) or "none"

    total_active = sum(len(v) for v in active.values())

    return (
        f"<b>💚 HVF Scanner — Weekly Health Check</b>\n\n"
        f"Status:         <b>Running ✓</b>\n"
        f"Scans run:      {health['scans_since_start']:,}\n"
        f"Signals fired:  {health['signals_since_start']:,} since start\n"
        f"Active patterns:{total_active}\n\n"
        f"<b>📐 Forming ({len(active['forming'])}):</b>  {forming_list}\n"
        f"<b>⚡ Near ({len(active['near'])}):</b>      {near_list}\n"
        f"<b>🚨 Breakout ({len(active['breakout'])}):</b> {breakout_list}\n\n"
        f"Watching:  {len(CRYPTO_SYMBOLS)} crypto · {len(COMMODITY_SYMBOLS)} commodities\n"
        f"Modes:     1D standard + 1D-macro\n"
        f"Next scan: in ~1 hour\n\n"
        f"<i>{now_utc()}</i>"
    )


def build_message(symbol: str, tf_label: str, hvf: dict) -> str:
    stage = hvf["stage"]
    if stage == "breakout":
        icon     = "🚨"
        headline = "HVF BREAKOUT"
        detail   = f"High touched resistance  <b>${hvf['resistance']:,.4f}</b>"
    elif stage == "near":
        icon     = "⚡"
        headline = "HVF NEAR BREAKOUT"
        detail   = f"{hvf['dist_pct']*100:.1f}% below resistance  (${hvf['resistance']:,.4f})"
    else:
        icon     = "📐"
        headline = "HVF FORMING"
        detail   = f"{hvf['dist_pct']*100:.1f}% below resistance  (${hvf['resistance']:,.4f})"

    return (
        f"<b>{icon} {headline} — {symbol} [{tf_label}]</b>\n\n"
        f"Status:        {detail}\n"
        f"Price:         <b>${hvf['price']:,.4f}</b>\n"
        f"Support line:  ${hvf['support']:,.4f}\n"
        f"ATR squeeze:   {hvf['contraction_pct']:.0f}% contraction\n"
        f"Pattern age:   {hvf['pattern_bars']} bars\n\n"
        f"<i>Hunt Volatility Funnel — descending highs + ascending lows "
        f"compressing toward breakout</i>\n"
        f"<i>{now_utc()}</i>"
    )


# ── Main loop ─────────────────────────────────────────────────────────────────

def run() -> None:
    n_crypto     = len(CRYPTO_SYMBOLS)
    n_commodity  = len(COMMODITY_SYMBOLS)
    logger.info(
        "HVF Scanner v3 started — %d crypto + %d commodities × 1D + 1D-macro — poll every %ds",
        n_crypto, n_commodity, POLL_SECS,
    )

    notifier = TelegramNotifier(config.telegram_token, config.telegram_chat_id)
    state    = load_state()
    health   = load_health()

    notifier.send(
        f"<b>🔍 HVF Scanner v3 started</b>\n\n"
        f"Crypto:      {n_crypto} symbols\n"
        f"Commodities: {n_commodity} symbols\n"
        f"Timeframes:  1D standard + 1D-macro\n"
        f"Data source: Yahoo Finance (no API key needed)\n"
        f"Alerts:      📐 Forming  ⚡ Near  🚨 Breakout\n\n"
        f"<i>{now_utc()}</i>"
    )

    # Build unified scan list: (display_name, yf_ticker)
    all_symbols = (
        list(CRYPTO_SYMBOLS.items()) +
        list(COMMODITY_SYMBOLS.items())
    )

    while True:
        alerts_fired = 0

        for tf_label_key, tf_cfg in TIMEFRAME_CONFIGS.items():
            tf_label = tf_cfg["label"]

            for name, ticker in all_symbols:
                try:
                    df = fetch_candles(ticker, tf_cfg)
                    if df.empty or len(df) < 100:
                        continue
                    hvf = detect_hvf(df, tf_cfg)
                    if hvf is None:
                        continue

                    key       = state_key(name, tf_label, hvf["pattern_start"])
                    prev_info = state.get(key)

                    # ── Migrate old plain-string state entries ────────────────
                    if isinstance(prev_info, str):
                        prev_info = {"stage": prev_info, "locked_resistance": hvf["resistance"]}
                        state[key] = prev_info

                    prev_stage = prev_info["stage"] if prev_info else None
                    prev_rank  = STAGE_RANK.get(prev_stage, 0)

                    # ── Lock resistance at first detection (FORMING) ──────────
                    # The polyfit resistance shifts as new pivot highs form near
                    # the breakout zone. We lock it at first sight so the breakout
                    # level doesn't drift upward as price approaches it.
                    if prev_info is None:
                        locked_res = hvf["resistance"]
                    else:
                        locked_res = prev_info.get("locked_resistance", hvf["resistance"])

                    # ── Recompute stage using locked resistance ────────────────
                    price    = hvf["price"]
                    bar_high = float(df["high"].iloc[-1])
                    near_pct = tf_cfg["near_pct"]

                    dist_pct  = (locked_res - price) / price
                    broke_out = bar_high >= locked_res

                    if broke_out:
                        cur_stage = "breakout"
                    elif dist_pct <= near_pct:
                        cur_stage = "near"
                    else:
                        cur_stage = "forming"

                    cur_rank = STAGE_RANK[cur_stage]

                    if cur_rank > prev_rank:
                        # Override hvf dict values with locked-resistance versions
                        hvf["stage"]      = cur_stage
                        hvf["dist_pct"]   = dist_pct
                        hvf["resistance"] = locked_res

                        logger.info("[%s %s] %s  dist=%.1f%%  squeeze=%.0f%%  bars=%d",
                            name, tf_label, cur_stage.upper(),
                            dist_pct * 100, hvf["contraction_pct"], hvf["pattern_bars"])
                        notifier.send(build_message(name, tf_label, hvf))
                        state[key] = {"stage": cur_stage, "locked_resistance": locked_res}
                        save_state(state)
                        alerts_fired += 1

                except Exception as exc:
                    logger.warning("[%s %s] %s", name, tf_label, exc)

        # ── Update health counters ────────────────────────────────────────────
        health["scans_since_start"] = health.get("scans_since_start", 0) + 1
        health["signals_since_start"] = health.get("signals_since_start", 0) + alerts_fired

        # ── Weekly health check ───────────────────────────────────────────────
        now_ts = time.time()
        if now_ts - health.get("last_check", 0) >= HEALTH_CHECK_SECS:
            logger.info("Sending weekly health check")
            notifier.send(build_health_message(state, health))
            health["last_check"] = now_ts

        save_health(health)
        logger.info("Scan complete — %d alert(s) fired — sleeping %ds", alerts_fired, POLL_SECS)
        time.sleep(POLL_SECS)


if __name__ == "__main__":
    run()
