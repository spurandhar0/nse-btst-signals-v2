"""
NSE Bhavcopy Fetcher
=====================
Downloads today's NSE EQ bhavcopy and saves to bhav_data/
Outputs GitHub Actions step output: new_file=true/false
"""

import os, glob, requests
from datetime import datetime, timedelta, timezone

IST     = timezone(timedelta(hours=5, minutes=30))
NOW_IST = datetime.now(tz=IST)
BHAV_ROOT = "bhav_data"


def set_gha_output(key, value):
    gha_output = os.environ.get("GITHUB_OUTPUT", "")
    if gha_output:
        with open(gha_output, "a") as f:
            f.write(f"{key}={value}\n")


def fetch_bhav():
    dd   = NOW_IST.strftime("%d")
    mm   = NOW_IST.strftime("%m")
    yyyy = NOW_IST.strftime("%Y")
    mon3 = NOW_IST.strftime("%b")

    fname    = f"sec_bhavdata_full_{dd}{mm}{yyyy}.csv"
    savepath = os.path.join(BHAV_ROOT, fname)

    date_pattern = f"*_{dd}{mm}{yyyy}.csv"
    existing = (
        glob.glob(os.path.join(BHAV_ROOT, "**", date_pattern), recursive=True)
        + glob.glob(os.path.join(BHAV_ROOT, date_pattern))
    )
    existing = list(set(existing))

    if existing:
        print(f"\u26a0\ufe0f  File for {dd}-{mon3}-{yyyy} already exists: {existing[0]}")
        print("\u23ed\ufe0f  Skipping download.")
        set_gha_output("new_file", "false")
        return False

    url = (
        f"https://nsearchives.nseindia.com/products/content/"
        f"sec_bhavdata_full_{dd}{mm}{yyyy}.csv"
    )
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/124.0.0.0 Safari/537.36",
        "Referer": "https://www.nseindia.com",
        "Accept": "text/html,application/xhtml+xml,*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    session = requests.Session()
    print("Getting NSE session cookies...")
    session.get("https://www.nseindia.com", headers=headers, timeout=20)

    print(f"Downloading bhav: {url}")
    resp = session.get(url, headers=headers, timeout=60)

    if resp.status_code != 200:
        print(f"\u274c NSE Bhavcopy FAILED \u2014 HTTP {resp.status_code}")
        print(f"   NSE may not have published today's bhavcopy yet.")
        set_gha_output("new_file", "false")
        return False

    os.makedirs(BHAV_ROOT, exist_ok=True)
    with open(savepath, "wb") as f:
        f.write(resp.content)

    size_kb = os.path.getsize(savepath) / 1024
    with open(savepath, "r") as f:
        row_count = sum(1 for _ in f) - 1

    print(f"\u2705 Saved   : {savepath} ({size_kb:.1f} KB)")
    print(f"\u2705 Date    : {dd}-{mon3}-{yyyy}")
    print(f"\u2705 Rows    : {row_count:,}")
    set_gha_output("new_file", "true")
    return True


if __name__ == "__main__":
    print("=" * 50)
    print(f"NSE Bhavcopy Fetch | {NOW_IST.strftime('%d-%b-%Y %H:%M IST')}")
    print("=" * 50)
    os.makedirs(BHAV_ROOT, exist_ok=True)
    fetch_bhav()
