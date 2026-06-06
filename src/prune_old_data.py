"""
prune_old_data.py — Rolling 2-Year Data Window
===============================================
Keeps only the last 2 years of market data across:
  - db/eq_data.parquet        (trim rows by DATE1)
  - db/consolidated.parquet   (trim rows by DATE1)
  - db/signals_C*.parquet     (trim rows by SIGNAL_DATE)
  - bhav_data/*.parquet/.csv  (delete files older than 2 years by filename date)
  - output/YYYY-MM/           (delete monthly folders older than 2 years)

Runs after every simulation — idempotent and safe.
Scales to 500 configs without any size or space issues.
"""

import os
import re
import glob
import shutil
import pandas as pd
from datetime import datetime, timedelta, timezone

CUTOFF_YEARS = 2
IST          = timezone(timedelta(hours=5, minutes=30))

cutoff = pd.Timestamp(datetime.now(tz=IST).replace(tzinfo=None) - timedelta(days=CUTOFF_YEARS * 365))

print(f"\n{'='*55}")
print(f"Pruning data older than {cutoff.date()}  ({CUTOFF_YEARS}-year rolling window)")
print(f"{'='*55}")


def prune_parquet(path, date_col):
    if not os.path.exists(path):
        return
    df = pd.read_parquet(path)
    if date_col not in df.columns:
        return
    df[date_col] = pd.to_datetime(df[date_col])
    before = len(df)
    df = df[df[date_col] >= cutoff].copy()
    after  = len(df)
    removed = before - after
    df.to_parquet(path, index=False)
    flag = f"  (removed {removed:,})" if removed else "  (nothing to prune)"
    print(f"  ✅ {os.path.basename(path)}: {before:,} → {after:,} rows{flag}")


# ── 1. eq_data.parquet ───────────────────────────────────────────────────────
print("\n[1] DB parquet files")
prune_parquet('db/eq_data.parquet',      'DATE1')
prune_parquet('db/consolidated.parquet', 'DATE1')

# ── 2. signals_C*.parquet ────────────────────────────────────────────────────
for sig_path in sorted(glob.glob('db/signals_C*.parquet')):
    prune_parquet(sig_path, 'SIGNAL_DATE')

# ── 3. bhav_data files (parquet + leftover csv) ──────────────────────────────
print("\n[2] bhav_data files")
deleted_bhav = 0
for f in glob.glob('bhav_data/*.parquet') + glob.glob('bhav_data/*.csv'):
    fname     = os.path.basename(f)
    file_date = None

    # Format A: YYYY-MM-DD.parquet
    m = re.search(r'(\d{4}-\d{2}-\d{2})', fname)
    if m:
        try:
            file_date = pd.Timestamp(m.group(1))
        except Exception:
            pass

    # Format B: sec_bhavdata_full_DDMMYYYY  e.g. sec_bhavdata_full_01012025
    if file_date is None:
        m2 = re.search(r'(\d{2})(\d{2})(\d{4})', fname)
        if m2:
            try:
                file_date = pd.Timestamp(f"{m2.group(3)}-{m2.group(2)}-{m2.group(1)}")
            except Exception:
                pass

    if file_date is None:
        continue   # can't parse date — leave it alone

    if file_date < cutoff:
        os.remove(f)
        deleted_bhav += 1
        print(f"  🗑  Deleted {fname}  ({file_date.date()})")

if deleted_bhav == 0:
    print("  ✅ Nothing to prune")
else:
    print(f"  ✅ Deleted {deleted_bhav} file(s) older than {cutoff.date()}")

# ── 4. output/YYYY-MM/ monthly folders ───────────────────────────────────────
print("\n[3] output/ monthly folders")
deleted_out = 0
for folder in sorted(glob.glob('output/????-??')):
    m = re.match(r'output/(\d{4})-(\d{2})$', folder)
    if not m:
        continue
    try:
        folder_date = pd.Timestamp(f"{m.group(1)}-{m.group(2)}-01")
    except Exception:
        continue
    if folder_date < cutoff:
        shutil.rmtree(folder, ignore_errors=True)
        deleted_out += 1
        print(f"  🗑  Removed {folder}/  ({folder_date.strftime('%Y-%m')})")

if deleted_out == 0:
    print("  ✅ Nothing to prune")
else:
    print(f"  ✅ Removed {deleted_out} old output folder(s)")

# ── 5. Remove stale sim_results.json if it still exists (legacy) ─────────────
legacy = 'docs/data/sim_results.json'
if os.path.exists(legacy):
    os.remove(legacy)
    print(f"\n[4] Removed legacy {legacy}")

print(f"\n{'='*55}")
print("Pruning complete — repo stays within 2-year window ✅")
print(f"{'='*55}\n")
