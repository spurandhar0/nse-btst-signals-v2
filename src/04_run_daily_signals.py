"""
Script 4: Run Daily BTST Signals
===================================
Reads: db/eq_data.parquet, db/ath.parquet, config/params.json

For each of the 4 fixed configs, checks if stocks match filter criteria
AS OF THE LATEST DATE in the data.

Output:
  db/signals_C1.parquet  ...  db/signals_C4.parquet  (accumulated, deduped by SYMBOL+SIGNAL_DATE)
  output/signals_latest.csv       - deduped view (best-config-count per symbol)
  output/signals_DDMMYYYY.csv     - archive copy
  output/meta.json                - metadata for dashboard
"""

import os, json
import numpy as np
import pandas as pd
from datetime import datetime, timedelta, timezone

CONFIG_FILE = "config/params.json"
EQ_FILE     = "db/eq_data.parquet"
ATH_FILE    = "db/ath.parquet"
OUTPUT_DIR  = "output"
DB_DIR      = "db"

IST = timezone(timedelta(hours=5, minutes=30))


def load_config():
    with open(CONFIG_FILE) as f:
        return json.load(f)


def check_signal(sym_df, ath_price, cfg):
    """
    Returns signal dict if symbol matches cfg criteria on the latest date, else None.
    All pct values stored as PERCENTAGES (e.g. -7.5 means -7.5%).
    """
    days_back = cfg["days_back"]
    pct_min   = cfg["pct_min"]
    pct_max   = cfg["pct_max"]
    ath_min   = cfg["ath_min"]
    ath_max   = cfg["ath_max"]

    arr    = sym_df[["DATE1", "CLOSE_PRICE", "LOW_PRICE"]].values
    dates  = arr[:, 0]
    closes = arr[:, 1].astype(float)
    lows   = arr[:, 2].astype(float)
    n      = len(dates)

    if n < days_back + 1:
        return None

    i = n - 1
    today_close = closes[i]
    if today_close <= 0:
        return None

    lookback_lows = lows[i - days_back: i]
    if len(lookback_lows) < days_back:
        return None
    min_low = float(np.min(lookback_lows))
    if min_low <= 0:
        return None

    pct_from_low = (today_close - min_low) / min_low

    # pct_min / pct_max are stored as fractions in config (e.g. -0.1)
    if not (pct_min <= pct_from_low <= pct_max):
        return None

    pct_from_ath = (today_close - ath_price) / ath_price
    if not (ath_min <= pct_from_ath <= ath_max):
        return None

    # Date of the minimum low in the lookback window
    min_idx      = int(np.argmin(lookback_lows))
    min_5d_date  = dates[i - days_back + min_idx]

    prev_close = closes[i - 1] if i > 0 else 0.0
    pct_1d = ((today_close - prev_close) / prev_close) if prev_close > 0 else 0.0
    close_5d_ago = closes[i - 5] if i >= 5 else closes[0]
    pct_5d = round(((today_close - close_5d_ago) / close_5d_ago) * 100, 2) if close_5d_ago > 0 else 0.0

    return {
        "SIGNAL_DATE":   dates[i],
        "SIGNAL_CLOSE":  round(float(today_close), 2),
        "PREV_CLOSE":    round(float(prev_close), 2),
        "ATH_PRICE":     round(float(ath_price), 2),
        "MIN_5D_LOW":    round(min_low, 2),
        "MIN_5D_DATE":   min_5d_date,
        # stored as PERCENT (e.g. -7.5) — consistent with CSV output
        "PCT_FROM_LOW":  round(pct_from_low * 100, 2),
        "PCT_FROM_ATH":  round(pct_from_ath * 100, 2),
        "PCT_1D_CHANGE": round(pct_1d * 100, 2),
        "CHG_5D":        pct_5d,
    }


def append_config_signals(cid, new_rows, db_dir):
    """Append new signal rows to db/signals_{cid}.parquet, dedup by (SYMBOL, SIGNAL_DATE)."""
    if not new_rows:
        print(f"  {cid}: 0 signals — skipping parquet update")
        return
    parquet_path = os.path.join(db_dir, f"signals_{cid}.parquet")
    new_df = pd.DataFrame(new_rows)
    new_df["SIGNAL_DATE"] = pd.to_datetime(new_df["SIGNAL_DATE"])
    new_df["MIN_5D_DATE"] = pd.to_datetime(new_df["MIN_5D_DATE"])

    if os.path.exists(parquet_path):
        existing = pd.read_parquet(parquet_path)
        existing["SIGNAL_DATE"] = pd.to_datetime(existing["SIGNAL_DATE"])
        existing["MIN_5D_DATE"] = pd.to_datetime(existing.get("MIN_5D_DATE", pd.NaT))
        combined = pd.concat([existing, new_df], ignore_index=True)
        combined = combined.drop_duplicates(subset=["SYMBOL", "SIGNAL_DATE"], keep="last")
        combined = combined.sort_values(["SIGNAL_DATE", "SYMBOL"]).reset_index(drop=True)
    else:
        combined = new_df.sort_values(["SIGNAL_DATE", "SYMBOL"]).reset_index(drop=True)

    combined.to_parquet(parquet_path, index=False)
    print(f"  {cid}: {len(new_rows)} new → {len(combined)} total signals saved to {parquet_path}")


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(DB_DIR, exist_ok=True)

    for f in [CONFIG_FILE, EQ_FILE, ATH_FILE]:
        if not os.path.exists(f):
            print(f"❌ Missing: {f}")
            raise SystemExit(1)

    cfg     = load_config()
    configs = cfg["configs"]
    watch_syms = set(s.strip().upper() for s in cfg.get("watch_symbols", []) if s.strip())

    print(f"Loaded {len(configs)} configs:")
    for c in configs:
        print(f"  {c['id']}: days_back={c['days_back']}  pct=[{c['pct_min']},{c['pct_max']}]  ath=[{c['ath_min']},{c['ath_max']}]")

    print("\nLoading EQ data...")
    eq = pd.read_parquet(EQ_FILE, columns=["SYMBOL", "DATE1", "CLOSE_PRICE", "LOW_PRICE"])
    eq["DATE1"] = pd.to_datetime(eq["DATE1"])
    eq.sort_values(["SYMBOL", "DATE1"], inplace=True)

    if watch_syms:
        eq = eq[eq["SYMBOL"].isin(watch_syms)]
        print(f"Filtered to {len(watch_syms)} watch symbols")

    print("Loading ATH data...")
    ath_df  = pd.read_parquet(ATH_FILE, columns=["SYMBOL", "ATH_PRICE"])
    ath_map = dict(zip(ath_df["SYMBOL"], ath_df["ATH_PRICE"]))

    latest_date = eq["DATE1"].max()
    print(f"Latest data date: {latest_date.date()}")

    symbols = eq["SYMBOL"].unique()
    print(f"Scanning {len(symbols):,} symbols x {len(configs)} configs...\n")

    # Per-config result accumulation
    config_results  = {c["id"]: [] for c in configs}  # new today's signals per config
    combined_view   = {}   # SYMBOL -> best entry (for CSV dedup view)

    for sym_idx, sym in enumerate(symbols):
        if (sym_idx + 1) % 1000 == 0:
            print(f"  [{sym_idx+1}/{len(symbols)}] ...")

        ath_price = ath_map.get(sym, 0)
        if ath_price <= 0:
            continue

        sym_df = eq[eq["SYMBOL"] == sym].reset_index(drop=True)

        if sym_df["DATE1"].iloc[-1] != latest_date:
            continue  # stock doesn't have data for latest date

        matched_configs = []
        first_signal    = None

        for c in configs:
            result = check_signal(sym_df, ath_price, c)
            if result is not None:
                matched_configs.append(c["id"])
                config_results[c["id"]].append({
                    "SYMBOL": sym,
                    **result,
                })
                if first_signal is None:
                    first_signal = result

        if matched_configs and first_signal:
            combined_view[sym] = {
                "SYMBOL":          sym,
                "SIGNAL_DATE":     latest_date.strftime("%Y-%m-%d"),
                "SIGNAL_CLOSE":    first_signal["SIGNAL_CLOSE"],
                "PREV_CLOSE":      first_signal["PREV_CLOSE"],
                "ATH_PRICE":       first_signal["ATH_PRICE"],
                "MIN_5D_LOW":      first_signal["MIN_5D_LOW"],
                "PCT_FROM_LOW":    first_signal["PCT_FROM_LOW"],
                "PCT_FROM_ATH":    first_signal["PCT_FROM_ATH"],
                "PCT_1D_CHANGE":   first_signal["PCT_1D_CHANGE"],
                "CHG_5D":          first_signal.get("CHG_5D", 0.0),
                "CONFIGS_MATCHED": ",".join(matched_configs),
                "CONFIG_COUNT":    len(matched_configs),
            }

    # ── Save per-config signal parquets ──────────────────────────────────────
    print("\nSaving per-config signal parquets...")
    for c in configs:
        append_config_signals(c["id"], config_results[c["id"]], DB_DIR)

    # ── Save CSV outputs (deduped by symbol) ─────────────────────────────────
    rows = sorted(combined_view.values(), key=lambda x: (-x["CONFIG_COUNT"], x["SYMBOL"]))
    print(f"\n✅ {len(rows)} unique symbols matched (combined view)")

    df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=[
        "SYMBOL", "SIGNAL_DATE", "SIGNAL_CLOSE", "PREV_CLOSE", "ATH_PRICE",
        "MIN_5D_LOW", "PCT_FROM_LOW", "PCT_FROM_ATH", "PCT_1D_CHANGE", "CHG_5D",
        "CONFIGS_MATCHED", "CONFIG_COUNT"
    ])

    date_str = latest_date.strftime("%d%m%Y")
    df.to_csv(f"{OUTPUT_DIR}/signals_{date_str}.csv", index=False)
    df.to_csv(f"{OUTPUT_DIR}/signals_latest.csv", index=False)
    print(f"✅ Saved output/signals_latest.csv")
    print(f"✅ Saved output/signals_{date_str}.csv")

    # ── Save meta.json ────────────────────────────────────────────────────────
    config_breakdown = {}
    for c in configs:
        config_breakdown[c["id"]] = len(config_results[c["id"]])

    meta = {
        "generated_at":     datetime.now(tz=IST).strftime("%d-%b-%Y %H:%M IST"),
        "signal_date":      latest_date.strftime("%d-%b-%Y"),
        "total_signals":    len(rows),
        "config_breakdown": config_breakdown,
        "configs":          configs,
    }
    with open(f"{OUTPUT_DIR}/meta.json", "w") as f:
        json.dump(meta, f, indent=2, default=str)
    print(f"✅ Saved output/meta.json")

    print(f"\n{'='*60}")
    print(f"SIGNAL SUMMARY - {latest_date.strftime('%d-%b-%Y')}")
    print(f"{'='*60}")
    print(f"Total unique signals : {len(rows)}")
    for cid, cnt in config_breakdown.items():
        print(f"  {cid}              : {cnt}")
    if rows:
        print(f"\nTop signals:")
        for r in rows[:10]:
            print(f"  {r['SYMBOL']:15s} close={r['SIGNAL_CLOSE']:8.2f}  "
                  f"pct_low={r['PCT_FROM_LOW']:+.1f}%  "
                  f"pct_ath={r['PCT_FROM_ATH']:+.1f}%  "
                  f"configs={r['CONFIGS_MATCHED']}")


if __name__ == "__main__":
    main()
