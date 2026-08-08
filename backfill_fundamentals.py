"""Full-history, point-in-time fundamentals backfill — Alpha Vantage Pro.

Pulls the COMPLETE available quarterly history (not the pipeline's 20-quarter
cap) for every universe ticker, across four endpoints:

    INCOME_STATEMENT, BALANCE_SHEET, CASH_FLOW, EARNINGS

and stores every quarter in the `fundamentals` table with:
  - fiscal_date   : the fiscal-period end (fiscalDateEnding)
  - reported_date : the ACTUAL filing/report date (AV EARNINGS `reportedDate`)
  - raw_data_json : the full raw report dict for that quarter

`reported_date` is the point of the whole exercise: it lets the backtester
filter on when data was actually knowable, eliminating look-ahead bias. AV's
three statement endpoints do NOT carry a filing date — only EARNINGS does — so
we fetch EARNINGS first, build a {fiscalDateEnding -> reportedDate} map, and
stamp it onto every statement quarter by fiscal period. EARNINGS rows are also
stored in their own right (statement_type='EARNINGS'), carrying reportedEPS /
estimatedEPS / surprise for later use.

After this completes, all backtest computation reads from the local DB with no
further API calls — historical shares (commonStockSharesOutstanding), D&A
(depreciationDepletionAndAmortization) and every other matrix input are already
inside the stored raw JSON.

Design notes:
  - Full history: no `[:max_quarters]` slice. AV returns ~80 quarters for
    mature names, full post-IPO history for younger ones.
  - Idempotent / resumable: INSERT OR REPLACE on (ticker, statement_type,
    fiscal_date). A statement already stored WITH a reported_date on every row
    is skipped, so re-runs cost ~0 API calls. A prior 20-quarter partial (from
    the live pipeline, reported_date NULL) is detected and re-fetched so it gets
    both full depth and filing dates.
  - Pro-tier only: reuses _av_fetch's exponential backoff; no daily cap.

Usage:
    /usr/bin/python3 backfill_fundamentals.py                 # all classified tickers
    /usr/bin/python3 backfill_fundamentals.py MSFT NVDA       # explicit list
    /usr/bin/python3 backfill_fundamentals.py --file names.txt

Credentials are read from ~/.zshrc exports if not already in the environment.
"""
import os
import re
import sys
import time
import json
import sqlite3
import logging

# --- Load ~/.zshrc exports into the environment (idempotent) ---------------
_zshrc = os.path.expanduser("~/.zshrc")
if os.path.exists(_zshrc):
    with open(_zshrc) as _f:
        for _line in _f:
            _m = re.match(r'^export\s+([A-Z_]+)="?([^"\n]*)"?\s*$', _line.strip())
            if _m:
                os.environ.setdefault(_m.group(1), _m.group(2))

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ".")

from data_pipeline1 import DB_NAME, setup_database, _av_fetch, AV_SLEEP, AV_CALLS_PER_MINUTE
import data_pipeline1 as _dp
import pandas as pd
import yfinance as yf

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("backfill_fundamentals")

STATEMENTS = ["INCOME_STATEMENT", "BALANCE_SHEET", "CASH_FLOW"]
CONSEC_FAIL_LIMIT = 5   # structural stop (dead key / network); throttles handled by _av_fetch


def parse_args(argv):
    if "--file" in argv:
        path = argv[argv.index("--file") + 1]
        with open(path) as f:
            return [ln.strip().upper() for ln in f if ln.strip()]
    return [a.upper() for a in argv[1:] if not a.startswith("--")]


def _already_complete(conn, ticker) -> bool:
    """True if all 4 statement types exist for this ticker AND every row has a
    non-NULL reported_date (i.e. a prior full backfill, not a live 20q partial)."""
    for stmt in STATEMENTS + ["EARNINGS"]:
        total = conn.execute(
            "SELECT COUNT(*) FROM fundamentals WHERE ticker=? AND statement_type=?",
            (ticker, stmt)).fetchone()[0]
        if total == 0:
            return False
        # EARNINGS always has reported_date; statements need it stamped.
        missing = conn.execute(
            "SELECT COUNT(*) FROM fundamentals "
            "WHERE ticker=? AND statement_type=? AND reported_date IS NULL",
            (ticker, stmt)).fetchone()[0]
        if missing > 0:
            return False
    return True


def _av_fetch_retry(ticker, function, tries=3, backoff=2.0):
    """AV occasionally returns a spurious 'Invalid API call' for a valid symbol's
    fundamentals (observed on SNPS/ABNB/ACHR — succeeds on retry). _av_fetch
    treats that as a hard api_error and does NOT retry it, so wrap it here with a
    short backoff. Rate-limit messages are already retried inside _av_fetch."""
    for attempt in range(tries):
        data = _av_fetch(ticker, function)
        if data is not None:
            return data
        if _dp.AV_LAST_FAILURE_REASON == "api_error" and attempt < tries - 1:
            time.sleep(backoff)
            continue
        return None
    return None


def _fetch_earnings_map(ticker):
    """Fetch EARNINGS; return (rows_for_storage, {fiscalDateEnding: reportedDate})."""
    data = _av_fetch_retry(ticker, "EARNINGS")
    if data is None:
        return [], {}
    qe = data.get("quarterlyEarnings", []) or []
    rows, rmap = [], {}
    for e in qe:
        fd = e.get("fiscalDateEnding")
        rd = e.get("reportedDate")
        if not fd:
            continue
        if rd and rd != "None":
            rmap[fd] = rd
        rows.append((ticker, "EARNINGS", fd, json.dumps(e), rd if rd and rd != "None" else None))
    return rows, rmap


def _fetch_statement(ticker, statement_type, rmap):
    """Fetch full quarterly history for one statement; stamp reported_date from rmap."""
    data = _av_fetch_retry(ticker, statement_type)
    if data is None:
        return []
    reports = data.get("quarterlyReports", []) or []
    rows = []
    for rep in reports:                       # NO cap — full history
        fd = rep.get("fiscalDateEnding")
        if not fd:
            continue
        rows.append((ticker, statement_type, fd, json.dumps(rep), rmap.get(fd)))
    return rows


def _backfill_price_history(conn, ticker) -> int:
    """Pull MAX available daily price history (yfinance, no AV credits) so the
    price series reaches as deep as the fundamentals. Without this the backtest
    can't price mcap/EV before ~2021 (the live pipeline only stored 5y). Stored
    with INSERT OR IGNORE, so existing rows are untouched and re-runs are cheap.
    price_data is local-only (build_repo_db.py strips it), so depth is free."""
    try:
        hist = yf.Ticker(ticker).history(period="max", auto_adjust=True)
    except Exception as e:
        log.warning(f"  {ticker}: price history fetch failed: {e}")
        return 0
    if hist is None or hist.empty:
        return 0
    hist.reset_index(inplace=True)
    records = []
    for _, row in hist.iterrows():
        dv = row["Date"]
        if hasattr(dv, "tzinfo") and dv.tzinfo is not None:
            dv = dv.tz_convert("UTC").tz_localize(None)
        ds = pd.Timestamp(dv).strftime("%Y-%m-%d")
        records.append((ticker, ds, float(row["Open"]), float(row["High"]),
                        float(row["Low"]), float(row["Close"]), int(row["Volume"])))
    conn.executemany("""
        INSERT OR IGNORE INTO price_data (ticker, date, open, high, low, close, volume)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, records)
    conn.commit()
    return len(records)


def _store(conn, rows):
    conn.executemany("""
        INSERT OR REPLACE INTO fundamentals
            (ticker, statement_type, fiscal_date, raw_data_json, reported_date)
        VALUES (?, ?, ?, ?, ?)
    """, rows)
    conn.commit()


def main():
    conn = sqlite3.connect(DB_NAME)
    setup_database(conn)  # ensures reported_date column exists

    tickers = parse_args(sys.argv)
    if not tickers:
        tickers = [r[0] for r in conn.execute(
            "SELECT ticker FROM classifications ORDER BY ticker")]

    todo = [t for t in tickers if not _already_complete(conn, t)]
    log.info(f"AV PRO ({AV_CALLS_PER_MINUTE}/min, no daily cap) | sleep={AV_SLEEP:.2f}s")
    log.info(f"{len(tickers)} requested, {len(tickers)-len(todo)} already backfilled, "
             f"{len(todo)} to process (~{len(todo)*4} calls).")

    results, consec, av_calls = [], 0, 0
    for i, t in enumerate(todo, 1):
        log.info(f"[{i}/{len(todo)}] {t}")

        earn_rows, rmap = _fetch_earnings_map(t)
        av_calls += 1
        time.sleep(AV_SLEEP)
        if earn_rows:
            _store(conn, earn_rows)

        stmt_counts = {}
        got_any = bool(earn_rows)
        for stmt in STATEMENTS:
            rows = _fetch_statement(t, stmt, rmap)
            av_calls += 1
            time.sleep(AV_SLEEP)
            if rows:
                _store(conn, rows)
                got_any = True
            stmt_counts[stmt] = len(rows)

        if not got_any:
            consec += 1
            reason = _dp.AV_LAST_FAILURE_REASON or "no data"
            log.warning(f"  {t}: 0 records ({reason})  [{consec}/{CONSEC_FAIL_LIMIT}]")
            results.append((t, "FAIL", reason))
            if consec >= CONSEC_FAIL_LIMIT:
                log.error(f"STOPPING: {CONSEC_FAIL_LIMIT} consecutive structural failures.")
                break
            continue
        consec = 0

        # Deepen price history to match the fundamentals (yfinance, no AV cost).
        n_px = _backfill_price_history(conn, t)

        stamped = sum(1 for r in _fetch_reported_coverage(conn, t))
        inc_n = stmt_counts.get("INCOME_STATEMENT", 0)
        px_span = conn.execute(
            "SELECT MIN(date), MAX(date) FROM price_data WHERE ticker=?", (t,)).fetchone()
        log.info(f"  ✓ {t}  earnings={len(earn_rows)}  "
                 f"inc={inc_n} bs={stmt_counts.get('BALANCE_SHEET',0)} "
                 f"cf={stmt_counts.get('CASH_FLOW',0)}  filing-dates on {stamped} quarters  "
                 f"price {px_span[0]}..{px_span[1]}")
        results.append((t, "OK", f"{inc_n}q history, price from {px_span[0]}"))

    # Summary
    print("\n" + "=" * 72)
    print(f"BACKFILL SUMMARY  (AV calls this run: {av_calls})")
    print("=" * 72)
    for t, status, detail in results:
        icon = {"OK": "✓", "FAIL": "✗"}.get(status, "?")
        print(f"  {icon}  {t:6s} {status:5s}  {detail}")
    n_rows = conn.execute("SELECT COUNT(*) FROM fundamentals").fetchone()[0]
    n_dated = conn.execute("SELECT COUNT(*) FROM fundamentals WHERE reported_date IS NOT NULL").fetchone()[0]
    print(f"\nfundamentals rows: {n_rows}  ({n_dated} with a filing date)")
    conn.close()


def _fetch_reported_coverage(conn, ticker):
    return conn.execute(
        "SELECT 1 FROM fundamentals WHERE ticker=? AND statement_type='INCOME_STATEMENT' "
        "AND reported_date IS NOT NULL", (ticker,)).fetchall()


if __name__ == "__main__":
    main()
