"""
validate_and_backfill.py
========================
Scans every NSE trading day from FROM_DATE to today.
For each day:
  - Checks if bhav copy CSV exists in bhav_data/
  - If missing → downloads from NSE archives (no cookies needed)
  - Validates that every symbol in our known-symbols list has OHLC data
  - Fills any per-symbol gaps from NSE historical API

SELF-CONTAINED: zero external tokens, zero third-party APIs.
Survives NSE website changes because it tries multiple URL formats.

Usage (GitHub Actions env vars):
  FROM_DATE      = "2025-01-01"    (default)
  FORCE_RECHECK  = "false"         (set true to re-validate already-present files)
"""

import os, io, glob, time, zipfile, logging, requests, random
import pandas as pd
from datetime import date, timedelta, datetime, timezone
from pathlib import Path

# ── Config ───────────────────────────────────────────────────────────────────
BHAV_ROOT   = Path("bhav_data")
DB_ROOT     = Path("db")
IST         = timezone(timedelta(hours=5, minutes=30))
TODAY_IST   = datetime.now(tz=IST).date()
FROM_DATE   = date.fromisoformat(os.environ.get("FROM_DATE", "2025-01-01"))
FORCE       = os.environ.get("FORCE_RECHECK", "false").lower() == "true"
MAX_RETRIES = 3
RETRY_WAIT  = 8   # seconds between retries

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

BHAV_ROOT.mkdir(exist_ok=True)
DB_ROOT.mkdir(exist_ok=True)

# ── NSE 2025–2026 Trading Holidays (BSE & NSE match) ─────────────────────────
# Updated annually — hardcoded avoids dependency on any API
NSE_HOLIDAYS = {
    # 2025
    date(2025, 1, 26),   # Republic Day
    date(2025, 2, 26),   # Mahashivratri
    date(2025, 3, 14),   # Holi
    date(2025, 3, 31),   # Eid ul-Fitr
    date(2025, 4, 14),   # Dr. Ambedkar Jayanti
    date(2025, 4, 18),   # Good Friday
    date(2025, 5, 1),    # Maharashtra Day
    date(2025, 8, 15),   # Independence Day
    date(2025, 8, 27),   # Ganesh Chaturthi
    date(2025, 10, 2),   # Gandhi Jayanti
    date(2025, 10, 21),  # Diwali Laxmi Puja
    date(2025, 10, 22),  # Diwali Balipratipada
    date(2025, 11, 5),   # Guru Nanak Jayanti
    date(2025, 12, 25),  # Christmas
    # 2026 (add as announced)
    date(2026, 1, 26),   # Republic Day (tentative)
}


def is_trading_day(d: date) -> bool:
    """True if d is a weekday that is NOT an NSE holiday."""
    return d.weekday() < 5 and d not in NSE_HOLIDAYS


def trading_days(start: date, end: date):
    """Yield every trading day in [start, end]."""
    cur = start
    while cur <= end:
        if is_trading_day(cur):
            yield cur
        cur += timedelta(days=1)


# ── Browser-like session (NSE blocks plain urllib) ────────────────────────────
def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nseindia.com",
    })
    # Warm up cookies — NSE requires a session cookie for some endpoints
    try:
        s.get("https://www.nseindia.com", timeout=15)
    except Exception:
        pass
    return s


# ── NSE archive bhav copy URL formats (try all until one works) ──────────────
def bhav_urls(d: date):
    dd   = d.strftime("%d")
    mm   = d.strftime("%m")
    yyyy = d.strftime("%Y")
    mon3 = d.strftime("%b").upper()

    return [
        # NEW format (2024+) — sec_bhavdata_full_DDMMYYYY.csv (no zip)
        f"https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{dd}{mm}{yyyy}.csv",
        # OLD zip format — cm<DD><MON><YYYY>bhav.csv.zip
        f"https://nsearchives.nseindia.com/content/historical/EQUITIES/{yyyy}/{mon3}/cm{dd}{mon3}{yyyy}bhav.csv.zip",
        # Alternate path
        f"https://www1.nseindia.com/content/historical/EQUITIES/{yyyy}/{mon3}/cm{dd}{mon3}{yyyy}bhav.csv.zip",
    ]


def download_bhav(d: date, session: requests.Session) -> pd.DataFrame | None:
    """Download bhav copy for date d. Returns DataFrame or None."""
    for url in bhav_urls(d):
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                log.info(f"  Trying {url} (attempt {attempt})")
                resp = session.get(url, timeout=30, stream=True)
                if resp.status_code == 404:
                    break   # try next URL format
                resp.raise_for_status()
                content = resp.content

                # Handle zip
                if url.endswith(".zip"):
                    with zipfile.ZipFile(io.BytesIO(content)) as zf:
                        csv_name = [n for n in zf.namelist() if n.endswith(".csv")][0]
                        content = zf.read(csv_name)

                df = pd.read_csv(io.BytesIO(content) if isinstance(content, bytes) else io.StringIO(content.decode()))
                # Normalize column names
                df.columns = [c.strip().upper() for c in df.columns]
                log.info(f"  ✓ Downloaded {d.isoformat()} — {len(df)} rows")
                return df

            except Exception as e:
                log.warning(f"  Attempt {attempt} failed: {e}")
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_WAIT + random.uniform(0, 3))

    log.warning(f"  ✗ Could not download bhav for {d.isoformat()} — all URL formats failed")
    return None


def save_bhav(d: date, df: pd.DataFrame):
    """Save cleaned bhav copy to bhav_data/YYYY-MM-DD.csv"""
    path = BHAV_ROOT / f"{d.isoformat()}.csv"
    # Keep only EQ series to reduce file size
    if "SERIES" in df.columns:
        df = df[df["SERIES"].str.strip() == "EQ"].copy()
    df.to_csv(path, index=False)
    log.info(f"  Saved {path} ({len(df)} EQ rows)")
    return path


def load_bhav_csv(d: date) -> pd.DataFrame | None:
    """Load existing bhav CSV for date d (any format we saved)."""
    # Prefer our normalized YYYY-MM-DD.csv
    p = BHAV_ROOT / f"{d.isoformat()}.csv"
    if p.exists():
        return pd.read_csv(p)
    # Fallback: legacy filenames
    dd, mm, yyyy = d.strftime("%d"), d.strftime("%m"), d.strftime("%Y")
    patterns = [
        BHAV_ROOT / f"sec_bhavdata_full_{dd}{mm}{yyyy}.csv",
        BHAV_ROOT / f"cm{dd}{d.strftime('%b').upper()}{yyyy}bhav.csv",
    ]
    for pat in patterns:
        if pat.exists():
            return pd.read_csv(pat)
    return None


def get_known_symbols() -> set[str]:
    """
    Return the set of symbols that our strategy tracks.
    Sources:
      1. db/symbols.txt  — explicitly curated list
      2. Union of all sim_results_C*.json symbols
      3. docs/data/mtf_symbols.json — MTF watchlist (NEW)
      4. Parquet DB if exists
    """
    syms = set()
    import json

    # 1. Curated list
    sym_file = DB_ROOT / "symbols.txt"
    if sym_file.exists():
        syms.update(l.strip().upper() for l in sym_file.read_text().splitlines() if l.strip())

    # 2. sim_results JSON files
    for jf in Path("docs/data").glob("sim_results_C*.json"):
        try:
            data = json.loads(jf.read_text())
            rows = data if isinstance(data, list) else data.get("rows", data.get("data", []))
            for r in rows:
                s = (r.get("SYMBOL") or r.get("symbol") or "").strip().upper()
                if s:
                    syms.add(s)
        except Exception:
            pass

    # 3. MTF symbols watchlist — docs/data/mtf_symbols.json
    mtf_file = Path("docs/data/mtf_symbols.json")
    if mtf_file.exists():
        try:
            mtf_list = json.loads(mtf_file.read_text())
            added = 0
            for entry in mtf_list:
                s = str(entry).strip().upper()
                # Skip header row and empty entries
                if s and s not in ("SYMBOL / SCRIP NAME", "SYMBOL", "SCRIP NAME"):
                    syms.add(s)
                    added += 1
            log.info(f"MTF symbols loaded: {added} from {mtf_file}")
        except Exception as e:
            log.warning(f"Could not load MTF symbols: {e}")

    # 4. Parquet DB if exists
    pq = DB_ROOT / "eq_filtered.parquet"
    if pq.exists():
        try:
            import pyarrow.parquet as pq_lib
            t = pq_lib.read_table(pq, columns=["SYMBOL"])
            syms.update(t.column("SYMBOL").to_pylist())
        except Exception:
            pass

    log.info(f"Total known symbols: {len(syms)}")
    return syms


def fetch_symbol_history_nse(symbol: str, from_d: date, to_d: date, session: requests.Session) -> pd.DataFrame | None:
    """
    Fetch OHLCV history for a symbol from NSE historical API.
    Returns DataFrame with DATE, OPEN, HIGH, LOW, CLOSE, VOLUME or None.
    """
    fmt = "%d-%m-%Y"
    url = (
        "https://www.nseindia.com/api/historical/cm/equity"
        f"?symbol={requests.utils.quote(symbol)}"
        f"&series=[%22EQ%22]"
        f"&from={from_d.strftime(fmt)}"
        f"&to={to_d.strftime(fmt)}"
    )
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(url, timeout=20)
            if resp.status_code == 400:
                return None   # symbol not found or no data
            resp.raise_for_status()
            data = resp.json().get("data", [])
            if not data:
                return None
            df = pd.DataFrame(data)
            # Normalize columns
            col_map = {
                "CH_TIMESTAMP": "DATE", "TIMESTAMP": "DATE",
                "CH_OPENING_PRICE": "OPEN", "CH_TRADE_HIGH_PRICE": "HIGH",
                "CH_TRADE_LOW_PRICE": "LOW", "CH_CLOSING_PRICE": "CLOSE",
                "CH_TOT_TRADED_QTY": "VOLUME",
            }
            df.rename(columns=col_map, inplace=True)
            keep = [c for c in ["DATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"] if c in df.columns]
            return df[keep]
        except Exception as e:
            log.warning(f"  Symbol {symbol} fetch attempt {attempt}: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_WAIT + random.uniform(0, 2))

    return None


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    log.info(f"=== NSE Bhav Validation & Backfill ===")
    log.info(f"From {FROM_DATE} → {TODAY_IST}  |  force={FORCE}")

    session = make_session()

    # ── Phase 1: Ensure bhav copy exists for every trading day ───────────────
    days = list(trading_days(FROM_DATE, TODAY_IST))
    missing_days = []
    for d in days:
        if load_bhav_csv(d) is None:
            missing_days.append(d)

    log.info(f"Phase 1: {len(days)} trading days, {len(missing_days)} bhav files missing")

    filled = 0
    for d in missing_days:
        log.info(f"  Downloading bhav for {d.isoformat()} …")
        df = download_bhav(d, session)
        if df is not None:
            save_bhav(d, df)
            filled += 1
            time.sleep(2 + random.uniform(0, 2))   # polite delay

    log.info(f"Phase 1 done: {filled}/{len(missing_days)} missing days filled")

    # ── Phase 2: Validate per-symbol OHLC completeness ───────────────────────
    known_syms = get_known_symbols()
    if not known_syms:
        log.info("No known symbols found — skipping per-symbol validation")
        return

    # Build a symbol → set of dates that exist in bhav data
    sym_dates: dict[str, set[date]] = {s: set() for s in known_syms}

    for d in days:
        df = load_bhav_csv(d)
        if df is None:
            continue
        df.columns = [c.strip().upper() for c in df.columns]
        sym_col = next((c for c in ["SYMBOL", "SCRIP_CD", "TckrSymb"] if c in df.columns), None)
        if sym_col is None:
            continue
        present = set(df[sym_col].str.strip().str.upper().tolist())
        for s in known_syms:
            if s in present:
                sym_dates[s].add(d)

    # Find symbols with gaps
    all_days_set = set(days)
    gaps: dict[str, list[date]] = {}
    for sym, have in sym_dates.items():
        missing = sorted(all_days_set - have)
        if missing:
            gaps[sym] = missing

    log.info(f"Phase 2: {len(gaps)} symbols have missing OHLC dates")

    if not gaps:
        log.info("All symbols fully covered — nothing to backfill ✓")
        return

    # ── Phase 3: Fetch missing symbol data from NSE API ──────────────────────
    log.info(f"Phase 3: Fetching gaps for {len(gaps)} symbols …")

    # Ensure per-symbol CSV directory exists
    sym_dir = DB_ROOT / "symbol_history"
    sym_dir.mkdir(exist_ok=True)

    for sym, missing_list in sorted(gaps.items()):
        # Group consecutive missing dates into ranges for efficient API calls
        ranges: list[tuple[date, date]] = []
        range_start = missing_list[0]
        prev = missing_list[0]
        for d in missing_list[1:]:
            if (d - prev).days > 7:
                ranges.append((range_start, prev))
                range_start = d
            prev = d
        ranges.append((range_start, prev))

        sym_path = sym_dir / f"{sym}.csv"
        existing_df = pd.read_csv(sym_path) if sym_path.exists() else pd.DataFrame()

        new_rows = []
        for (r_start, r_end) in ranges:
            log.info(f"  {sym}: fetching {r_start} → {r_end}")
            df = fetch_symbol_history_nse(sym, r_start, r_end, session)
            if df is not None and not df.empty:
                new_rows.append(df)
            time.sleep(1.5 + random.uniform(0, 1.5))

        if new_rows:
            new_df = pd.concat(new_rows, ignore_index=True)
            combined = pd.concat([existing_df, new_df], ignore_index=True)
            if "DATE" in combined.columns:
                combined["DATE"] = pd.to_datetime(combined["DATE"], errors="coerce")
                combined.drop_duplicates(subset=["DATE"], inplace=True)
                combined.sort_values("DATE", inplace=True)
            combined.to_csv(sym_path, index=False)
            log.info(f"  ✓ {sym}: {len(new_df)} rows added")
        else:
            log.warning(f"  ✗ {sym}: could not fetch any data for missing dates")

    log.info("=== Validation & backfill complete ===")


if __name__ == "__main__":
    main()
