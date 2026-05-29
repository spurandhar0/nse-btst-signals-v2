#!/usr/bin/env python3
"""
05_build_dashboard.py — Dashboard Injector
============================================
Strategy: index.html is the complete, working dashboard template.
          This script only injects fresh data into it on every run.

What it updates in index.html:
  1. const SIGNALS_RAW = [...]          ← today's signals from signals_latest.csv
  2. const NIFTY_DATA  = {...}          ← nifty index data from nifty_index.json
  3. const CONFIGS_DEF = [...]          ← configs from config/params.json
  4. hdr-badge timestamp                ← "23-May-2026 10:04 IST"
  5. bannerDate + Data date in banner   ← same timestamp + last data date
  6. toDate input value                 ← today's date (YYYY-MM-DD)

Reads:
  docs/index.html          (template — kept in both repos)
  output/signals_latest.csv
  docs/data/nifty_index.json   (optional)
  config/params.json

Writes:
  docs/index.html          (in-place, same file)
"""

import os
import re
import json
import csv
from datetime import datetime, timezone, timedelta

IST  = timezone(timedelta(hours=5, minutes=30))
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TEMPLATE = os.path.join(BASE, 'docs',   'index.html')
SIGS_CSV = os.path.join(BASE, 'output', 'signals_latest.csv')
NIFTY    = os.path.join(BASE, 'docs',   'data', 'nifty_index.json')
CONFIG   = os.path.join(BASE, 'config', 'params.json')
OUT      = TEMPLATE   # write back to same file


# ── helpers ──────────────────────────────────────────────────────────────────

def load_signals():
    rows = []
    if not os.path.exists(SIGS_CSV):
        print(f"  ⚠  signals_latest.csv not found at {SIGS_CSV} — SIGNALS_RAW will be []")
        return rows

    # Load MTF filter
    mtf_path = os.path.join(BASE, 'docs', 'data', 'mtf_symbols.json')
    mtf_set  = set()
    if os.path.exists(mtf_path):
        with open(mtf_path, encoding='utf-8') as f:
            mtf_list = json.load(f)
        mtf_set = set(s for s in mtf_list if s != 'Symbol / Scrip Name')

    with open(SIGS_CSV, newline='', encoding='utf-8') as f:
        for r in csv.DictReader(f):
            if mtf_set and r.get('SYMBOL', '') not in mtf_set:
                continue
            rows.append(dict(r))
    if mtf_set:
        print(f"  ✅ Signals loaded: {len(rows)} rows (MTF filtered)")
    else:
        print(f"  ✅ Signals loaded: {len(rows)} rows")
    return rows


def load_nifty():
    if not os.path.exists(NIFTY):
        print(f"  ⚠  nifty_index.json not found — NIFTY_DATA will be {{}}")
        return {}
    try:
        with open(NIFTY, encoding='utf-8') as f:
            raw = json.load(f)
        label_to_key = {
            'NIFTY 50':  'nifty50',
            'BANKNIFTY': 'banknifty',
            'SENSEX':    'sensex',
            'NIFTY IT':  'niftyit',
        }
        result = {}
        for idx in raw.get('indices', []):
            key = label_to_key.get(idx.get('label'))
            if key:
                result[key] = {
                    'price':      idx.get('close', 0),
                    'change':     idx.get('change', 0),
                    'change_pct': idx.get('change_pct', 0),
                    'date':       idx.get('date', ''),
                }
        result['updated_at'] = raw.get('updated_at', '')
        print(f"  ✅ Nifty data loaded: {list(result.keys())}")
        return result
    except Exception as e:
        print(f"  ⚠  Could not load nifty_index.json: {e}")
        return {}


def load_configs():
    if not os.path.exists(CONFIG):
        print(f"  ⚠  config/params.json not found — CONFIGS_DEF will be []")
        return []
    with open(CONFIG, encoding='utf-8') as f:
        data = json.load(f)
    cfgs = data.get('configs', [])
    print(f"  ✅ Configs loaded: {len(cfgs)} configs")
    return cfgs


def load_sim_last_date():
    """Read the last data date from sim_meta.json (or first per-config file) for the banner."""
    import glob
    meta_path = os.path.join(BASE, 'docs', 'data', 'sim_meta.json')
    if os.path.exists(meta_path):
        try:
            with open(meta_path, encoding='utf-8') as f:
                d = json.load(f)
            return d.get('last_date', '')
        except Exception:
            pass
    # Fallback: try first available sim_results_C*.json
    pattern = os.path.join(BASE, 'docs', 'data', 'sim_results_C*.json')
    files = sorted(glob.glob(pattern))
    if files:
        try:
            with open(files[0], encoding='utf-8') as f:
                d = json.load(f)
            return d.get('meta', {}).get('last_date', '')
        except Exception:
            pass
    return ''


# ── injection ─────────────────────────────────────────────────────────────────

def inject(html: str,
           signals_js: str,
           nifty_js: str,
           configs_js: str,
           generated: str,
           last_date: str,
           today_date: str) -> str:

    # 1. SIGNALS_RAW
    html = re.sub(
        r'const SIGNALS_RAW\s*=\s*\[.*?\];',
        f'const SIGNALS_RAW = {signals_js};',
        html, flags=re.DOTALL
    )

    # 2. NIFTY_DATA
    html = re.sub(
        r'const NIFTY_DATA\s*=\s*\{.*?\};',
        f'const NIFTY_DATA  = {nifty_js};',
        html, flags=re.DOTALL
    )

    # 3. CONFIGS_DEF
    html = re.sub(
        r'const CONFIGS_DEF\s*=\s*\[.*?\];',
        f'const CONFIGS_DEF = {configs_js};',
        html, flags=re.DOTALL
    )

    # 4. hdr-badge timestamp  e.g.  &#9679; 23-May-2026 10:04 IST
    html = re.sub(
        r'(<span class="hdr-badge">&#9679;\s*)([^<]+)(</span>)',
        rf'\g<1>{generated}\3',
        html
    )

    # 5. Banner line — bannerDate inner text + Data date
    #    ✓ Last simulation: <strong id="bannerDate">23-May-2026 10:04 IST</strong>
    #    ... Data date: <strong>2026-05-22</strong>
    html = re.sub(
        r'(<strong id="bannerDate">)([^<]*)(</strong>)',
        rf'\g<1>{generated}\3',
        html
    )
    html = re.sub(
        r'(Data date:\s*<strong>)([^<]*)(</strong>)',
        rf'\g<1>{last_date}\3',
        html
    )

    # 6. toDate input default value
    html = re.sub(
        r'(<input[^>]+id="toDate"[^>]+value=")[^"]*(")',
        rf'\g<1>{today_date}\2',
        html
    )

    return html


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{'='*60}")
    print("Dashboard Injector — NSE BTST Signals")
    print(f"{'='*60}")

    if not os.path.exists(TEMPLATE):
        print(f"❌ Template not found: {TEMPLATE}")
        print("   Make sure docs/index.html exists in your repo.")
        raise SystemExit(1)

    now        = datetime.now(tz=IST)
    generated  = now.strftime('%d-%b-%Y %H:%M IST')
    today_date = now.strftime('%Y-%m-%d')
    last_date  = load_sim_last_date() or today_date

    print(f"\nTimestamp : {generated}")
    print(f"Data date : {last_date}")
    print(f"Today     : {today_date}")
    print()

    signals = load_signals()
    nifty   = load_nifty()
    configs = load_configs()

    signals_js = json.dumps(signals, default=str, ensure_ascii=False)
    nifty_js   = json.dumps(nifty,   default=str, ensure_ascii=False)
    configs_js = json.dumps(configs, default=str, ensure_ascii=False)

    print(f"\nReading template: {TEMPLATE}")
    with open(TEMPLATE, encoding='utf-8') as f:
        html = f.read()

    print("Injecting fresh data...")
    html = inject(html, signals_js, nifty_js, configs_js,
                  generated, last_date, today_date)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='utf-8') as f:
        f.write(html)

    size_kb = os.path.getsize(OUT) / 1024
    print(f"\n✅ Written: {OUT}  ({size_kb:,.0f} KB)")
    print(f"   SIGNALS_RAW  : {len(signals)} rows")
    print(f"   NIFTY_DATA   : {len(nifty)} keys")
    print(f"   CONFIGS_DEF  : {len(configs)} configs")
    print(f"\n{'='*60}")
    print(f"Dashboard built — {generated}")
    print(f"{'='*60}\n")


if __name__ == '__main__':
    main()
