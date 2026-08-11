"""Repair bogus delisting dates in the `universe` table.

THE PROBLEM
-----------
Alpha Vantage's LISTING_STATUS delisted CSV stamps some rows with the file's
own snapshot date instead of the real delisting date. 597 of 9,410 delisted
rows carry 2026-08-07; Splunk (acquired March 2024) and Seagen (December 2023)
are both in that set. A backtest trusting those dates would hold dead positions
for years — survivorship bias inverted.

WHY NOT YFINANCE
----------------
Yahoo purges delisted symbols outright: yf history returns ZERO bars for SPLK,
SGEN, ACIA, CLDR, ARUN. There is no last price bar to read.

THE METHOD
----------
Alpha Vantage keeps serving delisted tickers, but FREEZES them: after the final
real trade the close is carried forward unchanged with zero (or negligible)
volume, every day, forever. SPLK shows one distinct close across the 2.4 years
since acquisition. So the true last trading day is the last bar whose volume is
a meaningful fraction of the ticker's own median volume.

Validated against known truth (all within one trading day):
    SPLK 2024-03-15 (Cisco closed 03-18)   SGEN 2023-12-13 (Pfizer 12-14)
    ACIA 2021-02-26 (CSV 03-01)            CLDR 2021-10-07 (CSV 10-08)
    ARUN 2015-05-18 (CSV 2015-05-18, exact — still_trading, no freeze)

The stored value is the LAST TRADABLE DAY, which is what a backtest needs (it
cannot transact after it). AV's CSV convention is the following business day,
hence the consistent one-day offset above.

Only rows stamped with the snapshot date are touched; spot-checks confirm the
other CSV dates are already accurate.

Usage:
    /usr/bin/python3 fix_delisting_dates.py            # apply
    /usr/bin/python3 fix_delisting_dates.py --dry-run  # report only
"""
import os
import re
import sys
import time
import sqlite3
import logging
import statistics

import requests

_zshrc = os.path.expanduser("~/.zshrc")
if os.path.exists(_zshrc):
    with open(_zshrc) as _f:
        for _line in _f:
            _m = re.match(r'^export\s+([A-Z_]+)="?([^"\n]*)"?\s*$', _line.strip())
            if _m:
                os.environ.setdefault(_m.group(1), _m.group(2))

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ".")
from data_pipeline1 import DB_NAME

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("fix_delisting_dates")

API_KEY = os.environ["ALPHA_VANTAGE_API_KEY"]
SNAPSHOT_DATE = "2026-08-07"          # the bogus stamp
CALLS_PER_MINUTE = 75
SLEEP = 60.0 / CALLS_PER_MINUTE
VOLUME_FLOOR_PCT = 0.01               # 1% of median volume counts as "real"
AUDIT_FILE = "delisting_date_fixes.tsv"


def true_last_trading_day(ticker):
    """-> (date_str|None, status). Detects the frozen carry-forward tail."""
    try:
        d = requests.get("https://www.alphavantage.co/query",
                         params={"function": "TIME_SERIES_DAILY", "symbol": ticker,
                                 "outputsize": "full", "apikey": API_KEY},
                         timeout=30).json()
    except Exception as e:
        return None, f"error:{type(e).__name__}"
    ts = d.get("Time Series (Daily)")
    if not ts:
        return None, "no_price_data"
    dates = sorted(ts)
    vols = [float(ts[x]["5. volume"]) for x in dates]
    real = [v for v in vols if v > 0]
    if not real:
        return dates[-1], "no_volume_anywhere"
    threshold = max(1.0, statistics.median(real) * VOLUME_FLOOR_PCT)
    for x in reversed(dates):
        if float(ts[x]["5. volume"]) >= threshold:
            return x, ("frozen_tail" if x != dates[-1] else "still_trading")
    return dates[-1], "no_volume_above_floor"


def main():
    dry = "--dry-run" in sys.argv
    conn = sqlite3.connect(DB_NAME)
    rows = conn.execute(
        "SELECT ticker, name, delisting_date FROM universe "
        "WHERE is_delisted=1 AND delisting_date=? ORDER BY ticker",
        (SNAPSHOT_DATE,)).fetchall()
    log.info(f"{len(rows)} universe rows carry the bogus snapshot date "
             f"{SNAPSHOT_DATE}{' (DRY RUN)' if dry else ''}.")

    fixed = unchanged = failed = 0
    audit = []
    for i, (t, name, old) in enumerate(rows, 1):
        new, status = true_last_trading_day(t)
        time.sleep(SLEEP)
        if new is None:
            failed += 1
            audit.append((t, name or "", old, "", status))
            log.warning(f"  [{i}/{len(rows)}] {t}: {status} — left unchanged")
            continue
        if new == old:
            unchanged += 1
        else:
            fixed += 1
            if not dry:
                conn.execute("UPDATE universe SET delisting_date=? WHERE ticker=?", (new, t))
        audit.append((t, name or "", old, new, status))
        if i % 25 == 0:
            if not dry:
                conn.commit()
            log.info(f"  … {i}/{len(rows)} processed ({fixed} corrected)")
    if not dry:
        conn.commit()

    with open(AUDIT_FILE, "w") as f:
        f.write("ticker\tname\told_date\tnew_date\tstatus\n")
        for r in audit:
            f.write("\t".join(str(x) for x in r) + "\n")

    print("\n" + "=" * 72)
    print(f"DELISTING DATE REPAIR{' (DRY RUN — nothing written)' if dry else ''}")
    print("=" * 72)
    print(f"  corrected : {fixed}")
    print(f"  unchanged : {unchanged}")
    print(f"  failed    : {failed}")
    print(f"  audit     : {AUDIT_FILE}")
    if not dry:
        still = conn.execute("SELECT COUNT(*) FROM universe WHERE is_delisted=1 AND delisting_date=?",
                             (SNAPSHOT_DATE,)).fetchone()[0]
        print(f"  rows still carrying {SNAPSHOT_DATE}: {still}")
    conn.close()


if __name__ == "__main__":
    main()
