"""Durable, resumable universe classifier — Alpha Vantage Pro.

Runs at the Pro ceiling: 75 requests/minute (0.8s between calls), NO daily cap.
There is no daily-quota handling — the former free-tier 25/day logic is gone.
The run stops only after 5 consecutive STRUCTURAL failures (bad symbol, dead
key, network down); per-minute burst limits are absorbed by the exponential-
backoff retry inside _av_fetch and never reach the failure counter.

Behaviour:
  - Skips tickers that already have all 3 statements cached AND a classification
    (resumable — safe to re-run; only does outstanding work).
  - Per-statement skip: never re-fetches a statement already stored (saves credits
    on partial-load recovery).
  - Runs the full stage-change -> Supabase -> alert chain via run_classification.

Usage:
  # one ticker or a few
  python3 run_universe.py MSFT NVDA SNOW
  # the whole built-in TICKERS list (default)
  python3 run_universe.py
  # a custom list from a file (one ticker per line)
  python3 run_universe.py --file my_tickers.txt

Credentials are read from ~/.zshrc exports if not already in the environment.
"""
import os
import re
import sys
import time
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

from data_pipeline1 import (
    TICKERS, DB_NAME, setup_database,
    fetch_and_store_price_data, fetch_and_store_fundamentals,
    compute_and_store_metrics, run_classification,
    AV_SLEEP, AV_CALLS_PER_MINUTE, EXCLUDED_TICKERS_NON_USD,
    STAGE_CHANGES_THIS_RUN,
)
import data_pipeline1 as _dp   # AV_LAST_FAILURE_REASON is module-level state

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("run_universe")

STATEMENTS = ["INCOME_STATEMENT", "CASH_FLOW", "BALANCE_SHEET"]
# Structural-failure stop only (dead key / bad symbols / network).
# Per-minute throttling is handled by _av_fetch retries, never counted here.
CONSEC_FAIL_LIMIT = 5


def parse_args(argv):
    if "--file" in argv:
        path = argv[argv.index("--file") + 1]
        with open(path) as f:
            return [ln.strip().upper() for ln in f if ln.strip()]
    explicit = [a.upper() for a in argv[1:] if not a.startswith("--")]
    return explicit or list(TICKERS)


def main():
    tickers = parse_args(sys.argv)
    conn = sqlite3.connect(DB_NAME)
    setup_database(conn)

    already = {r[0] for r in conn.execute("SELECT ticker FROM classifications")}
    tier = f"PRO ({AV_CALLS_PER_MINUTE}/min, no daily cap)"
    todo = [t for t in tickers if t not in already]
    log.info(f"AV tier: {tier}  |  sleep={AV_SLEEP:.2f}s  |  stop after {CONSEC_FAIL_LIMIT} consecutive failures")
    log.info(f"{len(tickers)} requested, {len(tickers)-len(todo)} already classified, {len(todo)} to process.")

    results, consec, av_calls = [], 0, 0
    for i, t in enumerate(todo, 1):
        log.info(f"[{i}/{len(todo)}] {t}")
        fetch_and_store_price_data(conn, t)

        got, fail_reasons = 0, set()
        for stmt in STATEMENTS:
            have = conn.execute(
                "SELECT COUNT(*) FROM fundamentals WHERE ticker=? AND statement_type=?",
                (t, stmt)).fetchone()[0]
            if have > 0:
                got += have
                continue
            n = fetch_and_store_fundamentals(conn, t, stmt)
            av_calls += 1
            if n == 0 and _dp.AV_LAST_FAILURE_REASON:
                fail_reasons.add(_dp.AV_LAST_FAILURE_REASON)
            got += n
            time.sleep(AV_SLEEP)

        if got == 0:
            consec += 1
            if "rate_limit" in fail_reasons:
                reason = "rate limit persisted through retries"
            elif "api_error" in fail_reasons:
                reason = "AV rejected the request (bad symbol/function)"
            elif "timeout" in fail_reasons or "exception" in fail_reasons:
                reason = "network error"
            else:
                reason = "no data returned (delisted / not covered)"
            log.warning(f"  {t}: 0 records ({reason})  [{consec}/{CONSEC_FAIL_LIMIT}]")
            results.append((t, "FAIL", reason))
            if consec >= CONSEC_FAIL_LIMIT:
                log.error(f"STOPPING: {CONSEC_FAIL_LIMIT} consecutive failures "
                          f"(structural — check API key / symbols / network).")
                break
            continue
        consec = 0

        ok = compute_and_store_metrics(conn, t)
        if not ok:
            note = "non-USD reporting currency" if t in EXCLUDED_TICKERS_NON_USD else "metrics failed"
            log.warning(f"  {t}: {note}")
            results.append((t, "EXCLUDED" if t in EXCLUDED_TICKERS_NON_USD else "PARTIAL", note))
            continue
        run_classification(conn, t)
        cell = conn.execute("SELECT matrix_cell, bm_method, bm_confidence FROM classifications WHERE ticker=?",
                            (t,)).fetchone()
        log.info(f"  ✓ {t} -> {cell[0]} ({cell[1]}, {cell[2]})")
        results.append((t, "OK", cell[0]))

    # Summary
    print("\n" + "=" * 70)
    print(f"SUMMARY  (AV calls this run: {av_calls})")
    print("=" * 70)
    for t, status, detail in results:
        icon = {"OK": "✓", "EXCLUDED": "🚫", "PARTIAL": "⚠️", "FAIL": "✗"}.get(status, "?")
        print(f"  {icon}  {t:6s} {status:9s}  {detail}")
    n_total = conn.execute("SELECT COUNT(*) FROM classifications").fetchone()[0]
    print(f"\nUniverse total: {n_total} classified tickers")
    if STAGE_CHANGES_THIS_RUN:
        print(f"Stage changes this run: {STAGE_CHANGES_THIS_RUN}")
    if EXCLUDED_TICKERS_NON_USD:
        print(f"Excluded (non-USD): {sorted(EXCLUDED_TICKERS_NON_USD)}")
    conn.close()


if __name__ == "__main__":
    main()
