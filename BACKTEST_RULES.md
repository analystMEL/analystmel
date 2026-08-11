# CVE Backtest Rules

Authoritative rules the backtester must obey. Add rules as they are decided;
each one states the rule, why it exists, and how to verify it holds.

_Location: `/Users/ommelwani/gemini/antigravity/scratch/BACKTEST_RULES.md`_

---

## Rule 1 — Hard-stop every ticker at its true last trading day

**Status: ✅ IMPLEMENTED (2026-08-11).** Enforced at two layers — see
*Implementation* and *Verification results* below.

**Rule.** A ticker's price series must terminate at its true last trading day.
After that date the backtester may not hold, price, buy, or mark the position.
On that date any open position is force-exited at that day's close, and the
proceeds return to cash. No position may survive its delisting date.

**Why.** Alpha Vantage does not stop serving a delisted ticker — it *freezes*
it. After the final real trade the last close is carried forward, unchanged,
with zero volume, every day, indefinitely:

```
SPLK   2024-03-14  close=156.51  volume=4,405,791   <- real trading
SPLK   2024-03-15  close=156.90  volume=18,211,874  <- last real day
SPLK   2024-03-18  close=156.90  volume=0           <- Cisco deal closes
SPLK   2026-08-10  close=156.90  volume=0           <- still "trading", 2.4y later
```

One distinct close across 2.4 years. A backtester reading this sees a position
that never moves, never loses, and never exits — it would carry Splunk at
$156.90 through 2026. That manufactures a flat, riskless holding out of a
company that ceased to exist, biasing returns upward in exactly the direction
that defeats the purpose of including delisted names at all.

**Source of truth.** `universe.delisting_date`, repaired by
`fix_delisting_dates.py`. That column holds the **last tradable day** — not
AV's CSV convention (the following business day) — precisely so it can be used
as a direct trading cutoff with no off-by-one adjustment.

**Implementation (two layers, both live).**

1. **At ingest** — `backfill_fundamentals.py::_backfill_price_history_av`.
   Delisted tickers have no yfinance presence (Yahoo purges them), so their
   prices come from AV `TIME_SERIES_DAILY_ADJUSTED`. Every bar dated after
   `universe.delisting_date` is dropped *before* it reaches `price_data`, so
   the frozen tail never enters the database at all. Adjusted close is stored,
   matching the yfinance `auto_adjust=True` rows already present.
2. **At the price-read boundary** — `backtester.py::get_price_on_date` returns
   `None` for any `date > delisting_date` (dates cached once per process from
   `universe`). This is the enforcement point named below, and it is
   defence-in-depth: a stale `price_data` row loaded before layer 1 existed
   still cannot be read.

Remaining notes:
- Force-exit before the cutoff, not after: the position must be closed *on* the
  last trading day, while a real price still exists. `get_price_on_date`
  returning `None` is the signal for the portfolio loop to close the position —
  wiring that exit is the Stage 4 engine's job (step 6).
- `delisting_date IS NULL` on a delisted row means undatable — see Open Item A;
  those tickers are `in_backtest = 0` and must not enter the run.
- Active tickers have `delisting_date IS NULL` and `is_delisted = 0`; the rule
  is a no-op for them.

**Verification results (2026-08-11).**
1. **DB-wide assertion:** `SELECT COUNT(*) FROM price_data p JOIN universe u
   ON p.ticker = u.ticker AND u.is_delisted = 1 AND p.date > u.delisting_date`
   → **0 rows**.
2. **SPLK (last trading day 2024-03-15):** 2,996 bars stored, **153 frozen
   post-delisting bars dropped** at ingest; series ends exactly 2024-03-15.
   Read guard returns 156.51 on 03-14, 156.90 on 03-15, `None` on 03-18 and on
   2026-08-01. Active control (MSFT) unaffected.
3. **The frozen tail is confirmed, not assumed.** Probing AV directly: of
   SPLK's 153 post-cutoff bars, 124 have literally zero volume and the other 29
   carry 5–5,546 shares (≤0.36% of its 1.55M median) at an unchanged $156.90
   close. EVBG: 128 post-cutoff bars, 125 zero-volume, 3 residual at ≤0.12% of
   median, close pinned at $35.00. MIXT: 590 post-cutoff bars, **all** zero
   volume. The cutoff dates land on genuine final trading days (SPLK's last
   real bar carries 18.2M shares), so truncation discards no real trading.
4. ~~Outstanding for step 6~~ **CLOSED 2026-08-11 (step 6).** The naive version
   of this check is ambiguous: the Stage 4 portfolio's results are *identical*
   with delisted names forced to −50% and −100%, but only because the strategy
   never bought one (12 delisted tickers, 292 of 17,366 panel rows, never top-5
   on the selection ranking). Identical results there prove nothing either way.

   `portfolio_sim.py` therefore runs the proof with the candidate pool
   **restricted to delisted names**, which forces the path to execute:
   **8 forced exits fire** — LN (2020-12-24), MIXT (2024-03-25), WKME
   (2024-09-11), PET (2025-07-29, exit at $0.05), ETWO (2025-08-01) and three
   others — each at the last real close on its true final trading day. The
   terminal-loss override moves the result decisively: **£27,967 at the last
   real close → £9,097 at −50% → £1,893 at −100%.** The hard-stop reaches the
   portfolio loop.

   **Consequence for the writeup:** the D3 survivorship bound is *vacuous* for
   the headline run and must not be quoted as evidence that survivorship is not
   driving the result. It is unbounded by that test.

---

## Open items — pending your instruction (not yet rules)

**A. Ticker-symbol reuse.** 32 delisted symbols resolve to a *different, live*
company because the ticker was reassigned (`SOS` = Storage Computer Corp →
SOS Ltd; also `BMM`, `PALX`, `RAC`, `SSM`), plus stray ETFs and a `CTEST-A`
test symbol. They are currently `delisting_date = NULL`, `in_backtest = 0`.
Splicing a reused symbol would join two different companies into one price
history. Any rule admitting delisted tickers must key on more than the symbol.

**B. Survivorship-bias scope.** Which delisted tickers actually enter the run
(Review Lists A and B) is still being decided.
