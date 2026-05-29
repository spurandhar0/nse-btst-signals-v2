"""
Bootstrap Historical Bhavcopy Data
=====================================
Downloads NSE bhavcopy from START_DATE to yesterday.
Run this ONCE manually to seed historical data.
Skips files that already exist.

Usage: python src/00_bootstrap_historical.py
"""

import os, time, requests
from datetime import datetime, timedelta, date, timezone

IST        = timezone(timedelta(hours=5, minutes=30))
NOW_IST    = datetime.now(tz=IST)
BHAV_ROOT  = "bhav_data"

# Fixed start date — gives context for Jan 2025 signal lookback windows
START_DATE = date(2024, 12, 1)


def get_session():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/124.0.0.0 Safari/537.36",
        "Referer":    "https://www.nseindia.com",
        "Accept":     "text/html,application/xhtml+xml,*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    session = requests.Session()
    print("Getting NSE session cookies...")
    try:
        session.get("https://www.nseindia.com", headers=headers, timeout=20)
    except Exception as e:
        print(f"  Warning: {e}")
    return session, headers


def download_one(session, headers, dt):
    dd   = dt.strftime("%d")
    mm   = dt.strftime("%m")
    yyyy = dt.strftime("%Y")
    fname    = f"sec_bhavdata_full_{dd}{mm}{yyyy}.csv"
    savepath = os.path.join(BHAV_ROOT, fname)

    if os.path.exists(savepath) and os.path.getsize(savepath) > 1000:
        return "skip"

    url = (f"https://nsearchives.nseindia.com/products/content/"
           f"sec_bhavdata_full_{dd}{mm}{yyyy}.csv")
    try:
        resp = session.get(url, headers=headers, timeout=30)
        if resp.status_code == 200 and len(resp.content) > 1000:
            with open(savepath, "wb") as f:
                f.write(resp.content)
            return "ok"
        else:
            return "skip"   # weekend / holiday / not yet published
    except Exception as e:
        print(f"  Error {dt}: {e}")
        return "err"


def main():
    os.makedirs(BHAV_ROOT, exist_ok=True)

    end_date  = (NOW_IST - timedelta(days=1)).date()

    print(f"Bootstrapping bhav data: {START_DATE} → {end_date}")
    print(f"Target folder : {BHAV_ROOT}/")

    session, headers = get_session()
    downloaded = skipped = errors = 0

    current = START_DATE
    while current <= end_date:
        if current.weekday() < 5:       # Mon–Fri only
            result = download_one(session, headers, current)
            if result == "ok":
                print(f"  ✅ {current.strftime('%d-%b-%Y')}")
                downloaded += 1
                if downloaded % 50 == 0:
                    session, headers = get_session()
                time.sleep(0.3)
            elif result == "skip":
                skipped += 1
            else:
                errors += 1
        current += timedelta(days=1)

    print(f"\n✅ Bootstrap complete.")
    print(f"   Downloaded : {downloaded}")
    print(f"   Skipped    : {skipped} (weekends/holidays/existing)")
    print(f"   Errors     : {errors}")


if __name__ == "__main__":
    main()
