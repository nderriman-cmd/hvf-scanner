"""
HVF Live Snapshot — shows all currently active patterns across all assets,
including WATCHING (pre-forming) stage.
Run: python3 hvf_snapshot.py
"""
import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
import yfinance as yf
import sys
sys.path.insert(0, ".")
from hvf_scanner import (
    detect_hvf,
    CRYPTO_SYMBOLS, COMMODITY_SYMBOLS,
    TIMEFRAME_CONFIGS, WATCHING_CONFIGS,
)

forming_results  = []
watching_results = []

all_symbols = list(CRYPTO_SYMBOLS.items()) + list(COMMODITY_SYMBOLS.items())

print("\nRunning live HVF snapshot across all assets...\n")

for tf_key, tf_cfg in TIMEFRAME_CONFIGS.items():
    w_cfg = WATCHING_CONFIGS.get(tf_key)
    for name, ticker in all_symbols:
        try:
            raw = yf.download(ticker, period="max", interval="1d",
                              progress=False, auto_adjust=True)
            if raw.empty or len(raw) < 100:
                continue
            raw = raw.rename(columns=str.lower)
            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = raw.columns.get_level_values(0)
            raw = raw[["open","high","low","close","volume"]].dropna().reset_index(drop=True)
            window = tf_cfg["window"]
            df = raw.iloc[-window:].reset_index(drop=True)

            # Try FORMING+ first
            hvf = detect_hvf(df, tf_cfg)
            if hvf is not None:
                forming_results.append({
                    "name":     name,
                    "tf":       tf_cfg["label"],
                    "stage":    hvf["stage"],
                    "dist_pct": hvf["dist_pct"] * 100,
                    "squeeze":  hvf["contraction_pct"],
                    "pat_bars": hvf["pattern_bars"],
                    "price":    hvf["price"],
                    "resist":   hvf["resistance"],
                    "support":  hvf["support"],
                })
                print(f"  ✓ {name:<8} [{tf_cfg['label']:<12}]  {hvf['stage'].upper():<10}  "
                      f"squeeze={hvf['contraction_pct']:.0f}%  "
                      f"dist={hvf['dist_pct']*100:.1f}%  "
                      f"bars={hvf['pattern_bars']}")
            elif w_cfg is not None:
                # Try WATCHING
                hvf_w = detect_hvf(df, w_cfg)
                if hvf_w is not None:
                    watching_results.append({
                        "name":     name,
                        "tf":       tf_cfg["label"],
                        "stage":    "watching",
                        "dist_pct": hvf_w["dist_pct"] * 100,
                        "squeeze":  hvf_w["contraction_pct"],
                        "pat_bars": hvf_w["pattern_bars"],
                        "price":    hvf_w["price"],
                        "resist":   hvf_w["resistance"],
                        "support":  hvf_w["support"],
                    })
                    print(f"  👁 {name:<8} [{tf_cfg['label']:<12}]  WATCHING     "
                          f"squeeze={hvf_w['contraction_pct']:.0f}%  "
                          f"dist={hvf_w['dist_pct']*100:.1f}%  "
                          f"bars={hvf_w['pattern_bars']}")
        except Exception as e:
            pass

all_results = forming_results + watching_results

if not all_results:
    print("  No active HVF patterns detected.")
else:
    STAGE_RANK = {"watching": 0, "forming": 1, "near": 2, "breakout": 3}
    STAGE_ICON = {"forming": "📐", "near": "⚡", "breakout": "🚨", "watching": "👁 "}
    df_out = pd.DataFrame(all_results)
    df_out["rank"] = df_out["stage"].map(STAGE_RANK)
    df_out = df_out.sort_values(["rank", "squeeze"], ascending=[False, False])

    print(f"\n{'='*80}")
    print(f"  HVF LIVE SNAPSHOT — {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')} UTC")
    print(f"{'='*80}")

    for stage in ["breakout", "near", "forming", "watching"]:
        sub = df_out[df_out["stage"] == stage]
        if sub.empty:
            continue
        icon  = STAGE_ICON[stage]
        label = stage.upper()
        if stage == "watching":
            label += "  (pre-forming · 2 pivots)"
        print(f"\n  {icon} {label} ({len(sub)})")
        print(f"  {'Asset':<8} {'TF':<14} {'Price':>10}  {'Resist':>10}  "
              f"{'Support':>10}  {'Squeeze':>8}  {'Dist':>7}  {'Bars':>6}")
        print(f"  {'-'*80}")
        for _, r in sub.iterrows():
            print(f"  {r['name']:<8} {r['tf']:<14} ${r['price']:>9,.4f}  "
                  f"${r['resist']:>9,.4f}  ${r['support']:>9,.4f}  "
                  f"{r['squeeze']:>7.0f}%  {r['dist_pct']:>6.1f}%  {int(r['pat_bars']):>6}")

    print(f"\n  Total: {len(df_out)}  |  "
          f"Breakout: {(df_out['stage']=='breakout').sum()}  "
          f"Near: {(df_out['stage']=='near').sum()}  "
          f"Forming: {(df_out['stage']=='forming').sum()}  "
          f"Watching: {(df_out['stage']=='watching').sum()}")
    print(f"{'='*80}\n")
