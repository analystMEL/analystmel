"""Build the slimmed database that ships to the Streamlit Cloud repo.

The app queries exactly four tables — classifications, computed_metrics,
valuations, fh_stage_history. `price_data` and `fundamentals` are pipeline-only
and account for ~90% of the file, so shipping them costs nothing but repo size.

That matters because the DB is committed to git: every push writes a *fresh
full blob*, so a 50MB DB adds 50MB to history each time. The full database
stays at the scratch root — the pipeline needs `fundamentals` for FH-history
backfill and for resumability (it skips statements it has already cached).

Usage:  python3 build_repo_db.py
"""
import os
import shutil
import sqlite3

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "valoura_backtest.db")
DST = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "Valuora_Previous_UI", "valoura_backtest.db")

# Tables the deployed app actually reads. Verified by enumerating every
# FROM/JOIN in app.py — keep this in sync if the app grows a new query.
APP_TABLES = {"classifications", "computed_metrics", "valuations", "fh_stage_history"}
DROP_TABLES = ["price_data", "fundamentals"]


def main():
    src_mb = os.path.getsize(SRC) / 1024 / 1024
    shutil.copyfile(SRC, DST)

    conn = sqlite3.connect(DST)
    for t in DROP_TABLES:
        conn.execute(f"DROP TABLE IF EXISTS {t}")
    conn.commit()
    conn.execute("VACUUM")

    kept = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    missing = APP_TABLES - kept
    if missing:
        raise SystemExit(f"ABORT: slimmed DB is missing app tables {missing}")
    n = conn.execute("SELECT COUNT(*) FROM classifications").fetchone()[0]
    conn.close()

    dst_mb = os.path.getsize(DST) / 1024 / 1024
    print(f"full:    {src_mb:6.1f} MB  ({SRC})")
    print(f"shipped: {dst_mb:6.1f} MB  ({DST})")
    print(f"dropped: {', '.join(DROP_TABLES)}")
    print(f"{n} classified tickers preserved; app tables intact.")


if __name__ == "__main__":
    main()
