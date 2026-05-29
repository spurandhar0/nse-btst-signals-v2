"""
Script 1: Consolidate NSE Bhavcopy CSVs
========================================
Reads all CSV files from bhav_data/**/*.csv
APPENDS new dates to existing db/consolidated.parquet (preserves history).

Rolling window: keeps last MAX_MONTHS of data to prevent file size bloat.
Floor: never trim below SIMULATION_START (2025-01-01) — all signal history preserved.

If no CSV files are found but consolidated.parquet already exists,
skips consolidation (allows re-runs after cleanup step removes raw CSVs).
"""

import os, glob
import pandas as pd
from datetime import datetime

BHAV_DIR    = "bhav_data"
OUTPUT_DIR  = "db"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "consolidated.parquet")

# Rolling window: keep last N months to prevent parquet exceeding 100MB GitHub limit
# Floor: never trim below SIMULATION_START so all signal history stays intact
MAX_MONTHS       = 30
SIMULATION_START = pd.Timestamp("2025-01-01")

REQUIRED_COLS = [
    "SYMBOL", "SERIES", "DATE1", "PREV_CLOSE", "OPEN_PRICE",
    "HIGH_PRICE", "LOW_PRICE", "LAST_PRICE", "CLOSE_PRICE",
    "AVG_PRICE", "TTL_TRD_QNTY", "TURNOVER_LACS",
    "NO_OF_TRADES", "DELIV_QTY", "DELIV_PER"
]


def parse_date(val):
    if pd.isna(val):
        return pd.NaT
    s = str(val).strip()
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return pd.NaT


def extract_file_date(filepath):
    name = os.path.splitext(os.path.basename(filepath))[0]
    if len(name) >= 8:
        date_str = name[-8:]
        try:
            return datetime.strptime(date_str, "%d%m%Y")
        except ValueError:
            pass
    return datetime.min


def load_csv(filepath):
    try:
        df = pd.read_csv(filepath, dtype=str, low_memory=False)
        df.columns = [c.strip().upper().replace(" ", "_") for c in df.columns]
        rename_map = {
            "DATE": "DATE1",
            "TOTTRDQTY": "TTL_TRD_QNTY",
            "TOTTRDVAL": "TURNOVER_LACS",
            "TOTALTRADES": "NO_OF_TRADES",
        }
        df.rename(columns=rename_map, inplace=True)
        for col in REQUIRED_COLS:
            if col not in df.columns:
                df[col] = ""
        df = df[REQUIRED_COLS].copy()
        df["SYMBOL"] = df["SYMBOL"].str.strip().str.upper()
        df["SERIES"] = df["SERIES"].str.strip().str.upper()
        df["DATE1"]  = df["DATE1"].apply(parse_date)
        df = df.dropna(subset=["DATE1", "SYMBOL"])
        df = df[df["SYMBOL"] != ""]
        num_cols = ["PREV_CLOSE", "OPEN_PRICE", "HIGH_PRICE", "LOW_PRICE",
                    "LAST_PRICE", "CLOSE_PRICE", "AVG_PRICE",
                    "TTL_TRD_QNTY", "TURNOVER_LACS", "NO_OF_TRADES",
                    "DELIV_QTY", "DELIV_PER"]
        for col in num_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
        return df
    except Exception as e:
        print(f"  ⚠️  Skipped {filepath}: {e}")
        return None


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    csv_files = glob.glob(os.path.join(BHAV_DIR, "**", "*.csv"), recursive=True)
    print(f"Found {len(csv_files)} CSV files in {BHAV_DIR}/")

    if not csv_files:
        if os.path.exists(OUTPUT_FILE):
            print("⚠️  No CSV files found, but consolidated.parquet already exists — skipping consolidation.")
            print("✅ Using existing consolidated.parquet")
            return
        else:
            print("❌ No CSV files found and no consolidated.parquet exists. Cannot continue.")
            raise SystemExit(1)

    csv_files = sorted(csv_files, key=extract_file_date)

    seen_dates = {}
    unique_files = []
    for f in csv_files:
        d = extract_file_date(f)
        key = d.strftime("%Y%m%d") if d != datetime.min else f
        if key not in seen_dates:
            seen_dates[key] = f
            unique_files.append(f)

    csv_files = unique_files
    print(f"Unique date files: {len(csv_files)}")

    parseable = [extract_file_date(f) for f in csv_files if extract_file_date(f) != datetime.min]
    if parseable:
        print(f"Date range in new CSVs: {min(parseable).strftime('%d-%b-%Y')} → {max(parseable).strftime('%d-%b-%Y')}")

    # Load new CSV data
    frames = []
    for i, f in enumerate(csv_files, 1):
        d = extract_file_date(f)
        label = d.strftime("%d-%b-%Y") if d != datetime.min else "?"
        print(f"  [{i:3d}/{len(csv_files)}] {label}")
        df = load_csv(f)
        if df is not None and len(df) > 0:
            frames.append(df)

    if not frames:
        print("❌ No valid data loaded from new CSVs.")
        if os.path.exists(OUTPUT_FILE):
            print("⚠️  Keeping existing consolidated.parquet unchanged.")
            return
        raise SystemExit(1)

    new_data = pd.concat(frames, ignore_index=True)
    print(f"New data rows: {len(new_data):,}")

    # ── APPEND to existing consolidated.parquet (CRITICAL: preserve history) ──
    if os.path.exists(OUTPUT_FILE):
        print(f"Loading existing consolidated.parquet for merge...")
        existing = pd.read_parquet(OUTPUT_FILE)
        print(f"Existing rows: {len(existing):,}")
        combined = pd.concat([existing, new_data], ignore_index=True)
        print(f"Combined rows before dedup: {len(combined):,}")
    else:
        print("No existing consolidated.parquet — creating fresh.")
        combined = new_data

    before = len(combined)
    combined.drop_duplicates(subset=["SYMBOL", "DATE1"], keep="last", inplace=True)
    after = len(combined)
    print(f"Deduplication: {before:,} → {after:,} rows (+{after - (before - len(new_data)):,} new rows added)")

    # ── ROLLING WINDOW: trim old data to keep file size bounded ──
    # Keep last MAX_MONTHS months, but never trim below SIMULATION_START
    cutoff = max(pd.Timestamp.now() - pd.DateOffset(months=MAX_MONTHS), SIMULATION_START)
    before_trim = len(combined)
    combined = combined[combined["DATE1"] >= cutoff]
    after_trim = len(combined)
    if before_trim != after_trim:
        print(f"Rolling trim (keep {MAX_MONTHS}m, floor {SIMULATION_START.date()}): {before_trim:,} → {after_trim:,} rows removed {before_trim - after_trim:,} old rows")
    else:
        print(f"Rolling trim: nothing to remove (all data within {MAX_MONTHS}m window)")

    combined.sort_values(["SYMBOL", "DATE1"], inplace=True)
    combined.reset_index(drop=True, inplace=True)
    combined.to_parquet(OUTPUT_FILE, index=False)

    size_mb = os.path.getsize(OUTPUT_FILE) / (1024 * 1024)
    print(f"✅ Consolidated: {after_trim:,} rows | {combined['SYMBOL'].nunique():,} symbols | file size: {size_mb:.1f} MB")
    print(f"✅ Saved: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
