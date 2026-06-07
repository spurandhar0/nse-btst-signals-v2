"""
validate_and_backfill.py (OPTIMIZED)
====================================
FAST MODE: Only validate symbols from actual trading signals, skip MTF watchlist.

Scans missing bhav copies and backfills OHLC gaps for CONFIG SYMBOLS ONLY.
Ignores MTF watchlist — validates only symbols that appear in sim_results_C*.json.

SELF-CONTAINED: zero external tokens, zero third-party APIs.

Usage (GitHub Actions env vars):
  FROM_DATE      = "2025-01-01"    (default)
  FORCE_RECHECK  = "false"         (set true to re-validate already-present files)
"""

import os, io, glob, time, zipfile, logging, requests, random, json
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

# ── NSE 2025–2026 Trading Holidays ───────────────────────────────────────────
NSE_HOLIDAYS = {
    date(2025, 1, 26),   date(2025, 2, 26),   date(2025, 3, 14),
    date(2025, 3, 31),   date(2025, 4, 14),   date(2025, 4, 18),
    date(2025, 5, 1),    date(2025, 8, 15),   date(2025, 8, 27),
    date(2025, 10, 2),   date(2025, 10, 21),  date(2025, 10, 22),
    date(2025, 11, 5),   date(2025, 12, 25),
    date(2026, 1, 26),
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
    try:
        s.get("https://www.nseindia.com", timeout=15)
    except Exception:
        pass
    return s


def bhav_urls(d: date):
    dd   = d.strftime("%d")
    mm   = d.strftime("%m")
    yyyy = d.strftime("%Y")
    mon3 = d.strftime("%b").upper()

    return [
        f"https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{dd}{mm}{yyyy}.csv",
        f"https://nsearchives.nseindia.com/content/historical/EQUITIES/{yyyy}/{mon3}/cm{dd}{mon3}{yyyy}bhav.csv.zip",
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
                    break
                resp.raise_for_status()
                content = resp.content

                if url.endswith(".zip"):
                    with zipfile.ZipFile(io.BytesIO(content)) as zf:
                        csv_name = [n for n in zf.namelist() if n.endswith(".csv")][0]
                        content = zf.read(csv_name)

                df = pd.read_csv(io.BytesIO(content) if isinstance(content, bytes) else io.StringIO(content.decode()))
                df.columns = [c.strip().upper() for c in df.columns]
                log.info(f"  ✓ Downloaded {d.isoformat()} — {len(df)} rows")
                return df

            except Exception as e:
                log.warning(f"  Attempt {attempt} failed: {e}")
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_WAIT + random.uniform(0, 3))

    log.warning(f"  ✗ Could not download bhav for {d.isoformat()}")
    return None


def save_bhav(d: date, df: pd.DataFrame):
    """Save bhav copy as CSV."""
    df.columns = [c.strip().upper() for c in df.columns]
    (BHAV_ROOT / d.isoformat()).with_suffix(".csv").write_text(df.to_csv(index=False))


def load_bhav_csv(d: date) -> pd.DataFrame | None:
    """Load saved bhav copy or None."""
    pth = BHAV_ROOT / d.isoformat()
    pth = pth.with_suffix(".csv")
    if not pth.exists():
        return None
    try:
        return pd.read_csv(pth)
    except Exception:
        return None


def get_config_symbols() -> set[str]:
    """
    OPTIMIZED: Load ONLY symbols from sim_results_C*.json files.
    Skip MTF watchlist — validate only trading signal symbols.
    """
    syms = set()

    for jf in Path("docs/data").glob("sim_results_C*.json"):
        try:
            data = json.loads(jf.read_text())
            signals = data.get("signals", []) if isinstance(data, dict) else data
            for r in signals:
                s = (r.get("SYMBOL") or r.get("symbol") or "").strip().upper()
                if s:
                    syms.add(s)
        except Exception as e:
            log.warning(f"Could not load {jf}: {e}")

    log.info(f"✓ Loaded {len(syms)} symbols from trading signals (skipped MTF watchlist)")
    return syms


def fetch_symbol_history_nse(symbol: str, from_d: date, to_d: date, session: requests.Session) -> pd.DataFrame | None:
    """Fetch OHLCV history for a symbol from NSE historical API."""
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
                return None
            resp.raise_for_status()
            data = resp.json().get("data", [])
            if not data:
                return None
            df = pd.DataFrame(data)
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


def main():
    log.info(f"=== NSE Bhav Validation & Backfill (FAST MODE) ===")
    log.info(f"From {FROM_DATE} → {TODAY_IST}  |  force={FORCE}")

    session = make_session()

    # ── Phase 1: Download missing bhav copies ────────────────────────────────
    days = list(trading_days(FROM_DATE, TODAY_IST))
    missing_days = [d for d in days if load_bhav_csv(d) is None]

    log.info(f"Phase 1: {len(days)} trading days, {len(missing_days)} bhav files missing")

    filled = 0
    for d in missing_days:
        log.info(f"  Downloading bhav for {d.isoformat()} …")
        df = download_bhav(d, session)
        if df is not None:
            save_bhav(d, df)
            filled += 1
            time.sleep(2 + random.uniform(0, 2))

    log.info(f"Phase 1 done: {filled}/{len(missing_days)} missing days filled ✓")

    # ── Phase 2: Load symbols from TRADING SIGNALS ONLY (skip MTF) ────────────
    known_syms = get_config_symbols()
    if not known_syms:
        log.info("No known symbols found — skipping validation")
        return

    # ── Phase 3: Check for OHLC gaps ──────────────────────────────────────────
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

    all_days_set = set(days)
    gaps: dict[str, list[date]] = {}
    for sym, have in sym_dates.items():
        missing = sorted(all_days_set - have)
        if missing:
            gaps[sym] = missing

    log.info(f"Phase 2: {len(gaps)}/{len(known_syms)} symbols have missing OHLC dates")

    if not gaps:
        log.info("All symbols fully covered — nothing to backfill ✓")
        return

    # ── Phase 4: Fetch missing symbol data ────────────────────────────────────
    log.info(f"Phase 3: Fetching gaps for {len(gaps)} symbols …")

    sym_dir = DB_ROOT / "symbol_history"
    sym_dir.mkdir(exist_ok=True)

    for sym, missing_list in sorted(gaps.items()):
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
            log.warning(f"  ✗ {sym}: could not fetch any data")

    log.info("=== Fast validation & backfill complete ===")


if __name__ == "__main__":
    main()
