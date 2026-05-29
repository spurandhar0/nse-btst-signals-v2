"""
Script 3: Find All Time High (ATH) Per Symbol
===============================================
Reads: db/eq_data.parquet
Output: db/ath.parquet (columns: SYMBOL, ATH_PRICE, ATH_DATE)
"""

import os
import pandas as pd

INPUT_FILE  = "db/eq_data.parquet"
OUTPUT_FILE = "db/ath.parquet"


def main():
    if not os.path.exists(INPUT_FILE):
        print(f"\u274c Input not found: {INPUT_FILE}")
        raise SystemExit(1)

    print(f"Loading {INPUT_FILE} ...")
    df = pd.read_parquet(INPUT_FILE, columns=["SYMBOL", "DATE1", "HIGH_PRICE"])
    print(f"  Rows: {len(df):,} | Symbols: {df['SYMBOL'].nunique():,}")

    idx = df.groupby("SYMBOL")["HIGH_PRICE"].idxmax()
    ath = df.loc[idx, ["SYMBOL", "DATE1", "HIGH_PRICE"]].copy()
    ath.rename(columns={"DATE1": "ATH_DATE", "HIGH_PRICE": "ATH_PRICE"}, inplace=True)
    ath.reset_index(drop=True, inplace=True)
    ath = ath[ath["ATH_PRICE"] > 0]

    ath.to_parquet(OUTPUT_FILE, index=False)
    print(f"\u2705 ATH saved: {OUTPUT_FILE} ({len(ath):,} symbols)")


if __name__ == "__main__":
    main()
