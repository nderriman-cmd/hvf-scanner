"""
HVF (Hunt Volatility Funnel) Scanner — v4
==========================================
Scans crypto and commodities on 1D and 1D-macro timeframes
for Hunt Volatility Funnel patterns.

Pattern definition (calibrated against real examples):
  - Gold Apr-Aug 2025 on 1D    : 141 bars
  - XRP May-Dec 2017 on 1D     : 221 bars
  - Platinum 2020-2025 on 1D   : 1,877 bars (macro)
  - Tron Nov 2024 onwards on 1D: 505+ bars, still forming

Four alert stages — WATCHING is weekly-snapshot only, the rest fire live Telegram alerts:
  👁  WATCHING    — 2 pivot highs + 2 pivot lows: pattern embryo (weekly snapshot only)
  📐 FORMING     — 3+ pivots each side, ≥20% ATR squeeze: confirmed compression
  ⚡ NEAR BREAK  — price within 5% of descending resistance line
  🚨 BREAKOUT    — bar high has touched / crossed the resistance line

Asset coverage:
  Crypto     — top 21 symbols via Yahoo Finance
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

COMMODITY_SYMBOLS = {
    "GOLD":    "GC=F",
    "SILVER":  "SI=F",
    "PLAT":    "PPLT",   # physical Platinum ETF — cleaner data than PL=F futures
    "OIL":     "CL=F",
    "NATGAS":  "NG=F",
    "COPPER":  "HG=F",
}

# ── Timeframe configs ─────────────────────────────────────────────────────────

TIMEFRAME_CONFIGS = {
    # Standard 1D — 2 to 7 month compression structures
    "D": {
        "label":        "1D",
        "pivot_lb":     5,
        "min_pat_bars": 50,
        "atr_contract": 0.20,
        "window":       300,
        "stem_window":  200,
        "near_pct":     0.05,
        "cooldown_bars":30,
        "yf_interval":  "1d",
        "yf_period":    "10y",
        "strict_mono":  False,
        "zone_mid_pct": 0.50,
    },
    # Macro 1D — 5 month to 4 year mega compression structures
    "D-macro": {
        "label":        "1D-macro",
        "pivot_lb":     10,
        "min_pat_bars": 150,
        "atr_contract": 0.40,
        "window":       1200,
        "stem_window":  1000,
        "near_pct":     0.05,
        "cooldown_bars":150,
        "yf_interval":  "1d",
        "yf_period":    "10y",
        "strict_mono":  False,
        "zone_mid_pct": 0.25,
    },
}

# ── WATCHING configs ──────────────────────────────────────────────────────────
# Pre-FORMING stage: only 2 pivots each side required, lower ATR threshold.
# WATCHING patterns appear in the weekly snapshot only — no live Telegram alerts.
# Think of it as: "stem high found, first two compression pivots complete,
# waiting to see if the third confirms the funnel."

WATCHING_CONFIGS = {
    "D": {
        **TIMEFRAME_CONFIGS["D"],
        "min_pivots_h": 2,      # 2 descending highs (vs 3 for FORMING)
        "min_pivots_l": 2,      # 2 ascending lows   (vs 3 for FORMING)
        "atr_contract": 0.08,   # 8% ATR squeeze — just starting to compress
        "min_pat_bars": 25,     # ~5 weeks minimum (vs 50 for FORMING)
        "body_contract": 0.10,  # 10% body compression (vs 15% for FORMING)
    },
    "D-macro": {
        **TIMEFRAME_CONFIGS["D-macro"],
        "min_pivots_h": 2,
        "min_pivots_l": 2,
        "atr_contract": 0.20,   # half of macro's 0.40
        "min_pat_bars": 75,     # half of macro's 150
        "body_contract": 0.10,
    },
}

POLL_SECS  = 3600       # scan every 1 hour
ALERT_FILE = "hvf_alerts.json"
MIN_PIVOTS = 3


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

    Works for both FORMING (min_pivots=3, atr_contract=0.20+) and
    WATCHING (min_pivots=2, atr_contract=0.08+) — controlled via tf_cfg.
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

    # Use per-config min_pivots when set (WATCHING uses 2, FORMING uses 3)
    min_piv_h = tf_cfg.get("min_pivots_h", tf_cfg.get("min_pivots", MIN_PIVOTS))
    min_piv_l = tf_cfg.get("min_pivots_l", tf_cfg.get("min_pivots", MIN_PIVOTS))

    if len(all_ph_idx) < 1 or len(all_pl_idx) < min_piv_l:
        return None

    # ── 1. Find the STEM HIGH ─────────────────────────────────────────────────
    stem_win    = tf_cfg.get("stem_window", n)
    stem_search = all_ph_idx[all_ph_idx >= max(0, n - stem_win)]
    if len(stem_search) == 0:
        return None

    stem_prices_arr = df["high"].iloc[stem_search].values.astype(float)
    stem_idx        = int(stem_search[np.argmax(stem_prices_arr)])

    # ── 2. Compression-zone pivots (strictly AFTER the stem) ─────────────────
    post_ph = all_ph_idx[all_ph_idx > stem_idx]
    post_pl = all_pl_idx[all_pl_idx > stem_idx]

    if len(post_ph) < min_piv_h or len(post_pl) < min_piv_l:
        return None

    # ── 2b. First post-stem low must CLOSE above the midpoint of the stem move ─
    stem_high      = float(df["high"].iloc[stem_idx])
    pre_low        = float(df["low"].iloc[max(0, stem_idx - 200) : stem_idx + 1].min())
    zone_mid_pct   = tf_cfg.get("zone_mid_pct", 0.50)
    zone_mid       = pre_low + (stem_high - pre_low) * zone_mid_pct
    first_pl_close = float(df["close"].iloc[post_pl[0]])
    if first_pl_close < zone_mid:
        return None

    # ── 3-6. Trendline fitting ───────────────────────────────────────────────
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

    # ── 9. Candle body compression ────────────────────────────────────────────
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

    if price > resistance * 1.08 or price < support * 0.92:
        return None

    dist_pct  = (resistance - price) / price
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


# ── Watching state ────────────────────────────────────────────────────────────

WATCHING_FILE = "hvf_watching.json"


def load_watching() -> dict:
    if Path(WATCHING_FILE).exists():
        with open(WATCHING_FILE) as f:
            return json.load(f)
    return {}


def save_watching(watching: dict) -> None:
    with open(WATCHING_FILE, "w") as f:
        json.dump(watching, f, indent=2)


# ── Stage ranking ─────────────────────────────────────────────────────────────
# WATCHING=0 (weekly snapshot only), FORMING=1, NEAR=2, BREAKOUT=3
# Stages only escalate upward — an alert fires when cur_rank > prev_rank.

STAGE_RANK = {"watching": 0, "forming": 1, "near": 2, "breakout": 3}


def state_key(symbol: str, tf_label: str, pattern_start: int) -> str:
    bucket = (pattern_start // 5) * 5
    return f"{symbol}_{tf_label}_{bucket}"


# ── Messages ──────────────────────────────────────────────────────────────────

def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


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


def build_health_message(state: dict, watching_state: dict, health: dict) -> str:
    """Build weekly health check Telegram message with full pattern snapshot table."""

    # Collect patterns by stage
    by_stage: dict[str, list] = {"breakout": [], "near": [], "forming": [], "watching": []}

    for key, info in state.items():
        if not isinstance(info, dict):
            continue
        stage = info.get("stage", "")
        if stage in by_stage:
            by_stage[stage].append(info)

    for key, info in watching_state.items():
        if isinstance(info, dict) and info.get("stage") == "watching":
            by_stage["watching"].append(info)

    # Sort each stage by squeeze descending (tightest first)
    for stage_list in by_stage.values():
        stage_list.sort(key=lambda r: r.get("squeeze", 0), reverse=True)

    total = sum(len(v) for v in by_stage.values())

    def pattern_lines(items: list) -> str:
        if not items:
            return "  none\n"
        lines = []
        for r in items:
            name    = r.get("name", "?")
            tf      = r.get("tf", "?")
            price   = r.get("price", 0)
            resist  = r.get("locked_resistance", r.get("resistance", 0))
            squeeze = r.get("squeeze", 0)
            dist    = r.get("dist_pct", 0)
            bars    = int(r.get("pattern_bars", 0))
            lines.append(
                f"  {name:<7} {tf:<10}  "
                f"${price:>10,.4f}  \u2192${resist:>10,.4f}  "
                f"{squeeze:>4.0f}% sq  {dist:>5.1f}% dist  {bars} bars"
            )
        return "\n".join(lines) + "\n"

    msg = (
        f"<b>\U0001f49a HVF Scanner \u2014 Weekly Snapshot</b>\n\n"
        f"Status:           <b>Running \u2713</b>\n"
        f"Scans run:        {health.get('scans_since_start', 0):,}\n"
        f"Signals fired:    {health.get('signals_since_start', 0):,} since start\n"
        f"Active patterns:  {total}\n"
        f"Coverage:         {len(CRYPTO_SYMBOLS)} crypto \u00b7 "
        f"{len(COMMODITY_SYMBOLS)} commodities \u00b7 1D + 1D-macro\n"
        f"{'─'*44}\n"
    )

    for stage, icon, label in [
        ("breakout", "\U0001f6a8", "BREAKOUT"),
        ("near",     "\u26a1",    "NEAR BREAKOUT"),
        ("forming",  "\U0001f4d0", "FORMING"),
        ("watching", "\U0001f441", "WATCHING  \u2014 pre-forming, not yet alerted"),
    ]:
        items = by_stage[stage]
        msg += f"\n<b>{icon} {label} ({len(items)})</b>\n"
        msg += pattern_lines(items)

    msg += f"\n<i>Next scan in ~1 hr \u00b7 {now_utc()}</i>"
    return msg


# ── Main loop ─────────────────────────────────────────────────────────────────

def run() -> None:
    n_crypto     = len(CRYPTO_SYMBOLS)
    n_commodity  = len(COMMODITY_SYMBOLS)
    logger.info(
        "HVF Scanner v4 started — %d crypto + %d commodities × 1D + 1D-macro — poll every %ds",
        n_crypto, n_commodity, POLL_SECS,
    )

    notifier      = TelegramNotifier(config.telegram_token, config.telegram_chat_id)
    state         = load_state()
    watching_state = load_watching()
    health        = load_health()

    notifier.send(
        f"<b>\U0001f50d HVF Scanner v4 started</b>\n\n"
        f"Crypto:      {n_crypto} symbols\n"
        f"Commodities: {n_commodity} symbols\n"
        f"Timeframes:  1D standard + 1D-macro\n"
        f"Data source: Yahoo Finance (no API key needed)\n"
        f"Alerts:      \U0001f4d0 Forming  \u26a1 Near  \U0001f6a8 Breakout\n"
        f"Snapshot:    \U0001f441 Watching (weekly only)\n\n"
        f"<i>{now_utc()}</i>"
    )

    all_symbols = (
        list(CRYPTO_SYMBOLS.items()) +
        list(COMMODITY_SYMBOLS.items())
    )

    while True:
        alerts_fired = 0

        for tf_key, tf_cfg in TIMEFRAME_CONFIGS.items():
            tf_label = tf_cfg["label"]

            for name, ticker in all_symbols:
                try:
                    df = fetch_candles(ticker, tf_cfg)
                    if df.empty or len(df) < 100:
                        continue

                    hvf = detect_hvf(df, tf_cfg)

                    if hvf is not None:
                        # ── FORMING+ pattern detected ──────────────────────────
                        key       = state_key(name, tf_label, hvf["pattern_start"])
                        prev_info = state.get(key)

                        # Migrate old plain-string state entries
                        if isinstance(prev_info, str):
                            prev_info = {"stage": prev_info, "locked_resistance": hvf["resistance"]}

                        prev_stage = prev_info["stage"] if prev_info else None
                        prev_rank  = STAGE_RANK.get(prev_stage, 0)

                        # Lock resistance at first detection so it can't drift upward
                        if prev_info is None:
                            locked_res = hvf["resistance"]
                        else:
                            locked_res = prev_info.get("locked_resistance", hvf["resistance"])

                        # Recompute stage using the locked resistance level
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

                        # Stage never goes backwards — keep highest reached stage
                        effective_stage = prev_stage if prev_rank >= cur_rank else cur_stage

                        if cur_rank > prev_rank:
                            # Fire live Telegram alert on escalation
                            hvf["stage"]      = cur_stage
                            hvf["dist_pct"]   = dist_pct
                            hvf["resistance"] = locked_res
                            logger.info("[%s %s] %s  dist=%.1f%%  squeeze=%.0f%%  bars=%d",
                                name, tf_label, cur_stage.upper(),
                                dist_pct * 100, hvf["contraction_pct"], hvf["pattern_bars"])
                            notifier.send(build_message(name, tf_label, hvf))
                            alerts_fired += 1

                        # Always update full snapshot data (for weekly health check)
                        state[key] = {
                            "stage":             effective_stage,
                            "locked_resistance": locked_res,
                            "support":           hvf["support"],
                            "price":             price,
                            "squeeze":           hvf["contraction_pct"],
                            "dist_pct":          dist_pct * 100,
                            "pattern_bars":      hvf["pattern_bars"],
                            "tf":                tf_label,
                            "name":              name,
                        }

                        # Immediate save on alert to prevent double-fires on restart
                        if cur_rank > prev_rank:
                            save_state(state)

                        # Pattern graduated from watching — remove watching entry
                        for wk in [k for k in list(watching_state.keys())
                                   if k.startswith(f"w_{name}_{tf_label}_")]:
                            del watching_state[wk]

                    else:
                        # ── No FORMING+ — try WATCHING ────────────────────────
                        watch_cfg  = WATCHING_CONFIGS.get(tf_key)
                        stale_wks  = [k for k in list(watching_state.keys())
                                      if k.startswith(f"w_{name}_{tf_label}_")]

                        if watch_cfg:
                            hvf_w = detect_hvf(df, watch_cfg)
                            if hvf_w is not None:
                                wkey = "w_" + state_key(name, tf_label, hvf_w["pattern_start"])
                                watching_state[wkey] = {
                                    "stage":        "watching",
                                    "name":         name,
                                    "tf":           tf_label,
                                    "resistance":   hvf_w["resistance"],
                                    "support":      hvf_w["support"],
                                    "price":        hvf_w["price"],
                                    "squeeze":      hvf_w["contraction_pct"],
                                    "dist_pct":     hvf_w["dist_pct"] * 100,
                                    "pattern_bars": hvf_w["pattern_bars"],
                                }
                            else:
                                # No watching pattern — clean up stale entries
                                for wk in stale_wks:
                                    del watching_state[wk]
                        else:
                            for wk in stale_wks:
                                del watching_state[wk]

                except Exception as exc:
                    logger.warning("[%s %s] %s", name, tf_label, exc)

        # ── End-of-scan save (persists snapshot data updates) ─────────────────
        save_state(state)
        save_watching(watching_state)

        # ── Update health counters ────────────────────────────────────────────
        health["scans_since_start"]  = health.get("scans_since_start", 0)  + 1
        health["signals_since_start"] = health.get("signals_since_start", 0) + alerts_fired

        # ── Weekly health check ───────────────────────────────────────────────
        now_ts = time.time()
        if now_ts - health.get("last_check", 0) >= HEALTH_CHECK_SECS:
            logger.info("Sending weekly health check / snapshot")
            notifier.send(build_health_message(state, watching_state, health))
            health["last_check"] = now_ts

        save_health(health)
        logger.info("Scan complete — %d alert(s) fired — sleeping %ds", alerts_fired, POLL_SECS)
        time.sleep(POLL_SECS)


if __name__ == "__main__":
    run()
