"""
backfill_symbol.py — Fetch NSE historical OHLCV data for specific symbols
and merge into the existing parquet DB.

Usage:
  python src/backfill_symbol.py SYMBOL1 SYMBOL2 ...
  Or set env var: BACKFILL_SYMBOLS="KALYANKJIL,HDFCBANK"

Data source: NSE India historical equity API
(requires session cookie — retried with exponential backoff)
"""
import os, sys, time, random, json, requests
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta

SYMBOLS = os.environ.get("BACKFILL_SYMBOLS", "").split(",") + sys.argv[1:]
SYMBOLS = [s.strip().upper() for s in SYMBOLS if s.strip()]

if not SYMBOLS:
    print("No symbols specified. Pass as args or BACKFILL_SYMBOLS env var.")
    sys.exit(0)

print(f"Backfilling {len(SYMBOLS)} symbol(s): {SYMBOLS}")

BASE_DIR   = Path(__file__).parent.parent
DB_DIR     = BASE_DIR / "db"
EQ_PARQUET = DB_DIR / "eq_data.parquet"

START_DATE = os.environ.get("SIGNAL_START_DATE", "2025-01-01")
END_DATE   = datetime.today().strftime("%Y-%m-%d")

# ── NSE session ──────────────────────────────────────────
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}

def get_nse_session():
    s = requests.Session()
    s.headers.update(HEADERS)
    for attempt in range(5):
        try:
            r = s.get("https://www.nseindia.com/", timeout=15)
            if r.status_code == 200:
                return s
        except Exception as e:
            print(f"  Session attempt {attempt+1}: {e}")
        time.sleep(2 ** attempt + random.random())
    return None

def fetch_symbol_history(session, symbol, from_date, to_date):
    """Fetch OHLCV history from NSE equity historical API."""
    url = "https://www.nseindia.com/api/historical/cm/equity"
    params = {
        "symbol": symbol,
        "series": '["EQ"]',
        "from":   datetime.strptime(from_date, "%Y-%m-%d").strftime("%d-%m-%Y"),
        "to":     datetime.strptime(to_date,   "%Y-%m-%d").strftime("%d-%m-%Y"),
        "csv":    "true",
    }
    for attempt in range(5):
        try:
            r = session.get(url, params=params, timeout=20)
            if r.status_code == 200 and r.text.strip():
                from io import StringIO
                df = pd.read_csv(StringIO(r.text))
                return df
            print(f"  {symbol}: HTTP {r.status_code} (attempt {attempt+1})")
        except Exception as e:
            print(f"  {symbol}: {e} (attempt {attempt+1})")
        time.sleep(2 ** attempt + random.random() * 2)
    return None

def normalize_nse_df(df, symbol):
    """Standardise NSE CSV columns to match existing parquet schema."""
    df.columns = [c.strip().upper().replace(" ", "_") for c in df.columns]
    rename = {
        "DATE1": "DATE", "DATE": "DATE",
        "OPEN_PRICE": "OPEN", "HIGH_PRICE": "HIGH", "LOW_PRICE": "LOW",
        "CLOSE_PRICE": "CLOSE", "PREV_CLOSE": "PREV_CLOSE",
        "NO_OF_TRADES": "NO_OF_TRADES", "TOTAL_TURNOVER_(RS.)": "TURNOVER",
        "DELIVERABLE_QTY": "DELIV_QTY", "% _DELY_QTY_TO_TRADED_QTY": "DELIV_PCT",
        "TTL_TRD_QNTY": "VOLUME",
    }
    df.rename(columns=rename, inplace=True)
    if "DATE" not in df.columns:
        date_cols = [c for c in df.columns if "DATE" in c]
        if date_cols:
            df.rename(columns={date_cols[0]: "DATE"}, inplace=True)
    df["DATE"] = pd.to_datetime(df["DATE"], dayfirst=True, errors="coerce")
    df["SYMBOL"] = symbol
    df["SERIES"] = "EQ"
    # Ensure numeric columns
    for col in ["OPEN","HIGH","LOW","CLOSE","VOLUME"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col].astype(str).str.replace(",",""), errors="coerce")
    df = df.dropna(subset=["DATE","CLOSE"])
    return df[["DATE","SYMBOL","SERIES","OPEN","HIGH","LOW","CLOSE","VOLUME"]].copy()

# ── Main ─────────────────────────────────────────────────
session = get_nse_session()
if not session:
    print("ERROR: Could not establish NSE session")
    sys.exit(1)
print("NSE session established")

# Load existing parquet
if EQ_PARQUET.exists():
    existing = pd.read_parquet(EQ_PARQUET)
    print(f"Existing parquet: {len(existing):,} rows")
else:
    existing = pd.DataFrame()
    print("No existing parquet, starting fresh")

new_rows = []
for sym in SYMBOLS:
    print(f"\nFetching {sym} from {START_DATE} to {END_DATE}...")
    df = fetch_symbol_history(session, sym, START_DATE, END_DATE)
    if df is None or df.empty:
        print(f"  {sym}: No data returned")
        continue
    df_norm = normalize_nse_df(df, sym)
    print(f"  {sym}: {len(df_norm)} rows fetched")
    new_rows.append(df_norm)
    time.sleep(1 + random.random())  # polite delay

if not new_rows:
    print("\nNo new data fetched. Exiting.")
    sys.exit(0)

new_df = pd.concat(new_rows, ignore_index=True)

# Merge with existing
if not existing.empty:
    combined = pd.concat([existing, new_df], ignore_index=True)
    combined["DATE"] = pd.to_datetime(combined["DATE"])
    combined = combined.drop_duplicates(subset=["DATE","SYMBOL","SERIES"], keep="last")
    combined.sort_values(["SYMBOL","DATE"], inplace=True)
else:
    combined = new_df

combined.to_parquet(EQ_PARQUET, index=False)
print(f"\nParquet updated: {len(combined):,} rows saved to {EQ_PARQUET}")
print("Done. Run generate_signals.yml or daily_run.yml to rebuild signals.")
