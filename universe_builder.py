"""universe_builder.py — build the backtest universe from Alpha Vantage LISTING_STATUS.

Pipeline:
  1. Load listing_status_active.csv + listing_status_delisted.csv, report shape and
     date population. If ipoDate/delistingDate are sparse, STOP (do not crawl).
  2. Filter both to assetType=='Stock' and exchange in {NASDAQ, NYSE}; the delisted
     file additionally to delistingDate >= 2012-01-01.
  3. For each surviving ticker call AV OVERVIEW for Sector/Industry/MarketCap.
     75 calls/min. Every response is cached to disk (overview_cache table) so this
     never needs re-running. Empty OVERVIEW (common for delisted names) is recorded
     as sector_unknown, NOT dropped.
  4. Build the `universe` table.
  5. Set in_backtest=1 where sector is Technology / a tech-adjacent industry AND
     market_cap > $1B, PLUS every ticker already in `classifications`.
  6. Print two review lists: delisted & in_backtest, and delisted & sector_unknown
     whose NAME looks tech (for manual confirmation).

Does NOT modify data_pipeline1.py and does NOT start any fundamentals backfill.
Resumable: re-running skips cached tickers and rebuilds the table from cache.

Usage:
    /usr/bin/python3 universe_builder.py            # full run (inspect -> crawl -> build)
    /usr/bin/python3 universe_builder.py --inspect  # step 1 only, then stop
    /usr/bin/python3 universe_builder.py --build     # skip crawl, (re)build table from cache
"""
import os
import re
import sys
import time
import json
import sqlite3
import logging
from datetime import datetime

import pandas as pd

# --- Load ~/.zshrc exports (AV key) without requiring an interactive shell -----
_zshrc = os.path.expanduser("~/.zshrc")
if os.path.exists(_zshrc):
    with open(_zshrc) as _f:
        for _line in _f:
            m = re.match(r'^export\s+([A-Z_]+)="?([^"\n]*)"?\s*$', _line.strip())
            if m:
                os.environ.setdefault(m.group(1), m.group(2))

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ".")

# Reuse the tested AV fetch (rate-limit backoff, error normalisation). We only
# READ from data_pipeline1 — this script never modifies it.
from data_pipeline1 import _av_fetch, DB_NAME
import data_pipeline1 as _dp

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("universe_builder")

ACTIVE_CSV = "listing_status_active.csv"
DELISTED_CSV = "listing_status_delisted.csv"
EXCHANGES = {"NASDAQ", "NYSE"}
DELISTED_SINCE = "2012-01-01"
CALLS_PER_MINUTE = 75
SLEEP = 60.0 / CALLS_PER_MINUTE          # 0.8s — minimum spacing between CALL STARTS

# A fixed post-call sleep undershoots the ceiling badly: 0.8s sleep + ~0.4s of
# request latency = 1.2s/call = 50/min, not 75. Pace from call-start to
# call-start instead, sleeping only the remainder. Self-correcting (slow
# responses shorten the wait to zero) and can never exceed CALLS_PER_MINUTE.
_last_call_ts = 0.0


def _pace():
    global _last_call_ts
    wait = SLEEP - (time.monotonic() - _last_call_ts)
    if wait > 0:
        time.sleep(wait)
    _last_call_ts = time.monotonic()
DATE_SPARSE_THRESHOLD = 0.50             # <50% populated => "sparse", stop
MIN_MARKET_CAP = 1_000_000_000           # $1B
TECH_MC_FLOOR = MIN_MARKET_CAP

# Industries that are technology-adjacent even when AV files them under a
# non-Technology sector (Communication Services, Consumer Cyclical, etc.).
# Matched case-insensitively as substrings of the AV Industry string.
TECH_ADJACENT_INDUSTRY = [
    "SEMICONDUCTOR", "SOFTWARE", "INFORMATION TECHNOLOGY", "IT SERVICES",
    "COMPUTER", "COMMUNICATION EQUIPMENT", "ELECTRONIC", "SCIENTIFIC & TECHNICAL",
    "INTERNET CONTENT", "INTERNET RETAIL", "ELECTRONIC GAMING", "SOLAR",
    "TECHNOLOGY", "DATA", "CLOUD", "CYBER",
]
# Name tokens that suggest a delisted sector_unknown ticker might be tech —
# used only to build a manual-review shortlist, never to auto-include.
TECH_NAME_HINTS = [
    "SOFTWARE", "SEMICONDUCTOR", "SYSTEMS", "TECHNOLOG", "TECH", "CYBER",
    "DATA", "CLOUD", "DIGITAL", "NETWORK", "INTERNET", "MICRO", "SILICON",
    "ROBOT", "AI ", " AI", "QUANTUM", "PHOTONIC", "SENSOR", "WIRELESS",
    "COMPUTING", "COMPUTER", "SOLAR", "BIOINFORMATIC", "PLATFORM", "WEB",
    "ELECTRON", "OPTIC", "TELECOM", "COMMUNICATION",
]


# ---------------------------------------------------------------------------
# Step 1 — inspection / sparsity gate
# ---------------------------------------------------------------------------
def _populated(series) -> int:
    s = series.astype(str).str.strip()
    return int(((s != "") & (~s.str.lower().isin(["nan", "null", "none"]))).sum())


def _av_str(raw):
    """Coerce an Alpha Vantage string field to a real value or None.

    AV emits the literal string "None" (and sometimes "-" or "") rather than
    JSON null, so a plain `or None` idiom lets "None" through as if it were
    data. Every AV string field must go through this."""
    if raw is None:
        return None
    s = str(raw).strip()
    if s == "" or s.lower() in ("none", "null", "n/a", "-"):
        return None
    return s


def inspect():
    print("=" * 78)
    print("STEP 1 — LISTING_STATUS inspection")
    print("=" * 78)
    frames = {}
    for label, path in (("ACTIVE", ACTIVE_CSV), ("DELISTED", DELISTED_CSV)):
        df = pd.read_csv(path)
        frames[label] = df
        print(f"\n{label}: {path}")
        print(f"  rows: {len(df)}")
        print(f"  columns: {list(df.columns)}")
        for col in ("ipoDate", "delistingDate"):
            if col in df.columns:
                n = _populated(df[col])
                print(f"  {col}: {n}/{len(df)} populated ({100*n/len(df):.1f}%)")
            else:
                print(f"  {col}: COLUMN ABSENT")

    # Sparsity gate: ipoDate on both, delistingDate on the delisted file.
    checks = {
        "ACTIVE.ipoDate": _populated(frames["ACTIVE"]["ipoDate"]) / len(frames["ACTIVE"]),
        "DELISTED.ipoDate": _populated(frames["DELISTED"]["ipoDate"]) / len(frames["DELISTED"]),
        "DELISTED.delistingDate": _populated(frames["DELISTED"]["delistingDate"]) / len(frames["DELISTED"]),
    }
    sparse = {k: v for k, v in checks.items() if v < DATE_SPARSE_THRESHOLD}
    if sparse:
        print("\n*** DATES ARE SPARSE — STOPPING. ***")
        for k, v in sparse.items():
            print(f"    {k}: only {100*v:.1f}% populated")
        print("Re-download LISTING_STATUS or adjust the plan before crawling OVERVIEW.")
        sys.exit(2)
    print("\n✓ Dates are well-populated (all key columns >= "
          f"{100*DATE_SPARSE_THRESHOLD:.0f}%). Safe to proceed.")
    return frames["ACTIVE"], frames["DELISTED"]


# ---------------------------------------------------------------------------
# Step 2 — filter
# ---------------------------------------------------------------------------
def filter_universe(active, delisted):
    af = active[(active.assetType == "Stock") & (active.exchange.isin(EXCHANGES))].copy()
    af["is_delisted"] = 0
    af["delisting_date"] = None

    df = delisted[(delisted.assetType == "Stock") & (delisted.exchange.isin(EXCHANGES))].copy()
    df["_dl"] = pd.to_datetime(df["delistingDate"], errors="coerce")
    df = df[df["_dl"] >= DELISTED_SINCE].copy()
    df["is_delisted"] = 1
    df["delisting_date"] = df["delistingDate"]

    cols = ["symbol", "name", "exchange", "ipoDate", "delisting_date", "is_delisted"]
    combined = pd.concat([af[cols], df[cols]], ignore_index=True)
    # De-dup: a symbol appearing in both files (re-listed) — keep the active row.
    combined = combined.sort_values("is_delisted").drop_duplicates("symbol", keep="first")

    # Drop non-operating securities. AV tags units, warrants and rights as
    # assetType 'Stock', so they survive the filter above — but they have no
    # financial statements of their own and nothing to value. SPAC shells are
    # excluded for the same reason: no operations to backtest.
    #
    # This removes only the derivative share class, never the underlying: e.g.
    # GCTSW / GCTS-WS (warrants) go, GCTS (common) stays.
    n_before = len(combined)
    sym = combined["symbol"].fillna("")
    nm = combined["name"].fillna("").str.upper()
    is_derivative = (
        sym.str.contains(r"-(?:U|UN|W|WS|WT|R|RT)$", regex=True, na=False)
        | nm.str.contains(r"WARRANT|RIGHTS| - UNIT|UNITS \(", regex=True, na=False)
    )
    is_spac_shell = nm.str.contains(r"ACQUISITION CORP|ACQUISITION CO\b|BLANK CHECK",
                                    regex=True, na=False)
    combined = combined[~(is_derivative | is_spac_shell)]
    log.info(f"Excluded {n_before - len(combined)} non-operating securities "
             f"({int(is_derivative.sum())} units/warrants/rights, "
             f"{int(is_spac_shell.sum())} SPAC shells).")

    log.info(f"Filtered universe: {len(combined)} tickers "
             f"({(combined.is_delisted==0).sum()} active, {(combined.is_delisted==1).sum()} delisted).")
    return combined.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Step 3 — OVERVIEW crawl with disk cache
# ---------------------------------------------------------------------------
def ensure_cache(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS overview_cache (
            ticker      TEXT PRIMARY KEY,
            raw_json    TEXT,        -- '{}' when AV returned no coverage
            fetched_at  TEXT
        )
    """)
    conn.commit()


def crawl_overviews(conn, tickers):
    ensure_cache(conn)
    cached = {r[0] for r in conn.execute("SELECT ticker FROM overview_cache")}
    todo = [t for t in tickers if t not in cached]
    log.info(f"OVERVIEW crawl: {len(tickers)} total, {len(cached)} cached, {len(todo)} to fetch "
             f"(~{len(todo)*SLEEP/60:.0f} min @ {CALLS_PER_MINUTE}/min).")
    done = 0
    for t in todo:
        _pace()
        data = _av_fetch(t, "OVERVIEW")
        # _av_fetch returns None on transient failure (rate-limit exhausted / network).
        # Do NOT cache a transient failure as permanent — skip and let a re-run retry.
        if data is None:
            reason = _dp.AV_LAST_FAILURE_REASON
            if reason in ("rate_limit", "timeout", "exception"):
                log.warning(f"  {t}: transient failure ({reason}) — not caching, will retry on re-run.")
                continue
            data = {}   # api_error / genuine empty => treat as no coverage
        # Empty dict or missing Symbol == AV has no coverage (expected for many delisted).
        payload = data if (data and data.get("Symbol")) else {}
        conn.execute(
            "INSERT OR REPLACE INTO overview_cache (ticker, raw_json, fetched_at) VALUES (?,?,?)",
            (t, json.dumps(payload), datetime.utcnow().isoformat()))
        done += 1
        # Commit often: an abrupt kill (lid close, outage) should cost seconds
        # of re-fetching, not a full 50-ticker batch.
        if done % 25 == 0:
            conn.commit()
            log.info(f"  … {done}/{len(todo)} fetched ({100*done/max(len(todo),1):.1f}%)")
    conn.commit()
    log.info(f"Crawl pass complete: {done} newly fetched.")


# ---------------------------------------------------------------------------
# Step 4/5 — build universe table + in_backtest flag
# ---------------------------------------------------------------------------
# Sectors that are never "tech-adjacent" for this engine, regardless of what
# their industry string happens to contain. Belt-and-braces behind the
# word-boundary match below.
NON_TECH_SECTORS = {"HEALTHCARE"}


def _is_tech(sector, industry):
    sec = (sector or "").strip().upper()
    if sec == "TECHNOLOGY":
        return True
    if sec in NON_TECH_SECTORS:
        return False
    ind = (industry or "").upper()
    # LEADING word boundary only — deliberately not a full \b...\b match.
    #   plain substring : "TECHNOLOGY" in "BIOTECHNOLOGY"  -> wrong (swept 179
    #                     biotechs into the universe, 25% of it)
    #   full \b...\b    : misses "SEMICONDUCTORS" (AV's actual plural industry
    #                     string), which would drop every chip company
    #   leading \b only : "BIOTECHNOLOGY" no match, "SEMICONDUCTORS" matches
    return any(re.search(rf"\b{re.escape(k)}", ind) for k in TECH_ADJACENT_INDUSTRY)


def build_universe(conn, filtered):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS universe (
            ticker          TEXT PRIMARY KEY,
            name            TEXT,
            exchange        TEXT,
            sector          TEXT,
            industry        TEXT,
            market_cap      REAL,
            ipo_date        TEXT,
            delisting_date  TEXT,
            is_delisted     INTEGER,
            sector_unknown  INTEGER,
            in_backtest     INTEGER DEFAULT 0
        )
    """)
    conn.commit()

    cache = {r[0]: json.loads(r[1] or "{}")
             for r in conn.execute("SELECT ticker, raw_json FROM overview_cache")}
    already = {r[0] for r in conn.execute("SELECT ticker FROM classifications")}

    rows = []
    for _, r in filtered.iterrows():
        t = r["symbol"]
        ov = cache.get(t, {})
        # AV returns the LITERAL STRING "None" for missing fields, not JSON null.
        # Treating that as a real sector left ~6% of tickers with sector="None":
        # not flagged sector_unknown, not tech, and therefore silently dropped
        # from the review lists entirely. Normalise it to a true None.
        sector = _av_str(ov.get("Sector"))
        industry = _av_str(ov.get("Industry"))
        mc_raw = ov.get("MarketCapitalization")
        try:
            market_cap = float(mc_raw) if mc_raw not in (None, "None", "", "0") else None
        except (ValueError, TypeError):
            market_cap = None
        # Unknown = OVERVIEW gave us no usable sector, whether because the call
        # came back empty (common for delisted names) or because AV returned
        # "None". Either way the ticker is kept and surfaced for manual review.
        sector_unknown = 1 if sector is None else 0
        tech = _is_tech(sector, industry)
        in_bt = 1 if ((tech and market_cap is not None and market_cap > TECH_MC_FLOOR)
                      or t in already) else 0
        rows.append((t, r["name"], r["exchange"], sector, industry, market_cap,
                     r["ipoDate"], r["delisting_date"], int(r["is_delisted"]),
                     sector_unknown, in_bt))

    conn.execute("DELETE FROM universe")
    conn.executemany("""
        INSERT OR REPLACE INTO universe
        (ticker, name, exchange, sector, industry, market_cap, ipo_date,
         delisting_date, is_delisted, sector_unknown, in_backtest)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, rows)
    conn.commit()

    # Any classified ticker not present in the CSV filter set still belongs in
    # the backtest — insert a minimal row so the flag is never lost.
    present = {r[0] for r in conn.execute("SELECT ticker FROM universe")}
    for t in (already - present):
        conn.execute("""INSERT OR REPLACE INTO universe
            (ticker,name,exchange,sector,industry,market_cap,ipo_date,delisting_date,
             is_delisted,sector_unknown,in_backtest)
            VALUES (?,?,?,?,?,?,?,?,?,?,1)""",
            (t, None, None, None, None, None, None, None, 0, 1))
    conn.commit()

    n = conn.execute("SELECT COUNT(*) FROM universe").fetchone()[0]
    nbt = conn.execute("SELECT COUNT(*) FROM universe WHERE in_backtest=1").fetchone()[0]
    nun = conn.execute("SELECT COUNT(*) FROM universe WHERE sector_unknown=1").fetchone()[0]
    log.info(f"universe: {n} rows | in_backtest={nbt} | sector_unknown={nun}")


# ---------------------------------------------------------------------------
# Step 6 — review lists
# ---------------------------------------------------------------------------
def review_lists(conn):
    print("\n" + "=" * 78)
    print("REVIEW LIST A — DELISTED tickers marked in_backtest = 1")
    print("=" * 78)
    a = conn.execute("""SELECT ticker, name, sector, industry, market_cap, delisting_date
                        FROM universe WHERE is_delisted=1 AND in_backtest=1
                        ORDER BY market_cap DESC NULLS LAST, ticker""").fetchall()
    print(f"({len(a)} tickers)")
    for t, nm, sec, ind, mc, dl in a:
        mcs = f"${mc/1e9:.1f}B" if mc else "n/a"
        print(f"  {t:6s} {mcs:>8s}  {sec or '?':22s} {(nm or '')[:40]:40s} delisted {dl}")

    print("\n" + "=" * 78)
    print("REVIEW LIST B — DELISTED + sector_unknown, NAME looks tech (manual confirm)")
    print("=" * 78)
    b = conn.execute("""SELECT ticker, name, delisting_date FROM universe
                        WHERE is_delisted=1 AND sector_unknown=1 ORDER BY ticker""").fetchall()
    hits = [(t, nm, dl) for (t, nm, dl) in b
            if nm and any(h in nm.upper() for h in TECH_NAME_HINTS)]
    print(f"({len(hits)} of {len(b)} sector_unknown delisted names look tech by name)")
    for t, nm, dl in hits:
        print(f"  {t:6s} {nm[:52]:52s} delisted {dl}")


def main():
    args = sys.argv[1:]
    active, delisted = inspect()
    if "--inspect" in args:
        return
    conn = sqlite3.connect(DB_NAME)
    filtered = filter_universe(active, delisted)
    if "--build" not in args:
        crawl_overviews(conn, list(filtered["symbol"]))
    build_universe(conn, filtered)
    review_lists(conn)
    conn.close()


if __name__ == "__main__":
    main()
