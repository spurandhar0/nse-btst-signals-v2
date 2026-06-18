"""
Script 00b: Historical Signal Backfill
========================================
Scans ALL trading dates from SIGNAL_START_DATE onwards and generates
BTST signals for each date using the 4 fixed configs.

Reads:  db/eq_data.parquet, db/ath.parquet, config/params.json
Output: db/signals_C1.parquet ... db/signals_C4.parquet  (deduplicated)
        output/signals_latest.csv
        output/meta.json

Supports checkpoint/resume for long runs (10 h+):
  - Saves progress to db/backfill_checkpoint.json after each date
  - On restart, skips already-processed dates automatically
  - Exits with code 2 if time budget exceeded (partial run)
  - Exits with code 0 when fully complete

SIGNAL_START_DATE can be overridden via environment variable:
  SIGNAL_START_DATE=2025-01-01 python src/00b_historical_backfill.py

BACKFILL_TIME_BUDGET_MINUTES: max runtime before saving + pausing (default: 300)
"""

import os, json, sys, time
import numpy as np
import pandas as pd
from datetime import datetime, timedelta, timezone

CONFIG_FILE     = "config/params.json"
EQ_FILE         = "db/eq_data.parquet"
ATH_FILE        = "db/ath.parquet"
OUTPUT_DIR      = "output"
DB_DIR          = "db"
CHECKPOINT_FILE = "db/backfill_checkpoint.json"

IST = timezone(timedelta(hours=5, minutes=30))

# Read from env var (set by workflow), fallback to 2025-01-01
_env_start = os.environ.get("SIGNAL_START_DATE", "2025-01-01")
SIGNAL_START_DATE = pd.Timestamp(_env_start)
print(f"Signal scan start date: {SIGNAL_START_DATE.date()}")

# Time budget in minutes before saving progress and pausing (default 300 = 5 h)
TIME_BUDGET_MINUTES = int(os.environ.get("BACKFILL_TIME_BUDGET_MINUTES", "300"))


def load_config():
    with open(CONFIG_FILE) as f:
        return json.load(f)


def load_checkpoint():
    """Return set of already-completed date strings (YYYY-MM-DD)."""
    if os.path.exists(CHECKPOINT_FILE):
        with open(CHECKPOINT_FILE) as f:
            data = json.load(f)
        completed = set(data.get("completed_dates", []))
        print(f"Checkpoint loaded: {len(completed)} dates already done")
        return completed
    return set()


def save_checkpoint(completed_dates):
    """Persist set of completed date strings to checkpoint file."""
    os.makedirs(DB_DIR, exist_ok=True)
    with open(CHECKPOINT_FILE, "w") as f:
        json.dump({"completed_dates": sorted(completed_dates)}, f)


def save_config_parquet(cid, rows, db_dir):
    """Save / merge signal rows to db/signals_{cid}.parquet."""
    if not rows:
        print(f"  {cid}: 0 signals")
        return 0
    parquet_path = os.path.join(db_dir, f"signals_{cid}.parquet")
    new_df = pd.DataFrame(rows)
    new_df["SIGNAL_DATE"] = pd.to_datetime(new_df["SIGNAL_DATE"])
    new_df["MIN_5D_DATE"] = pd.to_datetime(new_df["MIN_5D_DATE"])

    if os.path.exists(parquet_path):
        existing = pd.read_parquet(parquet_path)
        existing["SIGNAL_DATE"] = pd.to_datetime(existing["SIGNAL_DATE"])
        existing["MIN_5D_DATE"] = pd.to_datetime(existing.get("MIN_5D_DATE",
                                                  pd.Series([], dtype="object")))
        combined = pd.concat([existing, new_df], ignore_index=True)
        combined = combined.drop_duplicates(subset=["SYMBOL", "SIGNAL_DATE"], keep="last")
    else:
        combined = new_df

    combined = combined.sort_values(["SIGNAL_DATE", "SYMBOL"]).reset_index(drop=True)
    combined.to_parquet(parquet_path, index=False)
    print(f"  {cid}: {len(rows):,} signals → {len(combined):,} total in parquet")
    return len(rows)


def write_latest_outputs(configs, all_dates, db_dir, output_dir,
                         all_signals=None, total=0):
    """Write signals_latest.csv and meta.json (from in-memory or parquet)."""
    latest_date = all_dates[-1]

    if all_signals is None:
        # Resume-complete path: load from parquets
        all_signals = {}
        for c in configs:
            parquet_path = os.path.join(db_dir, f"signals_{c['id']}.parquet")
            if os.path.exists(parquet_path):
                df = pd.read_parquet(parquet_path)
                df["SIGNAL_DATE"] = pd.to_datetime(df["SIGNAL_DATE"])
                rows = df[df["SIGNAL_DATE"] == latest_date].to_dict("records")
                all_signals[c["id"]] = rows
            else:
                all_signals[c["id"]] = []

    combined_view = {}
    for c in configs:
        for row in all_signals[c["id"]]:
            if pd.Timestamp(row["SIGNAL_DATE"]) == latest_date:
                sym = row["SYMBOL"]
                if sym not in combined_view:
                    combined_view[sym] = {
                        "SYMBOL":          sym,
                        "SIGNAL_DATE":     latest_date.strftime("%Y-%m-%d"),
                        "SIGNAL_CLOSE":    row["SIGNAL_CLOSE"],
                        "PREV_CLOSE":      row["PREV_CLOSE"],
                        "ATH_PRICE":       row["ATH_PRICE"],
                        "MIN_5D_LOW":      row["MIN_5D_LOW"],
                        "PCT_FROM_LOW":    row["PCT_FROM_LOW"],
                        "PCT_FROM_ATH":    row["PCT_FROM_ATH"],
                        "PCT_1D_CHANGE":   row["PCT_1D_CHANGE"],
                        "CONFIGS_MATCHED": c["id"],
                        "CONFIG_COUNT":    1,
                    }
                else:
                    combined_view[sym]["CONFIGS_MATCHED"] += "," + c["id"]
                    combined_view[sym]["CONFIG_COUNT"]    += 1

    rows_list = sorted(combined_view.values(),
                       key=lambda x: (-x["CONFIG_COUNT"], x["SYMBOL"]))
    df_out = pd.DataFrame(rows_list) if rows_list else pd.DataFrame(columns=[
        "SYMBOL", "SIGNAL_DATE", "SIGNAL_CLOSE", "PREV_CLOSE", "ATH_PRICE",
        "MIN_5D_LOW", "PCT_FROM_LOW", "PCT_FROM_ATH", "PCT_1D_CHANGE",
        "CONFIGS_MATCHED", "CONFIG_COUNT"
    ])

    date_str = latest_date.strftime("%d%m%Y")
    df_out.to_csv(f"{output_dir}/signals_{date_str}.csv", index=False)
    df_out.to_csv(f"{output_dir}/signals_latest.csv", index=False)

    config_breakdown = {c["id"]: len(all_signals[c["id"]]) for c in configs}
    meta = {
        "generated_at":     datetime.now(tz=IST).strftime("%d-%b-%Y %H:%M IST"),
        "signal_date":      latest_date.strftime("%d-%b-%Y"),
        "total_signals":    len(rows_list),
        "config_breakdown": config_breakdown,
        "configs":          configs,
        "backfill":         True,
        "backfill_from":    str(SIGNAL_START_DATE.date()),
        "backfill_dates":   len(all_dates),
    }
    with open(f"{output_dir}/meta.json", "w") as f:
        json.dump(meta, f, indent=2, default=str)

    print(f"\n{'='*60}")
    print(f"BACKFILL COMPLETE")
    print(f"{'='*60}")
    print(f"Dates scanned    : {len(all_dates)}")
    print(f"Total signals    : {total:,}")
    for cid, cnt in config_breakdown.items():
        print(f"  {cid}             : {cnt:,}")
    print(f"Latest date sigs : {len(rows_list)}")


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(DB_DIR, exist_ok=True)

    for f in [CONFIG_FILE, EQ_FILE, ATH_FILE]:
        if not os.path.exists(f):
            print(f"❌ Missing: {f}")
            raise SystemExit(1)

    cfg     = load_config()
    configs = cfg["configs"]
    print(f"Loaded {len(configs)} configs")

    # ── Load price data ─────────────────────────────────────────────────────
    print("Loading EQ data...")
    eq = pd.read_parquet(EQ_FILE, columns=["SYMBOL", "DATE1", "CLOSE_PRICE", "LOW_PRICE"])
    eq["DATE1"] = pd.to_datetime(eq["DATE1"])
    eq.sort_values(["SYMBOL", "DATE1"], inplace=True)
    eq.reset_index(drop=True, inplace=True)
    print(f"  {len(eq):,} rows | {eq['SYMBOL'].nunique():,} symbols")
    print(f"  Date range: {eq['DATE1'].min().date()} → {eq['DATE1'].max().date()}")

    print("Loading ATH data...")
    ath_df  = pd.read_parquet(ATH_FILE, columns=["SYMBOL", "ATH_PRICE"])
    ath_map = dict(zip(ath_df["SYMBOL"], ath_df["ATH_PRICE"]))

    # ── All trading dates from SIGNAL_START_DATE ────────────────────────────
    all_dates = sorted(d for d in eq["DATE1"].unique() if d >= SIGNAL_START_DATE)
    if not all_dates:
        print(f"❌ No dates found from {SIGNAL_START_DATE.date()} onwards in eq_data!")
        raise SystemExit(1)

    print(f"\nTotal trading dates from start: {len(all_dates)} "
          f"({all_dates[0].date()} → {all_dates[-1].date()})")

    # ── Resume from checkpoint ──────────────────────────────────────────────
    completed_dates = load_checkpoint()
    remaining_dates = [d for d in all_dates if str(d)[:10] not in completed_dates]
    skipped = len(all_dates) - len(remaining_dates)
    print(f"Skipping {skipped} already-processed dates | Remaining: {len(remaining_dates)} dates")
    print(f"Time budget: {TIME_BUDGET_MINUTES} min\n")

    if not remaining_dates:
        print("✅ All dates already processed — writing final outputs")
        if os.path.exists(CHECKPOINT_FILE):
            os.remove(CHECKPOINT_FILE)
        write_latest_outputs(configs, all_dates, DB_DIR, OUTPUT_DIR)
        return

    # ── Per-symbol pre-group (fast lookup) ──────────────────────────────────
    print("Pre-grouping by symbol...")
    groups = {}
    for sym, grp in eq.groupby("SYMBOL", sort=False):
        g = grp.sort_values("DATE1").reset_index(drop=True)
        groups[sym] = {
            "dates":  g["DATE1"].values,
            "closes": g["CLOSE_PRICE"].values.astype(float),
            "lows":   g["LOW_PRICE"].values.astype(float),
        }
    symbols = list(groups.keys())
    print(f"  {len(symbols):,} symbols pre-grouped\n")

    # ── Time budget ─────────────────────────────────────────────────────────
    start_time     = time.time()
    budget_seconds = TIME_BUDGET_MINUTES * 60

    # ── Scan remaining dates ─────────────────────────────────────────────────
    all_signals = {c["id"]: [] for c in configs}
    partial     = False

    for date_i, date in enumerate(remaining_dates):
        if (date_i + 1) % 20 == 0 or date_i == 0 or date_i == len(remaining_dates) - 1:
            elapsed_min = (time.time() - start_time) / 60
            counts = {c["id"]: len(all_signals[c["id"]]) for c in configs}
            print(f"  [{date_i+1:3d}/{len(remaining_dates)}] {date.date()} | "
                  f"signals: {counts} | elapsed: {elapsed_min:.1f} min")

        for sym in symbols:
            g         = groups[sym]
            dates     = g["dates"]
            closes    = g["closes"]
            lows      = g["lows"]
            ath_price = ath_map.get(sym, 0)
            if ath_price <= 0:
                continue

            idx = np.searchsorted(dates, date, side="right") - 1
            if idx < 0 or dates[idx] != date:
                continue

            today_close = closes[idx]
            if today_close <= 0:
                continue

            for cfg in configs:
                days_back = cfg["days_back"]
                pct_min   = cfg["pct_min"]
                pct_max   = cfg["pct_max"]
                ath_min   = cfg["ath_min"]
                ath_max   = cfg["ath_max"]

                if idx < days_back:
                    continue

                lookback_lows = lows[idx - days_back: idx]
                min_low = float(np.min(lookback_lows))
                if min_low <= 0:
                    continue

                pct_from_low = (today_close - min_low) / min_low
                if not (pct_min <= pct_from_low <= pct_max):
                    continue

                pct_from_ath = (today_close - ath_price) / ath_price
                if not (ath_min <= pct_from_ath <= ath_max):
                    continue

                min_idx     = int(np.argmin(lookback_lows))
                min_5d_date = dates[idx - days_back + min_idx]
                prev_close  = closes[idx - 1] if idx > 0 else 0.0
                pct_1d      = ((today_close - prev_close) / prev_close
                               if prev_close > 0 else 0.0)

                all_signals[cfg["id"]].append({
                    "SYMBOL":        sym,
                    "SIGNAL_DATE":   date,
                    "SIGNAL_CLOSE":  round(float(today_close), 2),
                    "PREV_CLOSE":    round(float(prev_close), 2),
                    "ATH_PRICE":     round(float(ath_price), 2),
                    "MIN_5D_LOW":    round(float(min_low), 2),
                    "MIN_5D_DATE":   min_5d_date,
                    "PCT_FROM_LOW":  round(pct_from_low * 100, 2),
                    "PCT_FROM_ATH":  round(pct_from_ath * 100, 2),
                    "PCT_1D_CHANGE": round(pct_1d * 100, 2),
                })

        # Mark date complete
        completed_dates.add(str(date)[:10])

        # Check time budget after each date
        if time.time() - start_time > budget_seconds:
            elapsed_min = (time.time() - start_time) / 60
            print(f"\n⏱ Time budget of {TIME_BUDGET_MINUTES} min exceeded "
                  f"({elapsed_min:.1f} min elapsed) — saving progress and pausing.")
            partial = True
            break

    # ── Save per-config parquets (always: partial or complete) ───────────────
    print("\nSaving per-config signal parquets …")
    total = 0
    for c in configs:
        total += save_config_parquet(c["id"], all_signals[c["id"]], DB_DIR)

    if partial:
        save_checkpoint(completed_dates)
        remaining_after = len(all_dates) - len(completed_dates)
        print(f"\n{'='*60}")
        print(f"PARTIAL BACKFILL — checkpoint saved")
        print(f"  Completed : {len(completed_dates)} / {len(all_dates)} dates")
        print(f"  Remaining : {remaining_after} dates")
        print(f"  Signals   : {total:,} saved this leg")
        print(f"{'='*60}")
        sys.exit(2)   # Tells workflow: partial — please re-trigger

    # ── Complete — remove checkpoint and write final outputs ─────────────────
    if os.path.exists(CHECKPOINT_FILE):
        os.remove(CHECKPOINT_FILE)

    write_latest_outputs(configs, all_dates, DB_DIR, OUTPUT_DIR, all_signals, total)


if __name__ == "__main__":
    main()
