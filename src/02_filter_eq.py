"""
Script 2: Filter EQ Series Only
=================================
Reads: db/consolidated.parquet
Output: db/eq_data.parquet
"""

import os
import pandas as pd

INPUT_FILE  = "db/consolidated.parquet"
OUTPUT_FILE = "db/eq_data.parquet"
SERIES_CHANGED_FILE = "db/series_changed.parquet"


def main():
    if not os.path.exists(INPUT_FILE):
        print(f"\u274c Input not found: {INPUT_FILE}")
        raise SystemExit(1)

    print(f"Loading {INPUT_FILE} ...")
    df = pd.read_parquet(INPUT_FILE)
    print(f"  Total rows: {len(df):,}")

    df["DATE1"] = pd.to_datetime(df["DATE1"])
    latest_idx    = df.groupby("SYMBOL")["DATE1"].idxmax()
    latest_series = df.loc[latest_idx, ["SYMBOL", "SERIES", "DATE1"]].copy()
    latest_series.set_index("SYMBOL", inplace=True)

    was_eq_now_not = latest_series[latest_series["SERIES"] != "EQ"].copy()
    was_eq_now_not = was_eq_now_not[
        was_eq_now_not.index.isin(df[df["SERIES"] == "EQ"]["SYMBOL"].unique())
    ]

    was_eq_now_not.reset_index().rename(
        columns={"SERIES": "LATEST_SERIES", "DATE1": "LATEST_DATE"}
    ).to_parquet(SERIES_CHANGED_FILE, index=False)

    eq_symbols = set(latest_series[latest_series["SERIES"] == "EQ"].index)
    eq = df[(df["SERIES"] == "EQ") & (df["SYMBOL"].isin(eq_symbols))].copy()
    print(f"  EQ rows: {len(eq):,} | EQ symbols: {eq['SYMBOL'].nunique():,}")

    if len(eq) == 0:
        print("\u274c No EQ rows found!")
        raise SystemExit(1)

    eq.sort_values(["SYMBOL", "DATE1"], inplace=True)
    eq.reset_index(drop=True, inplace=True)
    eq.to_parquet(OUTPUT_FILE, index=False)

    print(f"\u2705 EQ data saved: {OUTPUT_FILE}")
    print(f"\u2705 Date range: {eq['DATE1'].min().date()} \u2192 {eq['DATE1'].max().date()}")


if __name__ == "__main__":
    main()
