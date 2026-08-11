"""Triage Review List B down to a human-reviewable shortlist.

Step 1 — HARD FILTER: does Alpha Vantage still hold financial statements for
this ticker? A ticker with no INCOME_STATEMENT cannot be classified or valued
by the engine at all, so reviewing it is wasted effort. This is a fact check,
not a judgement call, and it removes ~90% of the list.

Step 2 — AUTO-TRIAGE the survivors by name into:
    accept    obvious tech, no human needed
    reject    obvious non-tech (biotech / mining / energy / banking / airlines)
    review    genuinely ambiguous -> the only rows a human should look at

Results are cached in the `delisted_statement_probe` table, so re-running costs
no API calls.

Usage:
    /usr/bin/python3 triage_review_list.py
"""
import os
import re
import sys
import csv
import time
import json
import sqlite3
import logging

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
log = logging.getLogger("triage")

API_KEY = os.environ["ALPHA_VANTAGE_API_KEY"]
SLEEP = 60.0 / 75
IN_FILE = "review_list_B.tsv"

# Strong tech signals — accept without human review.
ACCEPT = [
    "SOFTWARE", "SEMICONDUCTOR", "MICROSYSTEMS", "MICROELECTRON", "CYBER",
    "DATA SYSTEMS", "COMPUTER", "NETWORKS", "NETWORKING", "TECHNOLOGIES",
    "TECHNOLOGY", "MICRO DEVICES", "CIRCUITS", "WIRELESS", "TELECOM",
    "COMMUNICATIONS", "DIGITAL", "INTERNET", "CLOUD", "ROBOTICS",
]
# Strong non-tech signals — reject without human review. Checked FIRST, because
# "Cellect Biotechnology" and "Nautilus Biotechnology" contain tech-ish tokens.
REJECT = [
    "BIOTECH", "PHARMA", "THERAPEUT", "ONCOLOG", "MEDICAL", "HEALTH", "BIOSCI",
    "GENOM", "DIAGNOST", "CLINICAL", "HOSPITAL", "SURGIC", "DENTAL", "VACCIN",
    "MINING", "GOLD", "SILVER", "COPPER", "PETROLEUM", "OIL", "GAS ", "ENERGY",
    "SOLAR", "COAL", "URANIUM", "BANCORP", "BANCSHARES", "BANK", "INSURANCE",
    "REALTY", "PROPERTIES", "REIT", "AIRLINE", "AIRWAYS", "AIR LINES",
    "RESTAURANT", "APPAREL", "RETAIL", "FOODS", "BEVERAGE", "AGRITECH",
    "AGRICULT", "WATER", "UTILIT", "SHIPPING", "MARINE", "TRUCKING",
]


def has_statements(ticker):
    """-> (n_quarters, currency|None). 0 quarters == unusable."""
    try:
        d = requests.get("https://www.alphavantage.co/query",
                         params={"function": "INCOME_STATEMENT", "symbol": ticker,
                                 "apikey": API_KEY}, timeout=25).json()
    except Exception:
        return -1, None
    q = d.get("quarterlyReports") or []
    cur = q[0].get("reportedCurrency") if q else None
    return len(q), cur


def verdict(name):
    n = (name or "").upper()
    if any(k in n for k in REJECT):
        return "reject"
    if any(k in n for k in ACCEPT):
        return "accept"
    return "review"


def main():
    conn = sqlite3.connect(DB_NAME)
    conn.execute("""CREATE TABLE IF NOT EXISTS delisted_statement_probe (
                        ticker TEXT PRIMARY KEY, quarters INTEGER,
                        currency TEXT, probed_at TEXT)""")
    conn.commit()

    rows = list(csv.DictReader(open(IN_FILE), delimiter="\t"))
    cached = {r[0]: r[1] for r in conn.execute("SELECT ticker, quarters FROM delisted_statement_probe")}
    todo = [r for r in rows if r["ticker"] not in cached]
    log.info(f"{len(rows)} candidates, {len(cached)} already probed, {len(todo)} to probe "
             f"(~{len(todo)*SLEEP/60:.0f} min).")

    for i, r in enumerate(todo, 1):
        n, cur = has_statements(r["ticker"])
        conn.execute("INSERT OR REPLACE INTO delisted_statement_probe VALUES (?,?,?,datetime('now'))",
                     (r["ticker"], n, cur))
        if i % 25 == 0:
            conn.commit()
            log.info(f"  … {i}/{len(todo)} probed")
        time.sleep(SLEEP)
    conn.commit()

    probe = {r[0]: (r[1], r[2]) for r in
             conn.execute("SELECT ticker, quarters, currency FROM delisted_statement_probe")}

    usable, no_data = [], []
    for r in rows:
        q, cur = probe.get(r["ticker"], (0, None))
        rec = dict(r, quarters=q, currency=cur or "", verdict=verdict(r["name"]))
        (usable if q and q > 0 else no_data).append(rec)

    usable.sort(key=lambda x: (-x["quarters"], x["ticker"]))
    with open("review_shortlist.tsv", "w") as f:
        f.write("ticker\tname\tdelisting_date\tquarters\tcurrency\tsuggested\tdecision\n")
        for r in usable:
            f.write(f"{r['ticker']}\t{r['name']}\t{r['delisting_date']}\t{r['quarters']}\t"
                    f"{r['currency']}\t{r['verdict']}\t\n")

    from collections import Counter
    c = Counter(r["verdict"] for r in usable)
    print("\n" + "=" * 72)
    print("TRIAGE RESULT")
    print("=" * 72)
    print(f"  candidates in Review List B : {len(rows)}")
    print(f"  NO financial statements     : {len(no_data)}  ({100*len(no_data)/len(rows):.0f}%) — unusable, dropped")
    print(f"  USABLE (have statements)    : {len(usable)}")
    print(f"     auto-accept (obvious tech)   : {c.get('accept',0)}")
    print(f"     auto-reject (obvious non-tech): {c.get('reject',0)}")
    print(f"     NEEDS YOUR REVIEW            : {c.get('review',0)}")
    print(f"\n  shortlist -> review_shortlist.tsv")
    conn.close()


if __name__ == "__main__":
    main()
