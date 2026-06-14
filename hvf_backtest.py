"""
HVF Signal Quality Backtest — v3
==================================
Validates HVF detection on 2H and 1D timeframes across crypto + commodities.

Known HVF examples used for calibration:
  Gold       Apr 08 – Aug 27 2025  2H   ~141 days / ~1,700 bars
  XRP        May 04 – Dec 11 2017  1D    221 bars
  Platinum   Mar 27 2020 – May 16 2025  1D  ~1,877 bars (macro)
  Platinum   Dec 19 2025 – Jan 20 2026  2H  ~32 days / ~384 bars
  Tron       Nov 25 2024 – Apr 06 2026  1D  497 bars, still forming

Run:
  python hvf_backtest.py
"""

import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
from pybit.unified_trading import HTTP

from config import config

try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False
    print("WARNING: yfinance not installed — commodity validation skipped. pip install yfinance")

# ── Parameters ────────────────────────────────────────────────────────────────

CRYPTO_SYMBOLS = [
    "BTCUSDT",  "ETHUSDT",  "BNBUSDT",  "SOLUSDT",  "XRPUSDT",
    "DOGEUSDT", "ADAUSDT",  "AVAXUSDT", "TRXUSDT",  "LINKUSDT",
    "DOTUSDT",  "LTCUSDT",  "NEARUSDT", "UNIUSDT",  "APTUSDT",
    "OPUSDT",   "ARBUSDT",  "SUIUSDT",  "ATOMUSDT", "HYPEUSDT",
    "TAOUSDT",
]

COMMODITY_SYMBOLS = {
    "GOLD":  "GC=F",
    "PLAT":  "PL=F",
    "SILVER":"SI=F",
    "OIL":   "CL=F",
}

TIMEFRAME_CONFIGS = {
    # Standard 2H — catches TAO (129 bars), Gold 141-day structures
    "120": {
        "label":        "2H",
        "pivot_lb":     10,
        "min_pat_bars": 80,
        "atr_contract": 0.25,
        "window":       600,
        "stem_window":  500,   # look for stem in most recent 500 bars (~42 days)
        "near_pct":     0.05,
        "lookback_days":500,
        "bybit_interval":"120",
        "yf_interval":  "1h",
        "yf_period":    "730d",
        "cooldown_bars":80,
        "outcome_bars": 80,
    },
    # Tight 2H — catches short squeezes like HYPE June 9-15 (~41 bar compression)
    "120T": {
        "label":           "2H-tight",
        "pivot_lb":        3,     # 3 bars each side = 6H — tighter pivot detection
        "min_pat_bars":    40,    # ≥40 bars (~3.3 days) — HYPE fires at 43 bars, still passes
        "min_pivots_h":    3,     # 3 descending resistance pivots — stronger confirmation
        "min_pivots_l":    2,     # 2 ascending support pivots — accommodates wick-style lows
        "atr_contract":    0.05,  # 5% — wick events re-inflate ATR; body/structure are primary filters
        "body_contract":   0.25,  # 25% body squeeze (HYPE ~31%, strict vs standard 15%)
        "strict_slopes":   True,  # require ph_slope<0 AND pl_slope>0 — rejects falling channels
        "window":          200,
        "stem_window":     150,   # stem must be within last 150 bars (~12 days)
        "near_pct":        0.04,
        "lookback_days":   500,
        "bybit_interval":  "120",
        "yf_interval":     "1h",
        "yf_period":       "730d",
        "cooldown_bars":   40,    # space signals out more (was 25)
        "outcome_bars":    40,
        "crypto_only":     True,  # commodities too slow/smooth for tight 2H patterns
    },
    # Standard 1D — catches BNB (67 bars), XRP 221-bar, longer crypto structures
    "D": {
        "label":        "1D",
        "pivot_lb":     5,
        "min_pat_bars": 50,
        "atr_contract": 0.20,
        "window":       300,
        "stem_window":  200,   # stem within last 200 daily bars (~7 months)
        "near_pct":     0.05,
        "lookback_days":1000,
        "bybit_interval":"D",
        "yf_interval":  "1d",
        "yf_period":    "10y",
        "cooldown_bars":30,
        "outcome_bars": 30,
        "strict_mono":  False,  # ascending triangles / coiling-at-resistance pass
    },
    # Macro 1D — catches TRON 489-bar, Platinum 5-year, mega compression structures
    # pivot_lb=10 filters micro-oscillations → only major structural pivots survive
    # stem_window=1000 looks up to ~4 years back for the spike/stem origin
    # min_pat_bars=150 ensures at least 5 months of compression before signalling
    "D-macro": {
        "label":        "1D-macro",
        "pivot_lb":     10,    # 10-day each side = major structural pivots only
        "min_pat_bars": 150,   # ≥150 bars (~5 months) — separates from standard 1D
        "atr_contract": 0.40,  # strong squeeze required — macro patterns compress deeply
        "window":       1200,  # full history window
        "stem_window":  1000,  # stem can be up to ~4 years ago
        "near_pct":     0.05,
        "lookback_days":2000,
        "bybit_interval":"D",
        "yf_interval":  "1d",
        "yf_period":    "10y",
        "cooldown_bars":150,   # only one signal per mega-pattern
        "outcome_bars": 120,   # 4-month outcome window
        "strict_mono":  False, # mandatory — long patterns oscillate mid-compression
        "zone_mid_pct": 0.25,  # allow deep retracements (TRON -52%, Platinum -40%)
    },
}

MIN_PIVOTS = 3   # 3 confirmed compression pivots each side for reliable trendlines


# ── HVF detection (self-contained) ───────────────────────────────────────────

def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat(
        [h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def pivot_highs(high: pd.Series, lb: int) -> np.ndarray:
    arr, idx = high.values, []
    for i in range(lb, len(arr) - lb):
        if (all(arr[i] > arr[i - j] for j in range(1, lb + 1)) and
                all(arr[i] > arr[i + j] for j in range(1, lb + 1))):
            idx.append(i)
    return np.array(idx, dtype=int)


def pivot_lows(low: pd.Series, lb: int) -> np.ndarray:
    arr, idx = low.values, []
    for i in range(lb, len(arr) - lb):
        if (all(arr[i] < arr[i - j] for j in range(1, lb + 1)) and
                all(arr[i] < arr[i + j] for j in range(1, lb + 1))):
            idx.append(i)
    return np.array(idx, dtype=int)


def detect_hvf(df: pd.DataFrame, tf_cfg: dict):
    """
    HVF detection built around the STEM HIGH concept:

      1. Find the STEM HIGH — earliest pivot high at ≥95% of the window peak.
         This is where the HVF compression begins (e.g. BNB March 15 2024).
      2. Look only at pivot highs/lows AFTER the stem — the compression zone.
         Need at least MIN_PIVOTS of each for trendlines.
      3. Trendlines must show descending highs, ascending lows, interleave, converge.
      4. ATR and body compression measured from the stem for a full-pattern view.
    """
    lb           = tf_cfg["pivot_lb"]
    min_pat      = tf_cfg["min_pat_bars"]
    atr_contract = tf_cfg["atr_contract"]
    n            = len(df)

    if n < lb * 2 + 20:
        return None

    all_ph_idx = pivot_highs(df["high"], lb)
    all_pl_idx = pivot_lows(df["low"],   lb)

    if len(all_ph_idx) < 1 or len(all_pl_idx) < MIN_PIVOTS:
        return None

    # ── 1. Find the STEM HIGH ─────────────────────────────────────────────────
    #   The earliest pivot high at or near the window peak (≥95% of window_high).
    #   Use stem_window: find the HIGHEST pivot in the most recent N bars.
    #   This prevents a distant rally (e.g. May) from overshadowing a recent
    #   stem (e.g. Oct) and gives each timeframe scale-appropriate detection.
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

    # ── 2b. First post-stem low must close above the zone_mid_pct of the stem move
    #   Default: 50% (classic HVF — compression stays in upper half of stem range)
    #   Macro mode: 25% — allows deep retracements (TRON -52%, Platinum -40%)
    #   Uses CLOSE not wick so flash-crash wicks don't disqualify (e.g. HYPE Jun 13)
    stem_high  = float(df["high"].iloc[stem_idx])
    pre_low    = float(df["low"].iloc[max(0, stem_idx - 200) : stem_idx + 1].min())
    zone_mid_pct   = tf_cfg.get("zone_mid_pct", 0.50)
    zone_mid   = pre_low + (stem_high - pre_low) * zone_mid_pct
    first_pl_close = float(df["close"].iloc[post_pl[0]])
    if first_pl_close < zone_mid:
        return None

    # ── 3-6. Trendline fitting — strict vs relaxed mode ─────────────────────
    #
    # strict_mono=True  (default — standard 2H, tight 2H):
    #   Uses last min_piv_h highs / min_piv_l lows. Requires strictly descending
    #   highs AND ascending lows between every consecutive pair. Classic wedge.
    #
    # strict_mono=False (1D):
    #   Uses ALL post-stem pivots for polyfit. Skips consecutive-pair checks.
    #   Only requires pl_slope > ph_slope (convergence) and pl_slope > 0 (rising
    #   support). Catches ascending triangles and "coiling at resistance" patterns
    #   like Gold Apr-Aug 2025: rising lows + roughly flat resistance → breakout.
    #
    strict_mono = tf_cfg.get("strict_mono", True)

    if strict_mono:
        ph_idx    = post_ph[-min_piv_h:]
        pl_idx    = post_pl[-min_piv_l:]
        ph_prices = df["high"].iloc[ph_idx].values.astype(float)
        pl_prices = df["low"].iloc[pl_idx].values.astype(float)

        # Strictly descending highs
        if not all(ph_prices[j] < ph_prices[j - 1] for j in range(1, len(ph_prices))):
            return None
        # Strictly ascending lows
        if not all(pl_prices[j] > pl_prices[j - 1] for j in range(1, len(pl_prices))):
            return None
        # Interleave — pivot groups must overlap in time
        if max(ph_idx[0], pl_idx[0]) >= min(ph_idx[-1], pl_idx[-1]):
            return None
    else:
        # Relaxed mode — use all post-stem pivots, trust the polyfit
        ph_idx    = post_ph
        pl_idx    = post_pl
        ph_prices = df["high"].iloc[ph_idx].values.astype(float)
        pl_prices = df["low"].iloc[pl_idx].values.astype(float)

    # ── 6. Convergence — resistance falling, support rising ──────────────────
    ph_slope, ph_intercept = np.polyfit(ph_idx.astype(float), ph_prices, 1)
    pl_slope, pl_intercept = np.polyfit(pl_idx.astype(float), pl_prices, 1)
    if pl_slope <= ph_slope:
        return None
    # Relaxed mode still requires support to be actively rising (ascending triangle
    # qualifies; a falling support line in a downtrend does not)
    if not strict_mono and pl_slope <= 0:
        return None
    # Optional strict slope check — rejects falling channels (tight 2H mode)
    if tf_cfg.get("strict_slopes", False):
        if ph_slope >= 0 or pl_slope <= 0:
            return None

    # ── 7. ATR contraction — from the stem high to now ───────────────────────
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
    if cur - stem_idx < min_pat:
        return None

    # ── 9. Candle body compression from stem to now ───────────────────────────
    #   Bodies must visibly shrink from the stem high to the current bar.
    #   Threshold is configurable (tight mode uses 0.22, standard uses 0.15).
    body_threshold = tf_cfg.get("body_contract", 0.15)
    pat_df     = df.iloc[stem_idx : cur + 1]
    body_sizes = (pat_df["close"] - pat_df["open"]).abs().values.astype(float)
    if len(body_sizes) > 4:
        third      = max(1, len(body_sizes) // 3)
        body_early = body_sizes[:third].mean()
        body_late  = body_sizes[-third:].mean()
        if body_early == 0:
            return None
        if (1.0 - body_late / body_early) < body_threshold:
            return None

    # ── 9b. Volume contraction across the funnel body ─────────────────────────
    #   Measured over the funnel BODY only (excludes the stem spike at the front
    #   and the breakout-approach ramp at the back). Data-quality aware. Mirrors
    #   hvf_scanner.detect_hvf so the backtest tests the live volume filter.
    vol_contraction_pct = None
    pat_df = df.iloc[stem_idx : cur + 1]
    if "volume" in pat_df.columns and len(pat_df) >= 12:
        vol = pat_df["volume"].astype(float).values
        if (vol > 0).mean() > 0.70:
            lo       = max(1, int(len(vol) * 0.10))
            hi       = max(lo + 4, int(len(vol) * 0.90))
            body_vol = vol[lo:hi]
            if len(body_vol) >= 4:
                mid     = len(body_vol) // 2
                first_h = body_vol[:mid].mean()
                last_h  = body_vol[mid:].mean()
                if first_h > 0:
                    vol_contraction = 1.0 - (last_h / first_h)
                    vol_contraction_pct = vol_contraction * 100
                    if vol_contraction < tf_cfg.get("vol_contract", 0.0):
                        return None

    resistance = float(ph_slope * cur + ph_intercept)
    support    = float(pl_slope * cur + pl_intercept)
    price      = float(df["close"].iloc[-1])

    if price > resistance * 1.08 or price < support * 0.92:
        return None

    return {
        "resistance":      resistance,
        "support":         support,
        "price":           price,
        "dist_pct":        (resistance - price) / price,
        "contraction_pct": contraction * 100,
        "vol_contraction_pct": vol_contraction_pct,
        "pattern_start":   stem_idx,
        "pattern_bars":    cur - stem_idx,
    }


# ── Data fetching ─────────────────────────────────────────────────────────────

def fetch_bybit(session, symbol: str, interval: str, days: int) -> pd.DataFrame:
    end_ms   = int(datetime.utcnow().timestamp() * 1000)
    start_ms = end_ms - days * 24 * 3600 * 1000
    all_rows, cur_end = [], end_ms

    while cur_end > start_ms:
        try:
            resp = session.get_kline(
                category="linear", symbol=symbol, interval=interval,
                limit=1000, end=cur_end,
            )
        except Exception as exc:
            print(f"    API error: {exc}")
            break
        rows = resp["result"]["list"]
        if not rows:
            break
        all_rows.extend(rows)
        oldest = int(rows[-1][0])
        if oldest <= start_ms:
            break
        cur_end = oldest - 1
        time.sleep(0.08)

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows, columns=["timestamp","open","high","low","close","volume","_t"])
    df = df.drop(columns=["_t"])
    df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="ms")
    for col in ("open","high","low","close","volume"):
        df[col] = df[col].astype(float)
    df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    start_dt = datetime.utcnow() - timedelta(days=days)
    df = df[df["timestamp"] >= pd.Timestamp(start_dt)].reset_index(drop=True)
    return df.iloc[:-1].reset_index(drop=True)


def fetch_yfinance(ticker: str, tf_cfg: dict) -> pd.DataFrame:
    if not YFINANCE_AVAILABLE:
        return pd.DataFrame()
    try:
        raw = yf.download(
            ticker, period=tf_cfg["yf_period"], interval=tf_cfg["yf_interval"],
            progress=False, auto_adjust=True,
        )
        if raw.empty:
            return pd.DataFrame()
        raw = raw.rename(columns=str.lower)
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        raw = raw[["open","high","low","close","volume"]].dropna()
        if tf_cfg["yf_interval"] == "1h":
            raw = raw.resample("2h").agg(
                {"open":"first","high":"max","low":"min","close":"last","volume":"sum"}
            ).dropna()
        raw = raw.reset_index()
        ts_col = raw.columns[0]
        raw = raw.rename(columns={ts_col: "timestamp"})
        return raw.reset_index(drop=True)
    except Exception as exc:
        print(f"    yfinance error for {ticker}: {exc}")
        return pd.DataFrame()


# ── Rolling backtest ──────────────────────────────────────────────────────────

def backtest_symbol(df: pd.DataFrame, symbol: str, tf_cfg: dict) -> list:
    window        = tf_cfg["window"]
    cooldown      = tf_cfg["cooldown_bars"]
    outcome_bars  = tf_cfg["outcome_bars"]
    signals       = []
    last_bar      = -(cooldown + 1)
    n             = len(df)

    for i in range(window, n - outcome_bars - 1):
        if i - last_bar < cooldown:
            continue
        win = df.iloc[i - window + 1 : i + 1].reset_index(drop=True)
        hvf = detect_hvf(win, tf_cfg)
        if hvf is None:
            continue

        entry_bar   = i + 1
        entry_price = float(df["open"].iloc[entry_bar])
        ts          = df["timestamp"].iloc[i] if "timestamp" in df.columns else i

        hi_fwd = df["high"].iloc[entry_bar : entry_bar + outcome_bars + 1].max()
        c_half = df["close"].iloc[min(entry_bar + outcome_bars // 2, n - 1)]
        c_full = df["close"].iloc[min(entry_bar + outcome_bars, n - 1)]

        signals.append({
            "symbol":   symbol,
            "tf":       tf_cfg["label"],
            "ts":       str(ts)[:16],
            "entry":    entry_price,
            "gain_mid": (c_half - entry_price) / entry_price * 100,
            "gain_end": (c_full - entry_price) / entry_price * 100,
            "max_gain": (hi_fwd - entry_price) / entry_price * 100,
            "squeeze":  hvf["contraction_pct"],
            "pat_bars": hvf["pattern_bars"],
        })
        last_bar = i

    return signals


# ── Reporting ─────────────────────────────────────────────────────────────────

def print_results(all_signals: list, tf_label: str) -> None:
    if not all_signals:
        print(f"  No signals on {tf_label}")
        return

    df = pd.DataFrame(all_signals)
    total = len(df)
    wr_mid = (df["gain_mid"] > 0).mean() * 100
    wr_end = (df["gain_end"] > 0).mean() * 100
    avg_max = df["max_gain"].mean()
    avg_end = df["gain_end"].mean()
    gt5  = (df["max_gain"] > 5).sum()
    gt10 = (df["max_gain"] > 10).sum()
    gt20 = (df["max_gain"] > 20).sum()

    print(f"\n  {'Symbol':<12} {'Date':<17} {'Entry':>12}  {'Mid':>7}  {'End':>7}  {'Max':>7}  {'Bars':>5}")
    print(f"  {'-'*72}")
    for _, r in df.iterrows():
        print(f"  {r['symbol']:<12} {r['ts']:<17} ${r['entry']:>11,.4f}  "
              f"{r['gain_mid']:>+6.1f}%  {r['gain_end']:>+6.1f}%  {r['max_gain']:>+6.1f}%  {int(r['pat_bars']):>5}")

    print(f"\n  Total signals : {total}")
    print(f"  Win rate mid  : {wr_mid:.0f}%")
    print(f"  Win rate end  : {wr_end:.0f}%")
    print(f"  Avg max gain  : {avg_max:+.1f}%")
    print(f"  Avg end gain  : {avg_end:+.1f}%")
    print(f"  Max >5%       : {gt5}  ({gt5/total*100:.0f}%)")
    print(f"  Max >10%      : {gt10}  ({gt10/total*100:.0f}%)")
    print(f"  Max >20%      : {gt20}  ({gt20/total*100:.0f}%)")
    print(f"  Avg squeeze   : {df['squeeze'].mean():.0f}%")
    print(f"  Avg pat bars  : {df['pat_bars'].mean():.0f}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    session = HTTP(testnet=False, api_key=config.api_key, api_secret=config.api_secret)

    for tf, tf_cfg in TIMEFRAME_CONFIGS.items():
        tf_label = tf_cfg["label"]
        print(f"\n{'='*70}")
        print(f"  HVF Backtest — {tf_label} — pivot_lb={tf_cfg['pivot_lb']} "
              f"min_pat={tf_cfg['min_pat_bars']} atr≥{tf_cfg['atr_contract']*100:.0f}%")
        print(f"{'='*70}")

        all_signals = []

        # Crypto
        print(f"\n  [CRYPTO — {tf_label}]")
        for symbol in CRYPTO_SYMBOLS:
            print(f"    {symbol:<12}", end=" ", flush=True)
            try:
                df = fetch_bybit(session, symbol, tf_cfg["bybit_interval"], tf_cfg["lookback_days"])
                if df.empty or len(df) < tf_cfg["window"] + tf_cfg["outcome_bars"] + 10:
                    print(f"insufficient data ({len(df)} bars)")
                    continue
                sigs = backtest_symbol(df, symbol, tf_cfg)
                all_signals.extend(sigs)
                print(f"{len(df)} bars  →  {len(sigs)} signals")
            except Exception as exc:
                print(f"error — {exc}")

        # Commodities — skipped for crypto_only configs (e.g. tight 2H)
        if YFINANCE_AVAILABLE and not tf_cfg.get("crypto_only", False):
            print(f"\n  [COMMODITIES — {tf_label}]")
            for name, ticker in COMMODITY_SYMBOLS.items():
                print(f"    {name:<12}", end=" ", flush=True)
                try:
                    df = fetch_yfinance(ticker, tf_cfg)
                    if df.empty or len(df) < 100:
                        print(f"insufficient data")
                        continue
                    sigs = backtest_symbol(df, name, tf_cfg)
                    all_signals.extend(sigs)
                    print(f"{len(df)} bars  →  {len(sigs)} signals")
                except Exception as exc:
                    print(f"error — {exc}")

        print_results(all_signals, tf_label)

    print("\n")


if __name__ == "__main__":
    main()
