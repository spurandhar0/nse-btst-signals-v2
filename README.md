# NSE BTST Daily Signals

Automated BTST (Buy Today Sell Tomorrow) signal generator for NSE stocks.

## Features
- Runs automatically **Monday–Friday at 9:00 PM IST**
- Scans all NSE EQ series stocks against **4 fixed parameter configs**
- Deduplicates results — each stock appears only once
- Highlights which configs matched
- Dashboard auto-refreshes every 5 minutes

## Dashboard
View live signals: https://spurandhar0.github.io/nse-btst-signals/

## Config Parameters

| Config | DaysBack | PctMin | PctMax | ATHMin | ATHMax | MaxBuys | BuyDrop | Target | StopLoss | MaxDuration |
|--------|----------|--------|--------|--------|--------|---------|---------|--------|----------|-------------|
| C1 | 10 | -10% | -5% | -45% | -40% | 3 | 10% | 10% | 25% | 50d |
| C2 | 10 | -10% | -5% | -45% | -40% | 3 | 10% | 10% | 25% | 60d |
| C3 | 10 | -10% | -5% | -45% | -40% | 4 | 5% | 10% | 25% | 40d |
| C4 | 10 | -9% | -5% | -45% | -40% | 4 | 5% | 10% | 25% | 40d |

## How Signals Work

**Filter 1 – N-Day Dip:** Today's close is between `pct_min` and `pct_max` above the lowest LOW price in the past `days_back` trading sessions.

**Filter 2 – ATH Distance:** Today's close is between `ath_min` and `ath_max` below the stock's All-Time High.

Both filters must pass simultaneously.

## Repo Structure

```
nse-btst-signals/
├── config/params.json       ← Edit to change parameters
├── src/
│   ├── nse_bhavcopy_fetch.py
│   ├── 00_bootstrap_historical.py
│   ├── 01_consolidate_csv.py
│   ├── 02_filter_eq.py
│   ├── 03_find_ath.py
│   ├── 04_run_daily_signals.py
│   └── 05_build_dashboard.py
├── bhav_data/               ← NSE CSV files stored here
├── db/                      ← Processed parquet files
├── output/                  ← Signal CSV output
└── docs/                    ← GitHub Pages dashboard
```

## Seeding Historical Data (First Run)

For accurate ATH values you need at least 1 year of historical bhavcopy CSVs.
Run the bootstrap workflow manually from GitHub Actions → "Bootstrap Historical Data".
This downloads ~1 year of NSE bhavcopy files automatically.

## Manual Run

Go to GitHub Actions → "NSE BTST Daily Signals" → "Run workflow"
