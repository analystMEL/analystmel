# Pre-Backtest Plan — Finish Classification + Point-in-Time Fundamentals Backfill

_Location: `/Users/ommelwani/.gemini/antigravity/scratch/PRE_BACKTEST_PLAN.md`_
_Status: ✅ COMPLETE (2026-08-08)._

## Completion summary
- **Backfill done:** all 222 universe tickers have INCOME/BALANCE/CASH_FLOW/EARNINGS with full history (up to 81 quarters). 0 tickers missing any statement type. Filing dates (`reported_date`) present on 88% of 2021+ quarters (backtest window); older quarters fall back to the 45-day lag via COALESCE. Price history deepened to 1962→2026.
- **Point-in-time fixes verified:** historical shares from `commonStockSharesOutstanding` (MSFT 2016 = 7.93B vs 7.43B today), D&A from `depreciationDepletionAndAmortization` (was silently 0), reported_date filtering — MSFT Sept-2016 valuation now correct at ~$406B mcap.
- **Offline proof:** `backtester.py` ran end-to-end with `yfinance` + `requests` hard-blocked → completed with zero network calls. Result (2021-08→2026-08): Valoura **+234.96%** vs QQQ **+102.05%** = **+132.9% alpha**. QQQ benchmark now also read from local `price_data`.
- **Classification:** MCHP → semi_hardware-3. ASML excluded (EUR). SNPS skipped per user. Universe = 222.
- **Note:** the backtest window is hardcoded to the last 5 years (`backtester.py:206`) even though data now reaches 2006 — widen that line to backtest deeper.

---

## Pre-execution verification result — "does AV have everything the 20-cell matrix needs?"

**Yes.** Every field the matrix consumes is present in AV, recent and historical:
- INCOME_STATEMENT: totalRevenue, grossProfit, operatingIncome, ebit, netIncome, researchAndDevelopment ✓
- CASH_FLOW: operatingCashflow, capitalExpenditures, stockBasedCompensation, **depreciationDepletionAndAmortization** ✓
- BALANCE_SHEET: cash, debt, equity, inventory, totalAssets, PPE (historical), totalCurrentLiabilities, **commonStockSharesOutstanding** ✓
- EARNINGS: reportedDate, reportedEPS, estimatedEPS, surprise ✓
- Depth: MSFT/NVDA 81 quarters (2006→), CRWD 34 (full post-IPO). Sufficient prior data.

**But verification found two field-correctness bugs that would corrupt the matrix if ignored — now folded into execution:**
1. **D&A field name wrong.** Code read `depreciationAmortization`/`depreciation`; AV's field is `depreciationDepletionAndAmortization`. Left unfixed, D&A=0 → EBITDA=operating income → every EV/EBITDA cell understated. Fixed in `data_pipeline1.py:783` and `backtester.py`.
2. **Backtester used TODAY's share count for all history** (its own code comment admits it) → market cap / EV / P/E / FCF-yield wrong at every historical date. AV's `commonStockSharesOutstanding` is point-in-time and already captured in the backfill's raw balance-sheet JSON, so the fix is to read it there. Wired into `backtester.py`.

**A third gap found during execution — price history depth.** `price_data` only held 5 years (the live pipeline's `YEARS_OF_DATA`), but fundamentals now reach 2006. Without matching prices, market cap/EV came out **0** for any date before ~2021 (verified: MSFT 2016 mcap was 0). Fixed by having the backfill also pull **max** daily price history via yfinance (no AV credits; `price_data` is local-only — `build_repo_db.py` strips it). After the fix, MSFT 2016 prices correctly to ~$406B market cap.

**Classification outcome:** MCHP → `semi_hardware-3` (HIGH). ASML excluded (EUR, as predicted). **SNPS skipped at user's instruction** (AV served it with intermittent "Invalid API call" flakiness; user chose to drop it rather than chase it). Universe: **222** (221 + MCHP).

---

## Context

Two requests, both preparation for running the historical backtest:

1. **Classify the remaining tickers** from your supplied 194-name list (deduplicated), ignoring anything non-technology and anything already classified.
2. **Build a historical backfill** that pulls full quarterly history (INCOME_STATEMENT, BALANCE_SHEET, CASH_FLOW, EARNINGS) for every universe ticker and stores each quarter with its fiscal period **and its reported/filing date**, so the backtest runs entirely off the local DB with no further API calls.

### What I verified (read-only investigation)

- **The 194-name list yields almost nothing new under the strict-technology rule.** 56 are already classified. Of the 138 new names, an AV `OVERVIEW` sweep confirms only **`SNPS`** and **`MCHP`** are `Sector=TECHNOLOGY` in USD. **`ASML`** is Technology but files in EUR and will be caught by the existing non-USD guard (`data_pipeline1.py:1890`), same as last batch. Everything else is banks, energy, healthcare, staples, industrials, Chinese/e-commerce consumer-cyclical, comm-services, fintech, or crypto miners — an S&P sector spread, not tech.
- **The backfill script does not exist.** `backfill_fh_history.py` is unrelated — it recomputes FH *stages* from already-cached fundamentals with zero API calls. Nothing pulls raw history or stores a filing date.
- **`fundamentals` has no filing-date column** — only `fiscal_date` (fiscal period end). AV's three statement endpoints don't return a filing date at all; only the **EARNINGS** endpoint does (`reportedDate`). That is exactly why EARNINGS is on your list.
- **AV has deep history.** MSFT returns 81 quarters of statements (2006→2026) and 122 quarters of EARNINGS. The current `fetch_and_store_fundamentals` throws most of it away via a `YEARS_OF_DATA * 4 = 20`-quarter cap (`data_pipeline1.py:553`). The backfill must not apply that cap. Statement `fiscalDateEnding` joins cleanly to EARNINGS `reportedDate`.
- **The backtester currently fakes the filing date.** `get_point_in_time_fundamentals` (`backtester.py:31`) filters on `fiscal_date <= target_date − 45 days` (a flat `REPORTING_LAG_DAYS` assumption). This is the look-ahead-bias hazard the real `reported_date` removes. You approved wiring the true date in.

---

## Answer to your direct question: does the backfill already exist?

**No.** There is no script that pulls raw AV history or stores a filing date. The closest existing file, `backfill_fh_history.py`, does something different (recomputes financial-health stages from data already cached, no API calls). This plan builds the real thing as a new script, `backfill_fundamentals.py`.

---

## Implementation

### Part A — Classify the 3 new technology names
Reuse the existing runner (staleness gate + non-USD guard already in place):
```bash
/usr/bin/python3 run_universe.py SNPS MCHP ASML
```
Expect SNPS + MCHP classified (both semiconductor-design software; audit per your standing override policy), ASML auto-excluded (EUR). Audit any LOW-confidence result and override in `MANUAL_BM_OVERRIDES` (`data_pipeline1.py:51`) only where the category contradicts the business, then re-run those tickers (free, cached). Every other name on the list is intentionally skipped; skip reasons (already-classified / non-technology) get appended to `universe_skipped_2026-08.tsv`.

### Part B — Schema: add the filing-date column
Additive and backward-compatible — no existing code selects it, so nothing breaks:
```sql
ALTER TABLE fundamentals ADD COLUMN reported_date TEXT;
```
Added inside `setup_database` (`data_pipeline1.py:202`) behind a `PRAGMA table_info` check so it is idempotent and self-applies to the live DB. (Deliberate, you-requested change; the earlier "don't touch the schema" rule was scoped to the Supabase task and is superseded here.)

### Part C — New script `backfill_fundamentals.py`
Durable, resumable, Pro-tier — mirrors `run_universe.py` (zshrc env loader, `_av_fetch` with its backoff, `AV_SLEEP`, honest failure reasons).

Per ticker (default = **all rows in `classifications`**, ~223; overridable by args / `--file`):
1. **EARNINGS first** → build `{fiscalDateEnding → reportedDate}` and store each earnings quarter as its own `statement_type='EARNINGS'` row (raw JSON incl. reportedEPS / estimatedEPS / surprise; `reported_date` = reportedDate).
2. **INCOME_STATEMENT, BALANCE_SHEET, CASH_FLOW** → fetch **full** `quarterlyReports` (no 20-quarter cap), upsert each quarter with `reported_date` from the earnings map (NULL if a very old quarter has no earnings coverage).
3. `INSERT OR REPLACE` on the existing PK `(ticker, statement_type, fiscal_date)` so re-runs both extend history and **enrich reported_date on the existing 20-quarter rows**. Skip a `(ticker, statement_type)` already fully populated *with* reported_date, so re-runs are cheap.

Cost ≈ 223 × 4 ≈ ~900 AV calls, ~20 min at Pro latency (unlimited/day on Pro). Runs in background.

Note: it will **not** reuse `fetch_and_store_fundamentals` directly — its 20-quarter cap and missing reported_date are the two things being fixed. Shared fetch goes through `_av_fetch`.

### Part D — Wire the real filing date into the backtester
In `get_point_in_time_fundamentals` (`backtester.py:31`), replace the fiscal-date-minus-45-days approximation with the true filing date, falling back to the old lag only when `reported_date` is NULL:
```sql
WHERE ticker=? AND statement_type=?
  AND COALESCE(reported_date, date(fiscal_date, '+45 days')) <= ?
ORDER BY COALESCE(reported_date, date(fiscal_date,'+45 days')) DESC LIMIT 8
```
Backward-compatible: pre-backfill rows (reported_date NULL) behave exactly as today. This is what makes the stored date actually eliminate look-ahead bias.

### Not changing
Classification logic, valuation engine, interpretation prompt, the app UI, and the slim repo-DB build (`build_repo_db.py` strips `fundamentals`, so the deep history stays local-only and never bloats the repo — correct, since the app doesn't read it and the backtest does).

---

## Verification

1. `PRAGMA table_info(fundamentals)` shows the `reported_date` column.
2. After backfill: `SELECT statement_type, COUNT(*) FROM fundamentals GROUP BY 1` shows four types incl. EARNINGS, and MSFT INCOME_STATEMENT count ≫ 20 (expect ~81).
3. `SELECT fiscal_date, reported_date FROM fundamentals WHERE ticker='MSFT' AND statement_type='INCOME_STATEMENT' ORDER BY fiscal_date DESC LIMIT 4` — every recent row has a real `reported_date` a few weeks after `fiscal_date`.
4. Idempotency: re-run for one ticker → 0 new API calls, counts unchanged.
5. Look-ahead check: for a known quarter, confirm the backtester excludes a statement whose `reported_date` is after the as-of date but whose `fiscal_date−45d` would have (mis)included it.
6. `SELECT COUNT(*) FROM classifications` = 223 (221 + SNPS + MCHP); ASML in the non-USD exclusion log.
7. Run `backtester.py` end-to-end; confirm it completes with **zero** AV/yfinance calls (all reads local).

---

## Files touched

| File | Change |
|---|---|
| `data_pipeline1.py` | `setup_database`: idempotent `ALTER TABLE … ADD COLUMN reported_date`; possible `MANUAL_BM_OVERRIDES` additions from the SNPS/MCHP audit |
| `backfill_fundamentals.py` | **new** — full-history + filing-date backfill (Part C) |
| `backtester.py` | `get_point_in_time_fundamentals`: use real `reported_date` (Part D) |
| `universe_skipped_2026-08.tsv` | append this list's skip reasons |
| `run_universe.py` | (no change) reused as-is for Part A |

---

# Universe Build (2026-08-11) — `universe_builder.py`

**Status: ✅ complete.** `universe` table built in `valoura_backtest.db`.

| | |
|---|--:|
| Universe rows | 11,009 (6,517 active / 4,492 delisted) |
| `in_backtest = 1` | 495 |
| `sector_unknown = 1` | 5,244 |
| Review List A (delisted & in_backtest) | 9 |
| Review List B (delisted, unknown sector, tech-sounding name) | 362 → `review_list_B.tsv` |

### Step 1 gate (passed)
active 14,279 rows · delisted 9,410 rows · `ipoDate` 100% populated in both ·
`delistingDate` 100% in delisted (0% in active, as expected). Not sparse → proceeded.

### Filters applied
- `assetType == Stock`, exchange ∈ {NASDAQ, NYSE}, delisted cut at ≥ 2012-01-01.
- **3,680 non-operating securities excluded** (2,932 units/warrants/rights + 1,860 SPAC
  shells). Removes only the derivative class, never the underlying: `GCTSW`/`GCTS-WS`
  dropped, `GCTS` kept.

### Bugs found and fixed
1. **AV returns the literal string `"None"`**, not JSON null. The `or None` idiom let it
   through as a real sector, so those tickers were neither `sector_unknown` nor tech —
   they vanished from the review lists. ~6% of rows. Fixed with `_av_str()`.
2. **`"BIOTECHNOLOGY"` contains `"TECHNOLOGY"`** — substring matching swept **179 biotechs**
   into the backtest universe (25% of it). Fixed with a *leading* word-boundary match.
   Note: a full `\b…\b` match is WRONG here — it fails on `SEMICONDUCTORS` (AV's plural
   industry string) and would drop every chip company. `in_backtest` 704 → 495.

### AV data defect: delisting dates  → `fix_delisting_dates.py`
597 of 9,410 delisted rows (228 in-universe) were stamped with the CSV's own snapshot
date (2026-08-07) instead of the real one — Splunk and Seagen among them.

- **yfinance cannot fix this**: it returns ZERO bars for delisted symbols (verified on
  SPLK, SGEN, ACIA, CLDR, ARUN). Yahoo purges them.
- **AV serves them but FREEZES them**: after the last real trade the close is carried
  forward with zero volume forever. SPLK = one distinct close for 2.4 years.
- Detector = last bar with volume ≥ 1% of the ticker's median volume. Validated within
  one trading day on SPLK / SGEN / ACIA / CLDR / ARUN / EVBG. **227 corrected.**
- Stored value is the **last tradable day** (what a backtest needs); AV's CSV convention
  is the next business day, hence a consistent 1-day offset.

### ⚠ Open risks for the backtest (NOT yet addressed)
1. **Frozen price tails fabricate returns.** Any delisted ticker priced from AV shows a
   flat line that never ends — no delisting, no loss, no exit. A backtest holding SPLK
   would carry it at $156.90 through 2026. This flatters results in exactly the direction
   that defeats adding delisted names. **The backtester must hard-stop each ticker's price
   series at its true last trading day and force an exit.**
2. **Ticker-symbol reuse.** 32 rows resolved as "still trading" because the symbol was
   reassigned to a different live company (`SOS` = Storage Computer Corp → SOS Ltd;
   `BMM`, `PALX`, `RAC`, `SSM` likewise), plus stray ETFs and a `CTEST-A` test symbol.
   Their dates were reverted to NULL and `in_backtest = 0`. **Splicing a reused symbol's
   history would join two different companies' price series.** Any future delisted-ticker
   work must key on more than the symbol.
