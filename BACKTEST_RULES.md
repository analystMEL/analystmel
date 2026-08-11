# CVE Backtest Rules

Authoritative rules the backtester must obey. Add rules as they are decided;
each one states the rule, why it exists, and how to verify it holds.

_Location: `/Users/ommelwani/gemini/antigravity/scratch/BACKTEST_RULES.md`_

---

## Rule 1 — Hard-stop every ticker at its true last trading day

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

**Implementation notes.**
- Filter at the price-read boundary, e.g. in `get_price_on_date`: return no
  price for `date > delisting_date`, rather than patching each call site.
- Force-exit before that, not after: the position must be closed *on* the last
  trading day, while a real price still exists.
- `delisting_date IS NULL` on a delisted row means undatable — see Open Item A;
  those tickers are `in_backtest = 0` and must not enter the run.
- Active tickers have `delisting_date IS NULL` and `is_delisted = 0`; the rule
  is a no-op for them.

**Verification.**
1. Assert no trade or mark exists for any ticker on a date after its
   `delisting_date`.
2. Take a known case (SPLK, last trading day 2024-03-15): confirm the position
   is closed on that date and absent from every later portfolio snapshot.
3. Confirm total return changes when delisted names are included — if adding
   them leaves results identical, the hard-stop is not wired in.

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
