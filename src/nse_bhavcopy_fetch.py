"""Fetch missing recent NSE sessions, validate dates, fail closed on missing data.

NSE holidays are accepted only from its official holiday-master response.
No filename, HTML response, or HTTP success alone proves valid market data.
"""
import io
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

IST = timezone(timedelta(hours=5, minutes=30))
HEADERS = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.nseindia.com'}


def validate_csv(content, expected):
    data = pd.read_csv(io.BytesIO(content))
    data.columns = data.columns.str.strip()
    required = {'SYMBOL', 'SERIES', 'DATE1', 'CLOSE_PRICE', 'OPEN_PRICE', 'LOW_PRICE', 'HIGH_PRICE'}
    if not required.issubset(data.columns) or len(data) < 100:
        raise ValueError('Invalid/empty NSE bhavcopy; required columns or rows missing')
    dates = pd.to_datetime(data['DATE1'].str.strip(), dayfirst=True, errors='raise').dt.date
    if set(dates) != {expected}:
        raise ValueError(f'Bhavcopy date does not match requested session {expected}')


def target_session(now):
    # Scheduled at 20:00 IST; manual runs before market close use prior day.
    target = now.date()
    if now.hour < 16:
        target -= timedelta(days=1)
    while target.weekday() >= 5:
        target -= timedelta(days=1)
    return target


def main():
    now = datetime.now(IST)
    target = target_session(now)
    root = Path('bhav_data')
    root.mkdir(exist_ok=True)
    last = None
    if Path('db/eq_data.parquet').exists():
        last = pd.to_datetime(pd.read_parquet('db/eq_data.parquet', columns=['DATE1'])['DATE1']).max().date()
    start = last + timedelta(days=1) if last else target
    if (target - start).days > 31:
        raise RuntimeError('Over 31 days of data missing; run historical bhav backfill first')
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504])
    session.mount('https://', HTTPAdapter(max_retries=retry))
    holidays = set()
    try:
        session.get('https://www.nseindia.com', headers=HEADERS, timeout=30)
        response = session.get('https://www.nseindia.com/api/holiday-master?type=trading', headers=HEADERS, timeout=30)
        response.raise_for_status()
        for item in response.json().get('CM', []):
            holidays.add(datetime.strptime(item['tradingDate'], '%d-%b-%Y').date())
    except (requests.RequestException, ValueError, KeyError):
        print('Holiday calendar unavailable; missing weekday files will fail, not publish stale data')
    new_files = 0
    date = start
    while date <= target:
        if date.weekday() >= 5 or date in holidays:
            print('Non-trading date:', date)
            date += timedelta(days=1)
            continue
        name = 'sec_bhavdata_full_' + date.strftime('%d%m%Y') + '.csv'
        path = root / name
        if path.exists():
            content = path.read_bytes()
        else:
            response = session.get('https://nsearchives.nseindia.com/products/content/' + name,
                                   headers=HEADERS, timeout=60)
            response.raise_for_status()
            content = response.content
        validate_csv(content, date)
        temporary = path.with_suffix('.tmp')
        temporary.write_bytes(content)
        temporary.replace(path)
        new_files += 1
        print('Validated NSE session:', date)
        date += timedelta(days=1)
    print(f'NSE fetch complete: {new_files} session files; previous EQ date={last}; requested through={target}')
    output = os.environ.get('GITHUB_OUTPUT')
    if output:
        with open(output, 'a') as handle:
            handle.write(f'new_file={str(new_files > 0).lower()}\n')


if __name__ == '__main__':
    main()
