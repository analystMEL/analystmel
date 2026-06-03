"""One-time bootstrap of the fh_stage_history table.

After the pipeline change lands (data_pipeline1.py adds backfill_fh_history()
and run_classification() auto-calls it), every future classification run
also refreshes the last 8 quarters of FH-stage history for that ticker.

This script is used once to backfill historical data for tickers that were
classified BEFORE the pipeline change. It's safe to re-run any time —
INSERT OR REPLACE keeps it idempotent.

Zero AV credits — works purely off cached fundamentals.

Usage:
    ALPHA_VANTAGE_API_KEY="..." GOOGLE_API_KEY="..." python3 backfill_fh_history.py
    (env vars only needed because data_pipeline1.py loads them at import time;
     no actual AV calls are made.)
"""
import os, sys, sqlite3
os.chdir("/Users/ommelwani/.gemini/antigravity/scratch")
sys.path.insert(0, ".")

from data_pipeline1 import DB_NAME, setup_database, backfill_fh_history

conn = sqlite3.connect(DB_NAME)
setup_database(conn)  # ensures fh_stage_history table exists

tickers = [
    r[0] for r in conn.execute(
        "SELECT ticker FROM classifications ORDER BY ticker"
    ).fetchall()
]

print(f"Backfilling {len(tickers)} classified tickers × 8 quarters …")
print("=" * 78)

total_written = 0
for i, t in enumerate(tickers, 1):
    n = backfill_fh_history(conn, t, n_quarters=8)
    total_written += n
    # Show the trajectory inline (oldest → newest)
    stages = conn.execute(
        "SELECT fh_stage FROM fh_stage_history WHERE ticker=? "
        "ORDER BY as_of_date ASC",
        (t,),
    ).fetchall()
    chain = " → ".join(str(s[0]) for s in stages) if stages else "(no data)"
    print(f"  [{i:2d}/{len(tickers)}] {t:6s}  {n} quarters  ({chain})")

print("=" * 78)
print(f"Total rows written: {total_written}")
print("Done.")
conn.close()
