import sqlite3
import pandas as pd
import yfinance as yf
import requests
import time
import os
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    logging.warning("google-generativeai not installed — LLM tiebreaker disabled.")

# ==========================================
# CONFIGURATION
# ==========================================
DB_NAME = "valoura_backtest.db"
ALPHA_VANTAGE_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY", "demo")
GOOGLE_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY", "")
GEMINI_MODEL_NAME = "gemini-2.5-flash-lite"  # no-thinking variant; gemini-2.5-flash burned budget on internal thinking
LLM_MAX_TOKENS = 400  # raised from 150; the previous limit truncated rationales to ~4 chars
MANUAL_METRICS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "manual_metrics.json")
MAINTENANCE_CAPEX_RATIO = 0.50  # Estimate maintenance capex as 50% of total capex
CYCLE_ADJ_YEARS = 5  # Years for cycle-adjusted P/E (Shiller-style)
EST_TAX_RATE = 0.21  # US statutory rate, used only for the net_income_quality_flag check

# Growth-percentage metrics blow up when the prior-period base is tiny
# (JOBY: revenue went from $111k to $77M → rev_growth_yoy = 69,874%).
# Null out rev_growth_yoy and operating_leverage when the prior LTM denominator
# is below this threshold — the percentage is technically correct but useless
# as a comparable and poisons downstream metrics like rule_of_40 and FH scoring.
MIN_PRIOR_BASE_REVENUE = 10_000_000   # $10M prior-LTM revenue
MIN_PRIOR_BASE_OP_INCOME = 5_000_000  # $5M |prior-LTM op income|

# Post-classification SaaS gate: a ticker classified saas must clear one of:
#   1. deferred_revenue_ratio >= SAAS_MIN_DEFERRED  (billed in advance → real subscription)
#   2. has ARR seeded in manual_metrics.json        (human-tagged as recurring)
#   3. listed in MANUAL_BM_OVERRIDES as "saas"     (human-confirmed)
# Otherwise, demote to next best non-saas category. See _apply_saas_gate.
SAAS_MIN_DEFERRED = 0.05  # 5% of LTM revenue; empirically separates RDDT (1.1%) from CRM/NET/ADBE/SNOW (33-72%)

# Manual BM overrides — seeded category short-circuits the validator + LLM path.
# Overrides should NEVER lose to the LLM (see classify_business_model).
# Add an entry here when domain knowledge says the validator can't get it right
# (e.g. MSFT: AV doesn't expose PPE, so hyperscale validator can never pass).
MANUAL_BM_OVERRIDES = {
    "MSFT":  "hyperscale",
    "AMZN":  "hyperscale",
    "GOOGL": "hyperscale",
    "ORCL":  "hyperscale",
    "IBM":   "hyperscale",
    "SNOW":  "saas",
    # Quantum / photonic R&D plays — zero-inventory + zero-PPE looks SaaS-like
    # to validators, but business model is fundamentally deep_tech (no recurring
    # subscription revenue, value lies in IP / scientific moat).
    "QBTS":  "deep_tech",
    # ── LOW-confidence LLM-tiebreaker overrides (manually reviewed) ─────────
    # Policy: always override when LLM confidence is LOW and the resulting
    # category disagrees with the company's primary business model.
    "INTC":  "semi_hardware",     # was deep_tech-3 — Intel is the canonical semi co.
    "ADI":   "semi_hardware",     # was deep_tech-3 — Analog Devices, major analog semi co.
    "BKSY":  "deep_tech",         # was consumer_internet-1 — satellite imaging
    "DASH":  "consumer_internet", # was deep_tech-3 — gig-economy marketplace
    "TTWO":  "consumer_internet", # was deep_tech-2 — Take-Two video-game publisher (like EA)
    "SNAP":  "consumer_internet", # was deep_tech-3 — social ad platform (AR is a feature)
    "MDB":   "saas",              # was deep_tech-2 — MongoDB Atlas is subscription DB
    "DOCN":  "saas",              # was consumer_internet-3 — DigitalOcean dev-infra SaaS
    # ── "Neocloud" AI hyperscalers — own physical GPU data centres, usage-based
    # compute revenue, extreme CapEx. Validators misread their SaaS-like margins
    # (CRWV has only 10 quarters of post-IPO data) but the business model is
    # textbook hyperscale infrastructure.
    "CRWV":  "hyperscale",        # was saas-1 — CoreWeave GPU cloud
    "NBIS":  "hyperscale",        # was deep_tech-1 — Nebius AI cloud
    "NTAP":  "semi_hardware",     # was deep_tech-3 — NetApp enterprise storage
                                  # hardware (same family as WDC/STX)
    "U":     "saas",              # was deep_tech-2 — Unity game-engine software,
                                  # subscription + ad monetisation, not frontier R&D
    # ── 2026-08 batch review (140-ticker technology expansion) ─────────────
    # Same failure modes as above: the LLM tiebreaker dumped (a) fabless/analog
    # semiconductor makers into deep_tech, and (b) high-margin vertical-SaaS
    # names into consumer_internet. Several had the correct category already in
    # their contender set. Overridden per the LOW-confidence policy.
    # Semiconductors / networking hardware wrongly tagged deep_tech:
    "CSCO":  "semi_hardware",     # Cisco — networking hardware (like NTAP/STX)
    "SLAB":  "semi_hardware",     # Silicon Labs — fabless semis (contender had it)
    "SYNA":  "semi_hardware",     # Synaptics — fabless semis (contender had it)
    "VYX":   "semi_hardware",     # NCR Voyix — commerce hardware + software (GM 25%)
    # Enterprise / vertical SaaS wrongly tagged deep_tech:
    "MANH":  "saas",              # Manhattan Associates — supply-chain SaaS
    "TYL":   "saas",              # Tyler Technologies — government SaaS
    "FIVN":  "saas",              # Five9 — cloud contact-center SaaS
    "ALKT":  "saas",              # Alkami — digital-banking SaaS
    "MQ":    "saas",              # Marqeta — card-issuing API platform (B2B)
    "DOX":   "saas",              # Amdocs — telecom BSS/OSS software
    "RBRK":  "saas",              # Rubrik — data-security SaaS (contender had saas)
    # Subscription software wrongly tagged consumer_internet (B2C):
    "NICE":  "saas",              # NICE — enterprise CX software (contender had saas)
    "PAYX":  "saas",              # Paychex — payroll software subscription
    "RAMP":  "saas",              # LiveRamp — data-connectivity SaaS
    "ROP":   "saas",              # Roper — vertical-software conglomerate (contender)
    "RPD":   "saas",              # Rapid7 — cybersecurity SaaS (contender had saas)
    "SPSC":  "saas",              # SPS Commerce — supply-chain SaaS (contender had saas)
    "IT":    "saas",              # Gartner — subscription research (contender had saas)
    # Consumer fintech platform wrongly tagged deep_tech:
    "XYZ":   "consumer_internet", # Block — Square + Cash App consumer/merchant fintech
}

# Populated at runtime: tickers whose AV fundamentals come back in a non-USD
# reporting currency (TSM=TWD, ASML=EUR, etc). They are excluded from
# classification + valuation because mixing those numbers with USD market cap
# produces nonsense ratios. TODO: build a proper FX normalisation layer
# (period-average rate for flow items, period-end rate for BS items) — not yet.
EXCLUDED_TICKERS_NON_USD: set[str] = set()

# Stage changes detected during this pipeline run (ticker / previous / new / cell).
# Written to Supabase stage_change_log as they happen; kept here for reporting.
STAGE_CHANGES_THIS_RUN: list = []


def _get_supabase_client_module():
    """Import supabase_client (lives in the UI folder) with a path fallback.
    Returns the module or None — never raises."""
    try:
        import supabase_client
        return supabase_client
    except ImportError:
        pass
    try:
        import sys as _sys, os as _os
        _ui_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "Valuora_Previous_UI")
        if _ui_dir not in _sys.path:
            _sys.path.insert(0, _ui_dir)
        import supabase_client
        return supabase_client
    except Exception:
        return None

TICKERS = [
    # Pure SaaS
    "PANW", "CRM", "WDAY", "ADBE", "TEAM", "MDB", "NET", "DOCN", "ZS", "SNOW",  # CFLT removed — taken private 2026
    # Semiconductor / Hardware
    "AMD", "NVDA", "TSM", "ASML", "MRVL", "AVGO", "QCOM", "INTC", "KLAC",
    # Hyperscale
    "MSFT", "GOOGL", "AMZN", "RKLB", "IBM",  # ORCL → moved below; RKLB pulled up (today's batch)
    # Consumer Internet
    "META", "AAPL", "NFLX", "SPOT", "PINS", "SNAP", "UBER", "ABNB", "DASH", "ROKU", "MTCH", "RDDT",
    # Enterprise Infra / Networking
    "DELL", "ANET", "SMCI",
    # Deep Tech / Space
    "PLTR", "PATH", "IONQ", "JOBY", "RIVN", "QBTS", "RGTI", "ASTS", "SOUN", "ORCL",

]

YEARS_OF_DATA = 5

# ---------------------------------------------------------------------------
# Alpha Vantage rate-limit configuration — PREMIUM (Pro) tier
#
#   Pro tier ($50/month): 75 requests/minute, NO daily cap.
#
# The engine is now Pro-only. There is no daily-quota handling: the former
# 25/day free-tier logic (hard-stop after N consecutive zero-record tickers
# assumed to mean "quota exhausted") has been removed. The only rate limit
# that exists on Pro is the per-minute burst ceiling, which is absorbed by
# the exponential-backoff retry in _av_fetch below.
#
# AV_CALLS_PER_MINUTE can still be overridden via env var if AV ever changes
# the plan ceiling, but it defaults to the Pro value — no export required.
# ---------------------------------------------------------------------------
AV_CALLS_PER_MINUTE: int = int(os.getenv("AV_CALLS_PER_MINUTE", "75"))
AV_SLEEP: float = 60.0 / AV_CALLS_PER_MINUTE           # 0.8s at 75/min
AV_RATE_LIMIT_RETRIES: int = 3                         # max retries on per-minute throttle
AV_RATE_LIMIT_BACKOFF: tuple = (15, 30, 60)            # seconds to wait before each retry

# Why the most recent AV fetch returned nothing. Lets callers distinguish a
# genuine throttle from a delisted/unknown symbol instead of guessing.
# One of: None | "rate_limit" | "api_error" | "timeout" | "exception" | "empty"
AV_LAST_FAILURE_REASON = None

# ==========================================
# LOGGING
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("pipeline.log"),
    ],
)
log = logging.getLogger(__name__)


# ==========================================
# 1. DATABASE SETUP
# ==========================================
def setup_database(conn: sqlite3.Connection) -> None:
    """
    Creates all tables if they don't exist.
    Tables:
      price_data       — daily OHLCV from yfinance
      fundamentals     — raw quarterly JSON from Alpha Vantage (3 statement types)
      computed_metrics — annualised LTM metrics derived from fundamentals
      classifications  — financial health stage + business model category per ticker/date
    """
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS price_data (
            ticker      TEXT,
            date        TEXT,
            open        REAL,
            high        REAL,
            low         REAL,
            close       REAL,
            volume      INTEGER,
            PRIMARY KEY (ticker, date)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS fundamentals (
            ticker          TEXT,
            statement_type  TEXT,
            fiscal_date     TEXT,
            raw_data_json   TEXT,
            PRIMARY KEY (ticker, statement_type, fiscal_date)
        )
    """)

    # Computed, clean columns — no JSON parsing at query time
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS computed_metrics (
            ticker              TEXT,
            as_of_date          TEXT,       -- date this snapshot was computed
            revenue_ltm         REAL,       -- last twelve months revenue ($)
            revenue_growth_yoy  REAL,       -- % YoY revenue growth (LTM vs prior LTM)
            gross_profit_ltm    REAL,
            gross_margin        REAL,       -- gross_profit_ltm / revenue_ltm * 100
            operating_income_ltm REAL,
            operating_margin    REAL,
            ebit_ltm            REAL,
            net_income_ltm      REAL,
            ocf_ltm             REAL,       -- operating cash flow LTM
            capex_ltm           REAL,       -- capital expenditure LTM (absolute value)
            fcf_ltm             REAL,       -- ocf_ltm - capex_ltm
            fcf_margin          REAL,       -- fcf_ltm / revenue_ltm * 100
            sbc_ltm             REAL,       -- stock-based compensation LTM
            sbc_pct_revenue     REAL,       -- sbc_ltm / revenue_ltm * 100
            fcf_margin_adj      REAL,       -- (fcf_ltm - sbc_ltm) / revenue_ltm * 100
            cash_and_equiv      REAL,       -- from most recent balance sheet
            total_debt          REAL,
            net_cash            REAL,       -- cash_and_equiv - total_debt
            total_equity        REAL,
            debt_equity_ratio   REAL,       -- total_debt / total_equity (None if equity <= 0)
            monthly_burn        REAL,       -- abs(fcf_ltm) / 12 if fcf negative, else None
            cash_runway_months  REAL,       -- cash_and_equiv / monthly_burn, capped at 999
            operating_leverage  REAL,       -- op_income growth - revenue_growth (pp)
            roic                REAL,       -- EBIT / (total_assets - current_liabilities) * 100
            inventory           REAL,       -- from most recent balance sheet
            total_assets        REAL,       -- from most recent balance sheet
            ppe_net             REAL,       -- property plant equipment net
            rd_expense_ltm      REAL,       -- R&D expense LTM
            current_liabilities REAL,       -- from most recent balance sheet
            shares_outstanding  REAL,       -- from yfinance
            market_cap          REAL,       -- from yfinance
            enterprise_value    REAL,       -- market_cap + total_debt - cash
            da_ltm              REAL,       -- depreciation & amortization LTM
            ebitda_ltm          REAL,       -- operating_income + D&A
            eps_ltm             REAL,       -- net_income / shares_outstanding
            net_income_prior    REAL,       -- prior year LTM net income (for PEG)
            reported_currency   TEXT,       -- e.g. "USD"; non-USD tickers are excluded from classification
            is_self_funded      INTEGER,    -- 1 if FCF >= 0 (replaces the 999 runway sentinel for stage scoring)
            net_income_quality_flag INTEGER, -- 1 if NI diverges from OpInc*(1-tax) by >50% (e.g. warrant gains)
            deferred_revenue    REAL,       -- from yfinance.quarterly_balance_sheet (current + non-current)
            deferred_revenue_ratio REAL,    -- deferred_revenue / revenue_ltm; drives the SaaS subscription gate
            PRIMARY KEY (ticker, as_of_date)
        )
    """)

    # Migration: existing DBs created before the schema additions need the new
    # columns. SQLite has no IF NOT EXISTS for ALTER, so we sniff PRAGMA.
    existing = {row[1] for row in cursor.execute("PRAGMA table_info(computed_metrics)").fetchall()}
    for col, decl in [
        ("reported_currency", "TEXT"),
        ("is_self_funded", "INTEGER"),
        ("net_income_quality_flag", "INTEGER"),
        ("deferred_revenue", "REAL"),
        ("deferred_revenue_ratio", "REAL"),
        # RPO directional-qualifier inputs. Populated from Prong 2 (SEC 10-Q
        # extraction) via _read_manual_metric. rpo and rpo_prior_year are raw
        # USD values; rpo_growth_yoy and rpo_revenue_spread are derived.
        # Only used by hyperscale-S2/S3 and saas-S2/S3 valuation cells.
        ("rpo", "REAL"),
        ("rpo_prior_year", "REAL"),
        ("rpo_growth_yoy", "REAL"),
        ("rpo_revenue_spread", "REAL"),
    ]:
        if col not in existing:
            cursor.execute(f"ALTER TABLE computed_metrics ADD COLUMN {col} {decl}")

    # `classifications` is created later in this same function — guard the
    # migration so it only runs if the table actually exists from a prior run.
    cls_info = cursor.execute("PRAGMA table_info(classifications)").fetchall()
    if cls_info:
        existing_cls = {row[1] for row in cls_info}
        if "bm_demotion_reason" not in existing_cls:
            cursor.execute("ALTER TABLE classifications ADD COLUMN bm_demotion_reason TEXT")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS classifications (
            ticker                  TEXT,
            as_of_date              TEXT,
            -- Financial health
            fh_fcf_stage            INTEGER,    -- 1-4 score for FCF margin
            fh_gm_stage             INTEGER,
            fh_runway_stage         INTEGER,
            fh_oplev_stage          INTEGER,
            fh_sbc_stage            INTEGER,
            fh_de_stage             INTEGER,
            fh_roic_stage           INTEGER,    -- 1-4 score for ROIC
            fh_weighted_score       REAL,       -- weighted average before cap
            fh_stage                INTEGER,    -- final stage 1-4
            fh_fcf_hard_cap_applied INTEGER,    -- 1 if FCF hard cap fired
            -- Business model
            bm_category             TEXT,       -- saas | hyperscale | semi_hardware | consumer_internet | deep_tech
            bm_method               TEXT,       -- 'validator' or 'llm_tiebreaker'
            bm_confidence           TEXT,       -- HIGH or LOW
            bm_decision_trace       TEXT,       -- JSON list of rule checks
            bm_validators_json      TEXT,       -- full pass/fail for all 5 validators
            bm_llm_rationale        TEXT,       -- LLM explanation (NULL if validator-only)
            bm_demotion_reason      TEXT,       -- set when SaaS gate demotes (e.g. "saas_no_subscription_evidence")
            -- Summary
            matrix_cell             TEXT,       -- e.g. "saas-3"
            classified_at           TEXT,       -- timestamp
            PRIMARY KEY (ticker, as_of_date)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS valuations (
            ticker              TEXT,
            as_of_date          TEXT,
            matrix_cell         TEXT,       -- e.g. "saas-3"
            -- Core multiples (all API-derived)
            ev_revenue          REAL,       -- EV / TTM revenue
            ps_ratio            REAL,       -- market_cap / TTM revenue
            pe_ratio            REAL,       -- price / EPS
            ev_fcf              REAL,       -- EV / (OCF - capex - SBC)
            fcf_yield           REAL,       -- FCF / market_cap * 100
            ev_ebitda           REAL,       -- EV / EBITDA
            peg_ratio           REAL,       -- P/E / avg 2yr earnings growth
            capex_adj_ev_ebit   REAL,       -- EV / (EBIT - maintenance capex)
            ev_gross_profit     REAL,       -- EV / gross_profit
            capex_revenue       REAL,       -- capex / revenue
            ntm_revenue         REAL,       -- TTM rev * (1 + growth)
            rule_of_40          REAL,       -- rev_growth% + FCF_margin%
            cycle_adj_pe        REAL,       -- price / avg EPS over N years
            ev_ntm_revenue      REAL,       -- EV / NTM revenue
            -- Manual metrics (from JSON)
            arr                 REAL,       -- annual recurring revenue
            ntm_arr             REAL,       -- ARR * (1 + growth)
            ev_ntm_arr          REAL,       -- EV / NTM ARR
            mau                 REAL,       -- monthly active users
            tcv                 REAL,       -- total contract value
            -- Routing
            primary_method      TEXT,       -- which valuation method the matrix selected
            valuation_json      TEXT,       -- JSON with all computed multiples for this cell
            computed_at         TEXT,
            PRIMARY KEY (ticker, as_of_date)
        )
    """)

    # Financial Health stage history — populated by backfill_fh_history()
    # below. Stores last-N-quarters FH stage assignments per ticker so the
    # UI can render a trajectory timeline. Pipeline auto-refreshes this on
    # every run_classification() call.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS fh_stage_history (
            ticker                  TEXT NOT NULL,
            as_of_date              TEXT NOT NULL,
            fh_stage                INTEGER,
            fh_weighted_score       REAL,
            fh_fcf_hard_cap_applied INTEGER,
            computed_at             TEXT,
            PRIMARY KEY (ticker, as_of_date)
        )
    """)

    # Index for fast backtest range queries
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_price_ticker_date ON price_data (ticker, date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_fund_ticker_type  ON fundamentals (ticker, statement_type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_metrics_ticker    ON computed_metrics (ticker, as_of_date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_class_ticker      ON classifications (ticker, as_of_date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_val_ticker        ON valuations (ticker, as_of_date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_fhh_ticker_date   ON fh_stage_history (ticker, as_of_date DESC)")

    # Point-in-time filing date (AV EARNINGS `reportedDate`). Additive column —
    # existing rows keep NULL until backfill_fundamentals.py populates them, and
    # nothing in the live pipeline SELECTs it, so this is backward-compatible.
    # Enables the backtester to filter on the true report date instead of a flat
    # 45-day fiscal-end lag (look-ahead-bias fix).
    _fund_cols = {r[1] for r in cursor.execute("PRAGMA table_info(fundamentals)")}
    if "reported_date" not in _fund_cols:
        cursor.execute("ALTER TABLE fundamentals ADD COLUMN reported_date TEXT")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_fund_reported ON fundamentals (ticker, statement_type, reported_date)")

    conn.commit()
    log.info("Database schema initialised.")


# ==========================================
# 2. PRICE DATA (yfinance)
# ==========================================
def fetch_and_store_price_data(conn: sqlite3.Connection, ticker: str) -> None:
    """
    Pulls YEARS_OF_DATA years of daily OHLCV and stores to price_data.
    Fixes: timezone-aware timestamps stripped before strftime; literal period string.
    """
    log.info(f"[{ticker}] Fetching price data ({YEARS_OF_DATA}y)...")
    try:
        stock = yf.Ticker(ticker)
        # FIX: yfinance requires a literal period string — f-string interpolation
        # produces e.g. "5y" correctly but only if YEARS_OF_DATA is an int.
        # Validate here to be explicit.
        period_str = f"{int(YEARS_OF_DATA)}y"
        hist = stock.history(period=period_str, auto_adjust=True)

        if hist.empty:
            log.warning(f"[{ticker}] No price data returned.")
            return

        hist.reset_index(inplace=True)

        records = []
        for _, row in hist.iterrows():
            # FIX: yfinance returns timezone-aware Timestamps (UTC).
            # .strftime() on a tz-aware Timestamp works in newer pandas but
            # can raise on some versions. Normalise to naive UTC explicitly.
            date_val = row["Date"]
            if hasattr(date_val, "tzinfo") and date_val.tzinfo is not None:
                date_val = date_val.tz_convert("UTC").tz_localize(None)
            date_str = pd.Timestamp(date_val).strftime("%Y-%m-%d")

            records.append((
                ticker,
                date_str,
                round(float(row["Open"]),   4),
                round(float(row["High"]),   4),
                round(float(row["Low"]),    4),
                round(float(row["Close"]),  4),
                int(row["Volume"]),
            ))

        cursor = conn.cursor()
        cursor.executemany("""
            INSERT OR IGNORE INTO price_data (ticker, date, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, records)
        conn.commit()
        log.info(f"[{ticker}] Stored {len(records)} days of price data.")

    except Exception as e:
        log.error(f"[{ticker}] Price data error: {e}", exc_info=True)


# ==========================================
# 3. FUNDAMENTAL DATA (Alpha Vantage)
# ==========================================
def _av_fetch(ticker: str, function: str, _attempt: int = 0) -> Optional[dict]:
    """
    Single Alpha Vantage API call with error normalisation and tier-aware retry.

    Free tier:  on rate-limit message → return None immediately (caller
                interprets as daily quota exhausted → hard stop).
    Pro tier:   on rate-limit message → exponential backoff and retry up to
                AV_RATE_LIMIT_RETRIES times, then return None.
                Genuine errors (Error Message, delisted ticker) are never retried.
    """
    global AV_LAST_FAILURE_REASON
    if _attempt == 0:
        AV_LAST_FAILURE_REASON = None
    url = (
        f"https://www.alphavantage.co/query"
        f"?function={function}&symbol={ticker}&apikey={ALPHA_VANTAGE_API_KEY}"
    )
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        # AV returns 200 OK even for errors — must inspect body.

        # Rate-limit / info messages ("Information" or "Note" keys).
        rate_msg = data.get("Information") or data.get("Note")
        if rate_msg:
            # Pro tier: the only limit is the per-minute burst ceiling.
            # Always absorb it with exponential backoff before giving up.
            if _attempt < AV_RATE_LIMIT_RETRIES:
                wait = AV_RATE_LIMIT_BACKOFF[_attempt]
                log.warning(
                    f"[{ticker}] AV per-minute limit hit "
                    f"(attempt {_attempt + 1}/{AV_RATE_LIMIT_RETRIES}) — "
                    f"backing off {wait}s then retrying."
                )
                time.sleep(wait)
                return _av_fetch(ticker, function, _attempt + 1)
            # Retries exhausted — surface to caller.
            AV_LAST_FAILURE_REASON = "rate_limit"
            log.warning(f"[{ticker}] AV rate limit persisted after "
                        f"{AV_RATE_LIMIT_RETRIES} retries: {rate_msg[:120]}")
            return None

        # Hard errors: bad symbol, invalid function, etc. Never retry.
        if "Error Message" in data:
            AV_LAST_FAILURE_REASON = "api_error"
            log.warning(f"[{ticker}] AV error: {data['Error Message']}")
            return None

        return data

    except requests.exceptions.Timeout:
        AV_LAST_FAILURE_REASON = "timeout"
        log.error(f"[{ticker}] AV request timed out for {function}")
        return None
    except Exception as e:
        log.error(f"[{ticker}] AV fetch error ({function}): {e}", exc_info=True)
        return None


def fetch_and_store_fundamentals(
    conn: sqlite3.Connection, ticker: str, statement_type: str
) -> int:
    """
    Fetches quarterly reports from Alpha Vantage and stores raw JSON rows.
    statement_type: INCOME_STATEMENT | CASH_FLOW | BALANCE_SHEET
    Returns number of records stored.
    """
    log.info(f"[{ticker}] Fetching {statement_type}...")
    data = _av_fetch(ticker, statement_type)
    if data is None:
        return 0

    reports = data.get("quarterlyReports", [])
    if not reports:
        global AV_LAST_FAILURE_REASON
        AV_LAST_FAILURE_REASON = "empty"
        log.warning(f"[{ticker}] No quarterly reports found for {statement_type}.")
        return 0

    # Currency check (fix #2): AV's `reportedCurrency` is unreliable — it
    # returns the literal string "None" for some tickers (observed on both
    # IONQ which is USD, and TSM which is TWD), so we can't trust it on its
    # own. The currency authority is yfinance's `financialCurrency` field,
    # checked in compute_and_store_metrics where we already call yfinance.

    max_quarters = YEARS_OF_DATA * 4
    records = []
    for report in reports[:max_quarters]:
        fiscal_date = report.get("fiscalDateEnding")
        if not fiscal_date:
            continue
        records.append((ticker, statement_type, fiscal_date, json.dumps(report)))

    cursor = conn.cursor()
    cursor.executemany("""
        INSERT OR IGNORE INTO fundamentals (ticker, statement_type, fiscal_date, raw_data_json)
        VALUES (?, ?, ?, ?)
    """, records)
    conn.commit()
    log.info(f"[{ticker}] Stored {len(records)} quarters of {statement_type}.")
    return len(records)


# ==========================================
# 4. COMPUTE LTM METRICS
# ==========================================
def _safe_float(val, default: float = 0.0) -> float:
    """Convert AV string values (including 'None') to float safely.

    Use for LTM SUMS only — those naturally tolerate missing-as-zero because
    you're summing 4 quarters. For point-in-time balance-sheet reads where
    'missing' is meaningfully different from 'zero', use `_av_num` instead.
    """
    if val is None or val == "None" or val == "":
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _av_num(raw, default=None):
    """Stricter coercion: returns None (not 0) for AV's "None" string.

    Critical for balance-sheet fields like PPE, where 0 means "company has no
    PPE" (a real signal — pure SaaS) but "None" means "AV didn't expose this"
    (data gap). Conflating the two — as the old `.get(a) or .get(b)` chain
    did — broke the hyperscale BM validator for MSFT, AMZN, GOOGL etc.

    BM validators must abstain (not fail) when a critical input is None.
    """
    if raw is None or raw == "None" or raw == "":
        return default
    try:
        return float(raw)
    except (ValueError, TypeError):
        return default


def _av_first(raw_dict: dict, *keys, default=None):
    """First non-None among `keys` in `raw_dict`, using _av_num coercion.

    Replaces the `latest_bs.get(a) or latest_bs.get(b)` idiom, which mis-fired
    because AV's literal "None" string is truthy.
    """
    for k in keys:
        v = _av_num(raw_dict.get(k))
        if v is not None:
            return v
    return default


def _load_quarterly_series(
    conn: sqlite3.Connection, ticker: str, statement_type: str
) -> list[dict]:
    """
    Returns list of parsed quarterly report dicts, sorted descending by fiscal_date.
    """
    cursor = conn.cursor()
    cursor.execute("""
        SELECT fiscal_date, raw_data_json FROM fundamentals
        WHERE ticker = ? AND statement_type = ?
        ORDER BY fiscal_date DESC
    """, (ticker, statement_type))
    rows = cursor.fetchall()
    return [{"fiscal_date": r[0], **json.loads(r[1])} for r in rows]


def _ltm_sum(series: list[dict], field: str, n: int = 4) -> float:
    """Sum a field across the most recent n quarters (LTM = last 4)."""
    return sum(_safe_float(q.get(field)) for q in series[:n])


def compute_and_store_metrics(conn: sqlite3.Connection, ticker: str) -> bool:
    """
    Derives clean LTM metrics from raw quarterly fundamentals and writes to
    computed_metrics. Returns True if successful.

    Calculations applied:
      FCF = OCF - capex (capex stored as negative in AV cash flow, so we abs() it)
      FCF margin adj = (FCF - SBC) / Revenue * 100  [SBC correction for economic reality]
      Debt/equity: skipped if equity <= 0 (negative book equity edge case)
      Cash runway: cash / (abs(FCF)/12) — only meaningful when FCF is negative
      Operating leverage: LTM op income growth rate minus LTM revenue growth rate
    """
    inc = _load_quarterly_series(conn, ticker, "INCOME_STATEMENT")
    cf  = _load_quarterly_series(conn, ticker, "CASH_FLOW")
    bs  = _load_quarterly_series(conn, ticker, "BALANCE_SHEET")

    if not inc or not cf or not bs:
        log.warning(f"[{ticker}] Missing one or more statement types — skipping metrics.")
        return False

    as_of_date = inc[0]["fiscal_date"]  # most recent quarter end

    # --- Income statement LTM ---
    revenue_ltm          = _ltm_sum(inc, "totalRevenue")
    gross_profit_ltm     = _ltm_sum(inc, "grossProfit")
    op_income_ltm        = _ltm_sum(inc, "operatingIncome")
    ebit_ltm             = _ltm_sum(inc, "ebit")
    net_income_ltm       = _ltm_sum(inc, "netIncome")
    sbc_ltm              = _ltm_sum(inc, "researchAndDevelopment")  # placeholder —
    # NOTE: AV Income Statement does not expose SBC directly.
    # The correct field is in the Cash Flow statement as "stockBasedCompensation".
    sbc_ltm              = _ltm_sum(cf, "stockBasedCompensation")

    # Prior year LTM for growth calculations (quarters 5-8)
    revenue_prior        = _ltm_sum(inc, "totalRevenue", 8) - revenue_ltm
    op_income_prior      = _ltm_sum(inc, "operatingIncome", 8) - op_income_ltm

    # --- Cash flow LTM ---
    ocf_ltm   = _ltm_sum(cf, "operatingCashflow")
    # AV reports capex as negative — take absolute value
    capex_ltm = abs(_ltm_sum(cf, "capitalExpenditures"))
    fcf_ltm   = ocf_ltm - capex_ltm

    # --- Balance sheet (most recent quarter only — point-in-time) ---
    # All reads use _av_num/_av_first so AV's "None" string becomes None, not 0.
    # None = "AV didn't expose this field for this ticker" (data gap).
    # 0    = "company genuinely has none of this" (a real signal).
    # Conflating the two was bug #1 — broke hyperscale BM validation for MSFT.
    latest_bs        = bs[0] if bs else {}
    cash_and_equiv   = _av_num(latest_bs.get("cashAndCashEquivalentsAtCarryingValue"))
    short_term_debt  = _av_first(latest_bs, "shortTermDebt", "currentPortionOfLongTermDebt")
    long_term_debt   = _av_first(latest_bs, "longTermDebtNoncurrent", "longTermDebt")
    total_debt       = (short_term_debt or 0) + (long_term_debt or 0) if (short_term_debt is not None or long_term_debt is not None) else None
    total_equity     = _av_first(latest_bs, "totalShareholderEquity", "stockholdersEquity")
    net_cash         = ((cash_and_equiv or 0) - (total_debt or 0)) if (cash_and_equiv is not None and total_debt is not None) else None

    inventory        = _av_num(latest_bs.get("inventory"))
    total_assets     = _av_num(latest_bs.get("totalAssets"))
    ppe_net          = _av_first(latest_bs, "propertyPlantEquipment", "propertyPlantAndEquipmentNet")
    current_liabilities = _av_first(latest_bs, "totalCurrentLiabilities", "currentNetLiabilities")

    # R&D expense LTM (from income statement)
    rd_expense_ltm   = _ltm_sum(inc, "researchAndDevelopment")

    # --- Derived ratios ---
    def pct(numerator, denominator):
        return round(numerator / denominator * 100, 4) if denominator and denominator != 0 else None

    gross_margin     = pct(gross_profit_ltm, revenue_ltm)
    operating_margin = pct(op_income_ltm, revenue_ltm)
    fcf_margin       = pct(fcf_ltm, revenue_ltm)
    sbc_pct_revenue  = pct(sbc_ltm, revenue_ltm)

    # SBC-adjusted FCF margin — the economically honest number
    fcf_margin_adj   = pct(fcf_ltm - sbc_ltm, revenue_ltm) if revenue_ltm else None

    # Revenue growth YoY — null when prior base is tiny (pre-revenue inflation)
    if revenue_prior and abs(revenue_prior) >= MIN_PRIOR_BASE_REVENUE:
        revenue_growth_yoy = pct(revenue_ltm - revenue_prior, revenue_prior)
    else:
        revenue_growth_yoy = None
        if revenue_prior:
            log.info(f"[{ticker}] revenue_growth_yoy=None (prior base ${revenue_prior:,.0f} < ${MIN_PRIOR_BASE_REVENUE:,.0f})")

    # Operating leverage (pp): op income growth rate - revenue growth rate.
    # Same guard against tiny prior op income blowing up the percentage.
    if op_income_prior and abs(op_income_prior) >= MIN_PRIOR_BASE_OP_INCOME:
        op_income_growth = pct(op_income_ltm - op_income_prior, abs(op_income_prior))
    else:
        op_income_growth = None
    operating_leverage = (
        round(op_income_growth - revenue_growth_yoy, 2)
        if op_income_growth is not None and revenue_growth_yoy is not None
        else None
    )

    # Debt / equity — skip if equity is zero/negative or debt is unknown
    debt_equity_ratio = (
        round(total_debt / total_equity, 4)
        if (total_debt is not None and total_equity and total_equity > 0)
        else None
    )

    # Cash runway + self-funded flag (fix #3): explicit boolean replaces the
    # magic 999 sentinel that fell into no FH_RANGES bucket.
    is_self_funded = fcf_ltm is not None and fcf_ltm >= 0
    monthly_burn = abs(fcf_ltm) / 12 if fcf_ltm is not None and fcf_ltm < 0 else None
    if is_self_funded:
        cash_runway_months = None  # not applicable; classifier reads is_self_funded directly
    elif monthly_burn and monthly_burn > 0 and cash_and_equiv:
        cash_runway_months = min(round(cash_and_equiv / monthly_burn, 1), 999)
    else:
        cash_runway_months = None

    # ROIC = EBIT / invested capital
    invested_capital = (
        total_assets - current_liabilities
        if (total_assets is not None and current_liabilities is not None)
        else None
    )
    roic = (
        pct(ebit_ltm, invested_capital)
        if invested_capital and invested_capital > 0 and ebit_ltm is not None
        else None
    )

    # Net income quality flag (fix #7): trip when reported NI diverges from
    # operating-income-after-tax by more than 50% of |NI|. Catches warrant
    # fair-value gains (IONQ), large one-time tax benefits, etc. Don't
    # correct the number — flag so the valuation layer can route around P/E.
    net_income_quality_flag = 0
    if net_income_ltm is not None and op_income_ltm is not None and abs(net_income_ltm) > 0:
        implied_after_tax = op_income_ltm * (1 - EST_TAX_RATE)
        if abs(net_income_ltm - implied_after_tax) > abs(net_income_ltm) * 0.5:
            net_income_quality_flag = 1
            log.warning(
                f"[{ticker}] net_income_quality_flag=1  "
                f"NI={net_income_ltm:,.0f}  OpInc*(1-tax)={implied_after_tax:,.0f}"
            )

    # --- Valuation inputs ---
    # D&A from cash flow statement
    # AV's cash-flow D&A field is `depreciationDepletionAndAmortization`; the
    # older names below are AV fallbacks/legacy and almost always absent. Using
    # only the wrong name silently zeroed D&A → EBITDA collapsed to operating
    # income and every EV/EBITDA cell was understated.
    da_ltm = (_ltm_sum(cf, "depreciationDepletionAndAmortization")
              or _ltm_sum(cf, "depreciationAmortization")
              or _ltm_sum(cf, "depreciation"))
    ebitda_ltm = (op_income_ltm or 0) + (da_ltm or 0) if op_income_ltm is not None else None

    # Prior year net income (quarters 5-8) for PEG calculation
    net_income_prior = _ltm_sum(inc, "netIncome", 8) - net_income_ltm

    # Shares outstanding, market cap, financialCurrency, AND deferred revenue
    # from yfinance. AV doesn't expose deferredRevenue (every ticker returns
    # the literal "None"), so we fall back to yfinance.quarterly_balance_sheet
    # which exposes Current Deferred Revenue + Non Current Deferred Revenue
    # for all tickers tested. Used by the SaaS subscription gate.
    financial_currency = None
    deferred_revenue = None
    try:
        tk = yf.Ticker(ticker)
        info = tk.info
        shares_outstanding = info.get("sharesOutstanding")
        market_cap = info.get("marketCap")
        financial_currency = info.get("financialCurrency")
    except Exception:
        shares_outstanding = None
        market_cap = None

    reported_currency = financial_currency or "USD"
    if financial_currency and financial_currency != "USD":
        log.warning(f"[{ticker}] excluded_currency: yfinance.financialCurrency={financial_currency} — skipping metrics.")
        EXCLUDED_TICKERS_NON_USD.add(ticker)
        return False

    try:
        bs = yf.Ticker(ticker).quarterly_balance_sheet
        if bs is not None and not bs.empty:
            latest_col = bs.columns[0]
            current_def = bs.loc["Current Deferred Revenue", latest_col] if "Current Deferred Revenue" in bs.index else 0
            noncurrent_def = bs.loc["Non Current Deferred Revenue", latest_col] if "Non Current Deferred Revenue" in bs.index else 0
            current_def = float(current_def) if current_def == current_def else 0  # NaN check
            noncurrent_def = float(noncurrent_def) if noncurrent_def == noncurrent_def else 0
            total_def = current_def + noncurrent_def
            deferred_revenue = total_def if total_def > 0 else None
    except Exception as e:
        log.warning(f"[{ticker}] deferred revenue fetch failed: {e}")

    deferred_revenue_ratio = (
        deferred_revenue / revenue_ltm
        if (deferred_revenue is not None and revenue_ltm and revenue_ltm > 0)
        else None
    )

    # RPO from Prong 2 (10-Q extraction landed in manual_metrics.json by
    # metrics_extractor.py). Stored as raw USD via _read_manual_metric. If
    # the ticker hasn't been through Prong 2 yet or RPO wasn't disclosed,
    # all three values stay None and the cell falls back to its primary
    # multiple with no qualifier flag (per the spec: "do not block").
    rpo_manual = _load_manual_metrics(ticker)
    rpo = _read_manual_metric(rpo_manual, "rpo")
    rpo_prior_year = _read_manual_metric(rpo_manual, "rpo_prior_year")
    if rpo is not None and rpo_prior_year is not None and rpo_prior_year > 0:
        rpo_growth_yoy = (rpo / rpo_prior_year - 1) * 100
    else:
        rpo_growth_yoy = None
    if rpo_growth_yoy is not None and revenue_growth_yoy is not None:
        rpo_revenue_spread = round(rpo_growth_yoy - revenue_growth_yoy, 2)
    else:
        rpo_revenue_spread = None

    # EPS
    eps_ltm = (
        round(net_income_ltm / shares_outstanding, 4)
        if shares_outstanding and shares_outstanding > 0 and net_income_ltm is not None
        else None
    )

    # Enterprise value
    enterprise_value = (
        (market_cap or 0) + (total_debt or 0) - (cash_and_equiv or 0)
        if market_cap else None
    )

    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO computed_metrics (
            ticker, as_of_date,
            revenue_ltm, revenue_growth_yoy, gross_profit_ltm, gross_margin,
            operating_income_ltm, operating_margin, ebit_ltm, net_income_ltm,
            ocf_ltm, capex_ltm, fcf_ltm, fcf_margin, sbc_ltm, sbc_pct_revenue,
            fcf_margin_adj, cash_and_equiv, total_debt, net_cash, total_equity,
            debt_equity_ratio, monthly_burn, cash_runway_months, operating_leverage,
            roic, inventory, total_assets, ppe_net, rd_expense_ltm, current_liabilities,
            shares_outstanding, market_cap, enterprise_value, da_ltm, ebitda_ltm,
            eps_ltm, net_income_prior,
            reported_currency, is_self_funded, net_income_quality_flag,
            deferred_revenue, deferred_revenue_ratio,
            rpo, rpo_prior_year, rpo_growth_yoy, rpo_revenue_spread
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?, ?, ?
        )
    """, (
        ticker, as_of_date,
        revenue_ltm, revenue_growth_yoy, gross_profit_ltm, gross_margin,
        op_income_ltm, operating_margin, ebit_ltm, net_income_ltm,
        ocf_ltm, capex_ltm, fcf_ltm, fcf_margin, sbc_ltm, sbc_pct_revenue,
        fcf_margin_adj, cash_and_equiv, total_debt, net_cash, total_equity,
        debt_equity_ratio, monthly_burn, cash_runway_months, operating_leverage,
        roic, inventory, total_assets, ppe_net, rd_expense_ltm, current_liabilities,
        shares_outstanding, market_cap, enterprise_value, da_ltm, ebitda_ltm,
        eps_ltm, net_income_prior,
        reported_currency, int(is_self_funded), net_income_quality_flag,
        deferred_revenue, deferred_revenue_ratio,
        rpo, rpo_prior_year, rpo_growth_yoy, rpo_revenue_spread,
    ))
    conn.commit()
    log.info(f"[{ticker}] Metrics computed and stored (as_of {as_of_date}).")
    return True


# ==========================================
# 5. CLASSIFICATION ENGINE
# ==========================================

# --- Financial health: stage-varying weights (from spreadsheet) ---
# Each stage has its own weight vector — weights sum to 1.0 per stage.
FH_STAGE_WEIGHTS = {
    1: {"fcf_margin_adj": 0.30, "cash_runway_months": 0.40, "gross_margin": 0.15,
        "operating_leverage": 0.00, "sbc_pct_revenue": 0.10, "debt_equity_ratio": 0.05, "roic": 0.00},
    2: {"fcf_margin_adj": 0.30, "cash_runway_months": 0.15, "gross_margin": 0.10,
        "operating_leverage": 0.25, "sbc_pct_revenue": 0.15, "debt_equity_ratio": 0.05, "roic": 0.00},
    3: {"fcf_margin_adj": 0.30, "cash_runway_months": 0.00, "gross_margin": 0.15,
        "operating_leverage": 0.20, "sbc_pct_revenue": 0.05, "debt_equity_ratio": 0.10, "roic": 0.20},
    4: {"fcf_margin_adj": 0.35, "cash_runway_months": 0.00, "gross_margin": 0.15,
        "operating_leverage": 0.05, "sbc_pct_revenue": 0.05, "debt_equity_ratio": 0.15, "roic": 0.25},
}

# Threshold ranges per stage: (lower_bound, upper_bound) — value must fall in range to score 1.0
FH_RANGES = {
    "fcf_margin_adj":     [(-999, -15), (-15, 6),  (8, 22),    (22, 999)],
    "cash_runway_months": [(0, 18),     (18, 48),  (48, 999),  (48, 999)],
    "gross_margin":       [(0, 40),     (40, 65),  (65, 78),   (78, 999)],
    "operating_leverage": [(-999, 0),   (0, 5),    (5, 15),    (15, 999)],
    "sbc_pct_revenue":    [(15, 999),   (8, 15),   (3, 8),     (0, 3)],      # inverted
    "debt_equity_ratio":  [(3, 999),    (1, 3),    (0.2, 1),   (0, 0.2)],    # inverted
    "roic":               [(-999, 0),   (0, 5),    (5, 15),    (15, 999)],
}


def _stage_for_metric(metric: str, value, is_self_funded: bool = False) -> int:
    """Per-metric stage assignment per FH_RANGES. Stage in [1,4].

    Special cases:
    - cash_runway_months on a self-funded company → Stage 4 directly
      (fix #3: replaces the 999 sentinel that fell into no bucket).
    - value is None (data gap) → Stage 2 as a neutral fallback.
    """
    if metric == "cash_runway_months" and is_self_funded:
        return 4
    if value is None:
        return 2
    for s in (1, 2, 3, 4):
        lo, hi = FH_RANGES[metric][s - 1]
        if lo <= value < hi:
            return s
    # Above the top bucket of an ascending metric → Stage 4. Below the bottom
    # of a descending (inverted) metric is already handled by the (lo, hi)
    # check; only the "tail above max" case lands here.
    return 4


def classify_financial_health(metrics: dict) -> dict:
    """Documented method (CLAUDE.md), implemented exactly:

      1. Score each metric to a stage in [1,4] via FH_RANGES.
      2. Weighted-average those stage numbers using FH_STAGE_WEIGHTS for the
         current candidate stage. Iterate to a fixed point — the per-stage
         weights mean the average can shift between iterations. Converges
         in 1-2 passes in practice; bounded to 5 for safety.
      3. Round to integer in [1,4].
      4. FCF Stage-1 hard cap: if the FCF metric is Stage 1, the final stage
         cannot exceed Stage 2.

    Replaces the previous "best-fit score per stage" method, which let
    high-D/E + gross-margin weight tip companies like SNOW into Stage 4
    despite a per-metric profile of [2,3,2,1,1,4,1].
    """
    is_self_funded = bool(metrics.get("is_self_funded"))

    values = {
        "fcf_margin_adj":     metrics.get("fcf_margin_adj"),
        "cash_runway_months": metrics.get("cash_runway_months"),
        "gross_margin":       metrics.get("gross_margin"),
        "operating_leverage": metrics.get("operating_leverage"),
        "sbc_pct_revenue":    metrics.get("sbc_pct_revenue"),
        "debt_equity_ratio":  metrics.get("debt_equity_ratio"),
        "roic":               metrics.get("roic"),
    }

    per_metric_stage = {
        m: _stage_for_metric(m, v, is_self_funded=is_self_funded)
        for m, v in values.items()
    }

    # Initial guess: simple unweighted mean of per-metric stages.
    stage = int(round(sum(per_metric_stage.values()) / len(per_metric_stage)))
    stage = max(1, min(4, stage))

    # Iterate using the candidate stage's weight vector. FH_STAGE_WEIGHTS
    # values are percentages (sum to 100 per column); normalise to fractions.
    weighted_avg = float(stage)
    for _ in range(5):
        weights = FH_STAGE_WEIGHTS[stage]
        total_w = sum(weights.values()) or 1.0
        weighted_avg = sum(weights[m] * per_metric_stage[m] for m in weights) / total_w
        new_stage = max(1, min(4, int(round(weighted_avg))))
        if new_stage == stage:
            break
        stage = new_stage

    # FCF Stage-1 hard cap
    cap_applied = False
    if per_metric_stage["fcf_margin_adj"] == 1 and stage > 2:
        stage = 2
        cap_applied = True

    return {
        "fh_fcf_stage":            per_metric_stage["fcf_margin_adj"],
        "fh_gm_stage":             per_metric_stage["gross_margin"],
        "fh_runway_stage":         per_metric_stage["cash_runway_months"],
        "fh_oplev_stage":          per_metric_stage["operating_leverage"],
        "fh_sbc_stage":            per_metric_stage["sbc_pct_revenue"],
        "fh_de_stage":             per_metric_stage["debt_equity_ratio"],
        "fh_roic_stage":           per_metric_stage["roic"],
        "fh_weighted_score":       round(weighted_avg, 3),
        "fh_stage":                stage,
        "fh_fcf_hard_cap_applied": int(cap_applied),
    }


def _compute_fh_metrics_at(
    inc_all: list[dict],
    cf_all:  list[dict],
    bs_all:  list[dict],
    target_date: str,
) -> Optional[dict]:
    """
    Re-derive the FH-input metric subset for a HISTORICAL quarter end.

    Takes the full quarterly series for all three statements and a target
    fiscal_date string (e.g. "2024-12-31"). Slices each series to entries
    with fiscal_date <= target_date, then computes LTM versions of every
    metric `classify_financial_health()` reads.

    Mirrors the calculation logic in `compute_and_store_metrics()` but:
      - No DB writes.
      - No yfinance / network calls (purely from cached fundamentals).
      - No deferred-revenue / RPO / market-cap (FH classification doesn't need them).

    Returns the metrics dict ready for `classify_financial_health()`, or
    None if the filtered series has fewer than 8 quarters (insufficient
    for LTM + prior-LTM growth math).
    """
    inc = [q for q in inc_all if q.get("fiscal_date") and q["fiscal_date"] <= target_date]
    cf  = [q for q in cf_all  if q.get("fiscal_date") and q["fiscal_date"] <= target_date]
    bs  = [q for q in bs_all  if q.get("fiscal_date") and q["fiscal_date"] <= target_date]
    if len(inc) < 8 or len(cf) < 8 or len(bs) < 1:
        return None  # not enough history at this snapshot point

    # --- Income statement LTM (most recent 4 quarters at target_date) ---
    revenue_ltm        = _ltm_sum(inc, "totalRevenue")
    gross_profit_ltm   = _ltm_sum(inc, "grossProfit")
    op_income_ltm      = _ltm_sum(inc, "operatingIncome")
    ebit_ltm           = _ltm_sum(inc, "ebit")
    net_income_ltm     = _ltm_sum(inc, "netIncome")

    # Prior LTM (quarters 5-8) for growth math
    revenue_prior      = _ltm_sum(inc, "totalRevenue", 8) - revenue_ltm
    op_income_prior    = _ltm_sum(inc, "operatingIncome", 8) - op_income_ltm

    # --- Cash flow LTM ---
    ocf_ltm   = _ltm_sum(cf, "operatingCashflow")
    capex_ltm = abs(_ltm_sum(cf, "capitalExpenditures"))
    fcf_ltm   = ocf_ltm - capex_ltm
    sbc_ltm   = _ltm_sum(cf, "stockBasedCompensation")

    # --- Balance sheet (point-in-time at target_date — first row in filtered set) ---
    latest_bs           = bs[0]
    cash_and_equiv      = _av_num(latest_bs.get("cashAndCashEquivalentsAtCarryingValue"))
    short_term_debt     = _av_first(latest_bs, "shortTermDebt", "currentPortionOfLongTermDebt")
    long_term_debt      = _av_first(latest_bs, "longTermDebtNoncurrent", "longTermDebt")
    total_debt          = (short_term_debt or 0) + (long_term_debt or 0) if (short_term_debt is not None or long_term_debt is not None) else None
    total_equity        = _av_first(latest_bs, "totalShareholderEquity", "stockholdersEquity")
    total_assets        = _av_num(latest_bs.get("totalAssets"))
    current_liabilities = _av_first(latest_bs, "totalCurrentLiabilities", "currentNetLiabilities")

    # --- Derived metrics ---
    def pct(numerator, denominator):
        return round(numerator / denominator * 100, 4) if denominator and denominator != 0 else None

    gross_margin     = pct(gross_profit_ltm, revenue_ltm)
    sbc_pct_revenue  = pct(sbc_ltm, revenue_ltm)
    fcf_margin_adj   = pct(fcf_ltm - sbc_ltm, revenue_ltm) if revenue_ltm else None

    # Revenue growth — same MIN_PRIOR_BASE guard as the live computation
    if revenue_prior and abs(revenue_prior) >= MIN_PRIOR_BASE_REVENUE:
        revenue_growth_yoy = pct(revenue_ltm - revenue_prior, revenue_prior)
    else:
        revenue_growth_yoy = None

    # Operating leverage (pp)
    if op_income_prior and abs(op_income_prior) >= MIN_PRIOR_BASE_OP_INCOME:
        op_income_growth = pct(op_income_ltm - op_income_prior, abs(op_income_prior))
    else:
        op_income_growth = None
    operating_leverage = (
        round(op_income_growth - revenue_growth_yoy, 2)
        if op_income_growth is not None and revenue_growth_yoy is not None
        else None
    )

    # Debt/equity — skip if equity is non-positive
    debt_equity_ratio = (
        round(total_debt / total_equity, 4)
        if (total_debt is not None and total_equity and total_equity > 0)
        else None
    )

    # Cash runway + self-funded flag
    is_self_funded = fcf_ltm is not None and fcf_ltm >= 0
    monthly_burn = abs(fcf_ltm) / 12 if fcf_ltm is not None and fcf_ltm < 0 else None
    if is_self_funded:
        cash_runway_months = None
    elif monthly_burn and monthly_burn > 0 and cash_and_equiv:
        cash_runway_months = min(round(cash_and_equiv / monthly_burn, 1), 999)
    else:
        cash_runway_months = None

    # ROIC = EBIT / invested capital
    invested_capital = (
        total_assets - current_liabilities
        if (total_assets is not None and current_liabilities is not None)
        else None
    )
    roic = (
        pct(ebit_ltm, invested_capital)
        if invested_capital and invested_capital > 0 and ebit_ltm is not None
        else None
    )

    return {
        "fcf_margin_adj":     fcf_margin_adj,
        "gross_margin":       gross_margin,
        "cash_runway_months": cash_runway_months,
        "operating_leverage": operating_leverage,
        "sbc_pct_revenue":    sbc_pct_revenue,
        "debt_equity_ratio":  debt_equity_ratio,
        "roic":               roic,
        "is_self_funded":     is_self_funded,
    }


def backfill_fh_history(conn: sqlite3.Connection, ticker: str, n_quarters: int = 8) -> int:
    """
    Compute and persist the last `n_quarters` of FH stage assignments for
    `ticker` into the fh_stage_history table.

    Reuses cached fundamentals — zero AV calls. Idempotent (INSERT OR REPLACE).
    Called automatically at the end of run_classification(); also runnable
    via the standalone backfill_fh_history.py bootstrap script.

    Returns the number of quarters successfully written.
    """
    inc_all = _load_quarterly_series(conn, ticker, "INCOME_STATEMENT")
    cf_all  = _load_quarterly_series(conn, ticker, "CASH_FLOW")
    bs_all  = _load_quarterly_series(conn, ticker, "BALANCE_SHEET")
    if not inc_all or not cf_all or not bs_all:
        return 0

    # Pick the most recent n_quarters distinct INCOME_STATEMENT fiscal_dates
    target_dates = []
    seen = set()
    for q in inc_all:
        fd = q.get("fiscal_date")
        if fd and fd not in seen:
            seen.add(fd)
            target_dates.append(fd)
            if len(target_dates) >= n_quarters:
                break

    cursor = conn.cursor()
    written = 0
    now_iso = datetime.utcnow().isoformat()
    for target_date in target_dates:
        metrics = _compute_fh_metrics_at(inc_all, cf_all, bs_all, target_date)
        if metrics is None:
            continue
        fh = classify_financial_health(metrics)
        cursor.execute(
            """
            INSERT OR REPLACE INTO fh_stage_history (
                ticker, as_of_date, fh_stage, fh_weighted_score,
                fh_fcf_hard_cap_applied, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                ticker, target_date, fh["fh_stage"], fh["fh_weighted_score"],
                fh["fh_fcf_hard_cap_applied"], now_iso,
            ),
        )
        written += 1
    conn.commit()
    return written


VALID_BM_CATEGORIES = ["saas", "hyperscale", "semi_hardware", "consumer_internet", "deep_tech"]


def _abstain(category: str, reason: str) -> dict:
    """Validator result used when a critical input is None (data gap).
    Abstaining validators are filtered out of the pass/fail count in
    classify_business_model so they don't false-fail an otherwise-clear
    category (e.g. hyperscale when AV doesn't expose PPE for MSFT)."""
    return {"category": category, "passed": False, "abstain": True,
            "confidence": 0.0, "rationale": f"abstain: {reason}"}


def _validate_saas(m: dict) -> dict:
    gm = m.get("gross_margin")
    ta = m.get("total_assets")
    if gm is None or ta is None or ta <= 0:
        return _abstain("saas", "gross_margin or total_assets missing")
    gm_frac = gm / 100
    inv = m.get("inventory")
    inv_ratio = (inv / ta) if inv is not None else 0  # inventory missing == zero inventory ok for SaaS
    passed = gm_frac >= 0.65 and inv_ratio < 0.01
    conf = 0.9 if (gm_frac >= 0.70 and inv_ratio < 0.005) else 0.6 if passed else 0.1
    return {"category": "saas", "passed": passed, "abstain": False, "confidence": conf,
            "rationale": f"GM={gm_frac:.0%}, inv/assets={inv_ratio:.4f}"}


def _validate_hyperscale(m: dict) -> dict:
    rev = m.get("revenue_ltm")
    ta = m.get("total_assets")
    capex = m.get("capex_ltm")
    ppe = m.get("ppe_net")
    # Hyperscale absolutely needs PPE (capex/rev alone isn't distinguishing).
    # If PPE is None (AV gap — common for MSFT/AMZN/GOOGL pre-fix), abstain
    # and let MANUAL_BM_OVERRIDES handle it.
    if rev is None or ta is None or rev <= 0 or ta <= 0:
        return _abstain("hyperscale", "revenue or total_assets missing")
    if ppe is None:
        return _abstain("hyperscale", "ppe_net not exposed by data source")
    capex_ratio = (capex or 0) / rev
    ppe_ratio = ppe / ta
    passed = capex_ratio > 0.12 and ppe_ratio > 0.25
    conf = 0.9 if (capex_ratio > 0.15 and ppe_ratio > 0.30) else 0.6 if passed else 0.1
    return {"category": "hyperscale", "passed": passed, "abstain": False, "confidence": conf,
            "rationale": f"capex/rev={capex_ratio:.3f}, PPE/assets={ppe_ratio:.3f}"}


def _validate_semi_hardware(m: dict) -> dict:
    ta = m.get("total_assets")
    if ta is None or ta <= 0:
        return _abstain("semi_hardware", "total_assets missing")
    inv = m.get("inventory")
    ppe = m.get("ppe_net")
    if inv is None and ppe is None:
        return _abstain("semi_hardware", "both inventory and ppe_net missing")
    inv_ratio = (inv / ta) if inv is not None else 0
    ppe_ratio = (ppe / ta) if ppe is not None else 0
    ppe_dominant = ppe_ratio > 0.20
    passed = inv_ratio > 0.05 or ppe_dominant
    conf = 0.9 if (inv_ratio > 0.10) else 0.6 if passed else 0.1
    return {"category": "semi_hardware", "passed": passed, "abstain": False, "confidence": conf,
            "rationale": f"inv/assets={inv_ratio:.4f}, PPE/assets={ppe_ratio:.3f}"}


def _validate_consumer_internet(m: dict) -> dict:
    rev = m.get("revenue_ltm")
    ta = m.get("total_assets")
    gm = m.get("gross_margin")
    if rev is None or ta is None or gm is None or rev <= 0 or ta <= 0:
        return _abstain("consumer_internet", "revenue, total_assets or gross_margin missing")
    capex_ratio = (m.get("capex_ltm") or 0) / rev
    inv = m.get("inventory")
    inv_ratio = (inv / ta) if inv is not None else 0
    passed = capex_ratio < 0.05 and inv_ratio < 0.01 and 55 <= gm <= 75
    conf = 0.8 if passed else 0.1
    return {"category": "consumer_internet", "passed": passed, "abstain": False, "confidence": conf,
            "rationale": f"capex/rev={capex_ratio:.3f}, inv/assets={inv_ratio:.4f}, GM={gm:.1f}%"}


def _validate_deep_tech(m: dict) -> dict:
    rev = m.get("revenue_ltm")
    ta = m.get("total_assets")
    op_income = m.get("operating_income_ltm")
    rd = m.get("rd_expense_ltm")
    if rev is None or ta is None or op_income is None or rd is None or rev <= 0 or ta <= 0:
        return _abstain("deep_tech", "revenue, total_assets, op_income or R&D missing")
    opex = max(abs(op_income) + rd, 1)
    rd_opex_ratio = rd / opex
    inv = m.get("inventory")
    inv_ratio = (inv / ta) if inv is not None else 0
    capex_ratio = (m.get("capex_ltm") or 0) / rev
    passed = rd_opex_ratio > 0.45 and op_income < 0
    supporting = capex_ratio < 0.05 and inv_ratio < 0.01
    conf = 0.85 if (passed and supporting) else 0.65 if passed else 0.1
    return {"category": "deep_tech", "passed": passed, "abstain": False, "confidence": conf,
            "rationale": f"R&D/opex={rd_opex_ratio:.2f}, opInc={op_income:,.0f}, supporting={supporting}"}


def _call_gemini_tiebreaker(ticker: str, contenders: list[dict], metrics: dict) -> dict:
    """Calls Gemini to resolve BM ambiguity. Returns {category, rationale}."""
    if not GEMINI_AVAILABLE or not GOOGLE_API_KEY:
        log.warning(f"[{ticker}] Gemini unavailable — defaulting to highest-confidence contender.")
        best = max(contenders, key=lambda c: c["confidence"]) if contenders else {"category": "deep_tech"}
        return {"category": best["category"], "rationale": "LLM unavailable, used highest confidence validator"}

    genai.configure(api_key=GOOGLE_API_KEY)
    # gemini-2.5-flash spends most of the token budget on internal thinking
    # before output, which truncated rationales to ~4 chars at LLM_MAX_TOKENS=150.
    # Switch to the lite variant (no thinking) — structured single-category
    # extraction is the right shape for it.
    model = genai.GenerativeModel(GEMINI_MODEL_NAME)

    contender_text = "\n".join(
        f"  - {c['category']} (confidence={c['confidence']:.2f}): {c['rationale']}"
        for c in contenders
    )
    key_ratios = (
        f"Gross margin: {metrics.get('gross_margin', 'N/A')}%, "
        f"Capex/Rev: {(metrics.get('capex_ltm',0) or 0)/max(metrics.get('revenue_ltm',1) or 1,1):.3f}, "
        f"Inv/Assets: {(metrics.get('inventory',0) or 0)/max(metrics.get('total_assets',1) or 1,1):.4f}, "
        f"PPE/Assets: {(metrics.get('ppe_net',0) or 0)/max(metrics.get('total_assets',1) or 1,1):.3f}, "
        f"R&D LTM: ${(metrics.get('rd_expense_ltm',0) or 0)/1e6:.0f}M"
    )

    prompt = f"""You are a financial analyst classifying {ticker} into exactly one business model category.

Contending categories from balance sheet validators:
{contender_text}

Key financial ratios:
{key_ratios}

You MUST respond with exactly one of these category strings on the first line, followed by a one-sentence rationale:
saas
hyperscale
semi_hardware
consumer_internet
deep_tech

Response format:
CATEGORY
Rationale sentence."""

    try:
        response = model.generate_content(
            prompt,
            generation_config=genai.GenerationConfig(max_output_tokens=LLM_MAX_TOKENS),
        )
        text = response.text.strip()
        lines = text.split("\n", 1)
        category = lines[0].strip().lower().replace(" ", "_")
        rationale = lines[1].strip() if len(lines) > 1 else "No rationale provided"
        if category not in VALID_BM_CATEGORIES:
            log.warning(f"[{ticker}] Gemini returned invalid category '{category}', defaulting.")
            category = max(contenders, key=lambda c: c["confidence"])["category"] if contenders else "deep_tech"
            rationale = f"LLM returned invalid '{lines[0]}', fell back to validator"
        return {"category": category, "rationale": rationale}
    except Exception as e:
        log.error(f"[{ticker}] Gemini call failed: {e}")
        best = max(contenders, key=lambda c: c["confidence"]) if contenders else {"category": "deep_tech"}
        return {"category": best["category"], "rationale": f"LLM error: {e}"}


def _apply_saas_gate(ticker: str, decision: dict, metrics: dict,
                      validator_results: list[dict]) -> dict:
    """Post-classification safety override: a ticker classified `saas` must
    show positive evidence of subscription billing. If not, demote to the
    next best non-saas category. Can only demote — never create a saas.

    Evidence (OR — any one is sufficient):
      1. deferred_revenue_ratio >= SAAS_MIN_DEFERRED  (billed in advance)
      2. has ARR seeded in manual_metrics.json        (human-tagged recurring)
      3. listed in MANUAL_BM_OVERRIDES as "saas"     (human-confirmed)

    Demotion routing: pick a non-saas validator pass; if 2+, LLM tiebreak
    among them; if 0, LLM picks from non-saas non-abstain results; final
    fallback is consumer_internet.
    """
    if decision["bm_category"] != "saas":
        return decision

    # Condition 1: deferred revenue ratio
    drr = metrics.get("deferred_revenue_ratio")
    cond_deferred = drr is not None and drr >= SAAS_MIN_DEFERRED

    # Condition 2: ARR seeded in manual_metrics.json (any shape).
    # Use _read_manual_metric so that structured entries with value=null
    # (i.e. "metrics_extractor checked and the company doesn't disclose ARR")
    # are correctly treated as ABSENT, not present-but-zero.
    manual = _load_manual_metrics(ticker)
    cond_manual_arr = _read_manual_metric(manual, "ARR") is not None

    # Condition 3: human-confirmed override
    cond_override = MANUAL_BM_OVERRIDES.get(ticker) == "saas"

    if cond_deferred or cond_manual_arr or cond_override:
        # Keeps saas; record the evidence for audit
        decision["bm_demotion_reason"] = None
        return decision

    # Demote. Pick from non-saas validators.
    non_saas = [r for r in validator_results if r["category"] != "saas" and not r.get("abstain")]
    passed_non_saas = [r for r in non_saas if r["passed"]]

    if len(passed_non_saas) == 1:
        new_cat = passed_non_saas[0]["category"]
        method = "validator_post_saas_demotion"
        rationale = passed_non_saas[0]["rationale"]
    elif len(passed_non_saas) >= 2:
        llm = _call_gemini_tiebreaker(ticker, passed_non_saas, metrics)
        new_cat = llm["category"]
        method = "llm_post_saas_demotion"
        rationale = llm["rationale"]
    elif non_saas:
        # No non-saas validator passed — let the LLM pick from the available
        # non-saas options, prompting it explicitly that saas was demoted.
        llm = _call_gemini_tiebreaker(ticker, non_saas, metrics)
        new_cat = llm["category"]
        method = "llm_post_saas_demotion_zero_pass"
        rationale = llm["rationale"]
    else:
        # No non-saas validators ran at all (all abstained). Default to
        # consumer_internet — high-GM + zero-inventory + no subscription
        # evidence is almost always an ad-supported platform.
        new_cat = "consumer_internet"
        method = "default_post_saas_demotion"
        rationale = "all non-saas validators abstained; default to consumer_internet"

    log.warning(
        f"[{ticker}] saas_no_subscription_evidence — demoting saas → {new_cat}  "
        f"(deferred_ratio={drr if drr is None else f'{drr:.3f}'}, "
        f"has_manual_ARR={cond_manual_arr}, override=False; method={method})"
    )

    return {
        "bm_category": new_cat,
        "bm_method": method,
        "bm_confidence": "LOW",
        "bm_decision_trace": json.dumps({
            "original": "saas",
            "demotion_reason": "saas_no_subscription_evidence",
            "deferred_revenue_ratio": drr,
            "has_manual_ARR": cond_manual_arr,
            "rationale": rationale,
        }),
        "bm_validators_json": decision.get("bm_validators_json", "[]"),
        "bm_llm_rationale": rationale,
        "bm_demotion_reason": "saas_no_subscription_evidence",
    }


def classify_business_model(ticker: str, metrics: dict) -> dict:
    """Hybrid: manual override (highest priority) → balance-sheet validators →
    LLM tiebreaker (lowest priority). Overrides never lose to the LLM.

    1. If `ticker` is in MANUAL_BM_OVERRIDES, assign that category and stop.
       Use overrides for tickers where the validators can't be right
       (e.g. AV doesn't expose PPE, so hyperscale validator abstains).
    2. Run validators; filter out abstaining ones.
    3. Exactly 1 passes -> assign directly (HIGH confidence).
    4. 0 pass or 2+ pass -> LLM breaks the tie among the non-abstaining set.
    """
    # Fix #6: short-circuit on manual override before any validator/LLM work.
    if ticker in MANUAL_BM_OVERRIDES:
        cat = MANUAL_BM_OVERRIDES[ticker]
        log.info(f"[{ticker}] BM override -> {cat}")
        return {"bm_category": cat, "bm_method": "override", "bm_confidence": "HIGH",
                "bm_decision_trace": json.dumps({"override": cat}),
                "bm_validators_json": json.dumps([]),
                "bm_llm_rationale": None}

    validators = [_validate_saas, _validate_hyperscale, _validate_semi_hardware,
                  _validate_consumer_internet, _validate_deep_tech]
    results = [v(metrics) for v in validators]
    # Abstaining validators don't count toward pass/fail (fix #1 enables this).
    non_abstain = [r for r in results if not r.get("abstain")]
    passed = [r for r in non_abstain if r["passed"]]

    validators_json = json.dumps(results, default=str)

    def _gated(decision: dict) -> dict:
        """Apply SaaS subscription gate to any saas-classified decision. The
        gate only ever demotes — never promotes — so it's safe to wrap every
        non-override return path."""
        return _apply_saas_gate(ticker, decision, metrics, results)

    if len(passed) == 1:
        cat = passed[0]["category"]
        log.info(f"[{ticker}] BM validator: {cat} (sole pass, conf={passed[0]['confidence']:.2f})")
        return _gated({"bm_category": cat, "bm_method": "validator", "bm_confidence": "HIGH",
                       "bm_decision_trace": json.dumps(passed[0]), "bm_validators_json": validators_json,
                       "bm_llm_rationale": None})

    elif len(passed) == 0:
        log.info(f"[{ticker}] BM: 0 validators passed (of {len(non_abstain)} non-abstain) — calling Gemini.")
        llm = _call_gemini_tiebreaker(ticker, non_abstain or results, metrics)
        return _gated({"bm_category": llm["category"], "bm_method": "llm_tiebreaker", "bm_confidence": "LOW",
                       "bm_decision_trace": json.dumps({"contenders": "none", "llm": llm}),
                       "bm_validators_json": validators_json, "bm_llm_rationale": llm["rationale"]})

    else:
        labels = [p["category"] for p in passed]
        log.info(f"[{ticker}] BM: {len(passed)} validators passed ({labels}) — calling Gemini tiebreaker.")
        llm = _call_gemini_tiebreaker(ticker, passed, metrics)
        return _gated({"bm_category": llm["category"], "bm_method": "llm_tiebreaker", "bm_confidence": "LOW",
                       "bm_decision_trace": json.dumps({"contenders": labels, "llm": llm}),
                       "bm_validators_json": validators_json, "bm_llm_rationale": llm["rationale"]})


# ==========================================
# 6. LEVEL 3 — VALUATION MATRIX ENGINE
# ==========================================

def _read_manual_metric(manual: dict, key: str):
    """Look up a metric in a per-ticker manual_metrics.json dict, handling
    BOTH shapes the file now contains side-by-side:

      flat       :  {"ARR": 3200000000}
                    → returns 3200000000 (raw dollars or raw count)

      structured :  {"arr": {"value": 26.06, "unit": "billions_usd",
                             "confidence": 0.95, "disclosed": true, ...}}
                    → returns 26060000000 (normalized to raw units via the
                                            unit tag, matches flat shape)

    Tries the key as-given, then lowercase, then uppercase — the legacy
    human-edited entries use uppercase (ARR/MAU/TCV) but metrics_extractor.py
    writes lowercase (arr/mau/tcv). Falls through to None if the key is
    absent OR if the structured value is null (not-disclosed).

    Returns None for any unparseable input — never raises into the caller.
    """
    raw = manual.get(key)
    if raw is None:
        raw = manual.get(key.lower())
    if raw is None:
        raw = manual.get(key.upper())
    if raw is None:
        return None

    if isinstance(raw, dict):
        v = raw.get("value")
        if v is None:
            return None
        try:
            v = float(v)
        except (TypeError, ValueError):
            return None
        unit = (raw.get("unit") or "").lower()
        if unit == "billions_usd":
            return v * 1e9
        if unit == "millions_usd":
            return v * 1e6
        if unit == "count" and key.lower() in {"mau", "dau", "wau"}:
            # User-count metrics come back from the extractor "in millions"
            # per its prompt — normalize to raw count to match the legacy
            # flat-shape entries (e.g. old NVDA MAU=3070000000).
            return v * 1e6
        # percent, usd, ratio, raw-count (e.g. cash_runway_months) → as-is
        return v

    # Flat numeric scalar — already in raw units
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _load_manual_metrics(ticker: str) -> dict:
    """Load manual metrics for a ticker from the JSON file."""
    if not os.path.exists(MANUAL_METRICS_PATH):
        return {}
    try:
        with open(MANUAL_METRICS_PATH, "r") as f:
            data = json.load(f)
        return data.get(ticker, {})
    except (json.JSONDecodeError, IOError):
        return {}


def _safe_div(numerator, denominator, rnd=2):
    """Safe division returning None on zero/None."""
    if numerator is None or denominator is None or denominator == 0:
        return None
    return round(numerator / denominator, rnd)


# The 5×4 matrix: maps (bm_category, fh_stage) → valuation method label
# Matrix version — written into every valuation row (valuation_json) so
# backtest results are traceable to the matrix that produced them.
# v2-2026-08 changes vs v1: hyperscale-2 → EV/NTM Rev (CapEx EV/EBIT + P/S
# removed at S2); deep_tech-1 → EV/NTM Rev (renamed from P/S) with EV/Cash
# pre-revenue fallback; consumer_internet-4 P/E primary; S4 FCF-yield ranges
# retuned; semi_hardware-3 R40 dropped from fair ranges; PEG value-trap flag.
MATRIX_VERSION = "v2-2026-08"

VALUATION_MATRIX = {
    ("saas", 1):             "EV/NTM Revenue",
    ("saas", 2):             "EV/NTM ARR",
    ("saas", 3):             "EV/FCF and PEG",
    ("saas", 4):             "FCF yield and EV/FCF and P/E and PEG",
    ("hyperscale", 1):       "EV/Revenue and CapEx/TTM Revenue",
    ("hyperscale", 2):       "EV/NTM Revenue",
    ("hyperscale", 3):       "CapEx-adjusted EV/EBIT and PEG",
    ("hyperscale", 4):       "FCF yield and PEG",
    ("semi_hardware", 1):    "EV/Revenue and CapEx/TTM Revenue",
    ("semi_hardware", 2):    "EV/NTM Revenue",
    ("semi_hardware", 3):    "Cycle-adjusted P/E",
    ("semi_hardware", 4):    "FCF yield and P/E and PEG",
    ("consumer_internet", 1): "P/S and MAU",
    ("consumer_internet", 2): "EV/NTM Revenue",
    ("consumer_internet", 3): "EV/EBITDA and PEG",
    ("consumer_internet", 4): "P/E and EV/EBITDA and PEG",
    ("deep_tech", 1):        "EV/NTM Revenue",
    ("deep_tech", 2):        "EV/NTM Revenue",
    ("deep_tech", 3):        "EV/Gross Profit",
    ("deep_tech", 4):        "FCF yield and PEG",
}


def compute_valuation(conn: sqlite3.Connection, ticker: str, matrix_cell: str) -> bool:
    """
    Computes all valuation multiples, routes to the correct matrix cell method,
    and stores results in the valuations table.
    """
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM computed_metrics WHERE ticker = ?
        ORDER BY as_of_date DESC LIMIT 1
    """, (ticker,))
    row = cursor.fetchone()
    if not row:
        log.warning(f"[{ticker}] No computed metrics — cannot value.")
        return False

    cols = [d[0] for d in cursor.description]
    m = dict(zip(cols, row))

    # Load manual metrics
    manual = _load_manual_metrics(ticker)

    # Parse matrix cell
    parts = matrix_cell.rsplit("-", 1)
    bm_cat = parts[0] if len(parts) == 2 else "unknown"
    fh_stage = int(parts[1]) if len(parts) == 2 else 0

    # --- Compute all multiples ---
    ev = m.get("enterprise_value")
    mcap = m.get("market_cap")
    rev = m.get("revenue_ltm")
    gp = m.get("gross_profit_ltm")
    ebit = m.get("ebit_ltm")
    ebitda = m.get("ebitda_ltm")
    fcf = m.get("fcf_ltm")
    sbc = m.get("sbc_ltm") or 0
    capex = m.get("capex_ltm") or 0
    eps = m.get("eps_ltm")
    ni = m.get("net_income_ltm")
    ni_prior = m.get("net_income_prior")
    rev_growth = m.get("revenue_growth_yoy")

    # Core multiples
    ev_revenue = _safe_div(ev, rev)
    ps_ratio = _safe_div(mcap, rev)
    pe_ratio = _safe_div(mcap, ni) if ni and ni > 0 else None
    adj_fcf = (m.get("ocf_ltm") or 0) - capex - sbc
    ev_fcf = _safe_div(ev, adj_fcf) if adj_fcf and adj_fcf > 0 else None
    fcf_yield = round(adj_fcf / mcap * 100, 2) if mcap and adj_fcf else None
    ev_ebitda = _safe_div(ev, ebitda) if ebitda and ebitda > 0 else None
    ev_gross_profit = _safe_div(ev, gp) if gp and gp > 0 else None
    capex_revenue = _safe_div(capex, rev, 4)
    # EV/EBIT — used as the P/E fallback when net_income_quality_flag fires.
    ev_ebit = _safe_div(ev, ebit) if ebit and ebit > 0 else None

    # NTM Revenue = TTM × (1 + YoY growth)
    ntm_revenue = rev * (1 + (rev_growth or 0) / 100) if rev else None
    ev_ntm_revenue = _safe_div(ev, ntm_revenue)

    # PEG ratio (use average of last 2 years earnings growth)
    peg_ratio = None
    if pe_ratio and ni and ni_prior and ni_prior > 0:
        earnings_growth = ((ni / ni_prior) - 1) * 100  # 2yr CAGR approximation
        if earnings_growth > 0:
            peg_ratio = round(pe_ratio / earnings_growth, 2)

    # CapEx-adjusted EV/EBIT
    maint_capex = capex * MAINTENANCE_CAPEX_RATIO
    adj_ebit = (ebit or 0) - maint_capex
    capex_adj_ev_ebit = _safe_div(ev, adj_ebit) if adj_ebit and adj_ebit > 0 else None

    # Rule of 40
    fcf_margin = m.get("fcf_margin_adj")
    rule_of_40 = round((rev_growth or 0) + (fcf_margin or 0), 2)

    # Cycle-adjusted P/E (average EPS over last N years from quarterly data)
    cycle_adj_pe = None
    try:
        inc_series = _load_quarterly_series(conn, ticker, "INCOME_STATEMENT")
        n_quarters = CYCLE_ADJ_YEARS * 4
        if len(inc_series) >= n_quarters and m.get("shares_outstanding"):
            total_ni = sum(_safe_float(q.get("netIncome")) for q in inc_series[:n_quarters])
            avg_annual_ni = total_ni / CYCLE_ADJ_YEARS
            avg_eps = avg_annual_ni / m["shares_outstanding"]
            if avg_eps and avg_eps > 0:
                price = mcap / m["shares_outstanding"] if mcap and m["shares_outstanding"] else None
                cycle_adj_pe = _safe_div(price, avg_eps)
    except Exception:
        pass

    # Manual metrics — _read_manual_metric handles both shapes (legacy flat
    # values and structured metrics_extractor entries) and normalises to raw
    # units (dollars or count). Tries uppercase, lowercase, and as-given keys.
    arr = _read_manual_metric(manual, "ARR")
    mau = _read_manual_metric(manual, "MAU")
    tcv = _read_manual_metric(manual, "TCV")
    nrr = _read_manual_metric(manual, "NRR")  # not used in any matrix cell yet, but available
    ntm_arr = arr * (1 + (rev_growth or 0) / 100) if arr else None
    ev_ntm_arr = _safe_div(ev, ntm_arr)

    # --- Route to matrix cell method ---
    method_key = (bm_cat, fh_stage)
    primary_method = VALUATION_MATRIX.get(method_key, "EV/Revenue (fallback)")

    # Build valuation summary for this cell
    cell_multiples = {}

    # deep_tech-1 pre-revenue fallback: EV/NTM Rev divides by (near-)zero for
    # pre-revenue names. Switch to EV/Cash + cash runway as the primary signal.
    # Metric label written as "EV/Cash" so verdict/source lookups key correctly.
    if method_key == ("deep_tech", 1) and (rev is None or rev <= 0):
        primary_method = "EV/Cash and Cash Runway"
        ev_to_cash = _safe_div(ev, m.get("cash_and_equiv")) if m.get("cash_and_equiv") else None
        cell_multiples["ev_to_cash"] = ev_to_cash
        cell_multiples["metric_label"] = "EV/Cash"
        cell_multiples["cash_runway_months"] = m.get("cash_runway_months")
    if "EV/NTM Revenue" in primary_method:
        cell_multiples["ev_ntm_revenue"] = ev_ntm_revenue
    if "EV/NTM ARR" in primary_method:
        cell_multiples["ev_ntm_arr"] = ev_ntm_arr
        cell_multiples["arr"] = arr
        cell_multiples["ntm_arr"] = ntm_arr
    if "EV/FCF" in primary_method:
        cell_multiples["ev_fcf"] = ev_fcf
    if "FCF yield" in primary_method:
        cell_multiples["fcf_yield"] = fcf_yield
    if "P/E" in primary_method:
        # Fix #7: when reported NI is distorted (warrant gains, big one-time
        # tax benefits), swap P/E for EV/EBIT so the multiple reflects
        # operating performance instead of accounting noise.
        if m.get("net_income_quality_flag"):
            cell_multiples["pe_ratio"] = None
            cell_multiples["ev_ebit"] = ev_ebit
            cell_multiples["_pe_swapped_to_ev_ebit"] = True
        else:
            cell_multiples["pe_ratio"] = pe_ratio
    if "PEG" in primary_method:
        # PEG also derives from P/E; suppress alongside the swap.
        cell_multiples["peg_ratio"] = None if m.get("net_income_quality_flag") else peg_ratio
    if "P/S" in primary_method:
        cell_multiples["ps_ratio"] = ps_ratio
    if "EV/Revenue" in primary_method and "NTM" not in primary_method:
        cell_multiples["ev_revenue"] = ev_revenue
    if "CapEx/TTM" in primary_method:
        cell_multiples["capex_revenue"] = capex_revenue
    if "EV/(EBIT-CapEx)" in primary_method or "CapEx-adjusted" in primary_method:
        cell_multiples["capex_adj_ev_ebit"] = capex_adj_ev_ebit
    if "EV/EBITDA" in primary_method:
        cell_multiples["ev_ebitda"] = ev_ebitda
    if "Cycle-adjusted" in primary_method:
        cell_multiples["cycle_adj_pe"] = cycle_adj_pe
    if "MAU" in primary_method:
        cell_multiples["mau"] = mau
    if "TCV" in primary_method:
        cell_multiples["tcv"] = tcv
    if "EV/Gross Profit" in primary_method:
        cell_multiples["ev_gross_profit"] = ev_gross_profit

    # Always include Rule of 40 as a reference
    cell_multiples["rule_of_40"] = rule_of_40

    # Matrix version stamp — every valuation row is traceable to the matrix
    # revision that produced it (backtest reproducibility).
    cell_multiples["matrix_version"] = MATRIX_VERSION

    # ---- PEG value-trap flag --------------------------------------------
    # Where PEG is a cell metric: a low PEG (<0.8) combined with DECELERATING
    # revenue growth (current-quarter LTM YoY below the prior quarter's) often
    # signals a value trap, not undervaluation. Mirrors the RPO persistence
    # idea: direction matters, not the level alone. The PEG value itself is
    # never suppressed — both are surfaced.
    if "PEG" in primary_method and peg_ratio is not None and peg_ratio < 0.8:
        try:
            _inc = _load_quarterly_series(conn, ticker, "INCOME_STATEMENT")
            if len(_inc) >= 9:
                _ltm_now    = sum(_safe_float(q.get("totalRevenue")) for q in _inc[0:4])
                _ltm_prior  = sum(_safe_float(q.get("totalRevenue")) for q in _inc[4:8])
                _ltm_now_q1   = sum(_safe_float(q.get("totalRevenue")) for q in _inc[1:5])
                _ltm_prior_q1 = sum(_safe_float(q.get("totalRevenue")) for q in _inc[5:9])
                if _ltm_prior > 0 and _ltm_prior_q1 > 0:
                    _g_now  = (_ltm_now / _ltm_prior - 1) * 100
                    _g_prev = (_ltm_now_q1 / _ltm_prior_q1 - 1) * 100
                    if _g_now < _g_prev:
                        cell_multiples["FLAG_peg_value_trap"] = (
                            "Low PEG with decelerating growth — may indicate a "
                            "value trap rather than undervaluation.")
                        log.info(f"[{ticker}] FLAG_peg_value_trap "
                                 f"(PEG={peg_ratio}, growth {_g_prev:.1f}% → {_g_now:.1f}%)")
        except Exception as e:
            log.warning(f"[{ticker}] PEG value-trap check failed: {e}")

    # ---- RPO directional qualifier --------------------------------------
    # Apply ONLY to hyperscale S2/S3 (primary qualifier) and saas S2/S3
    # (secondary qualifier). RPO is meaningless for semi/consumer/deep-tech;
    # do not wire it into those cells.
    #
    # Three-state directional read on rpo_revenue_spread (in percentage points
    # = rpo_growth_yoy - revenue_growth_yoy):
    #
    #   spread > SPREAD_POS_PP                  → forward_demand_ahead
    #   spread < 0 (current) AND prior < 0      → forward_demand_decelerating
    #   anything else                           → neutral
    #
    # The negative-decelerating flag requires TWO consecutive quarters below
    # zero because single-quarter RPO is noisy (enterprise deals are lumpy).
    # Positive flag fires on a single quarter — a strong forward signal even
    # once is informative. The qualifier never replaces the cell's primary
    # multiple; it only adds an interpretive flag in the valuation_json.
    SPREAD_POS_PP = 5.0
    RPO_QUALIFIER_CELLS = {
        ("hyperscale", 2): "primary",
        ("hyperscale", 3): "primary",
        ("saas",       2): "secondary",
        ("saas",       3): "secondary",
    }
    qualifier_role = RPO_QUALIFIER_CELLS.get((bm_cat, fh_stage))
    if qualifier_role:
        spread = m.get("rpo_revenue_spread")
        # Look up prior quarter's spread for this ticker (must be < 0 too).
        prior_spread = None
        try:
            prior = cursor.execute("""
                SELECT rpo_revenue_spread FROM computed_metrics
                WHERE ticker = ? AND as_of_date < ?
                ORDER BY as_of_date DESC LIMIT 1
            """, (ticker, m["as_of_date"])).fetchone()
            if prior:
                prior_spread = prior[0]
        except Exception:
            pass

        if spread is None:
            qualifier_state = None  # Cannot compute — fall back to primary
            # Distinguish: (a) RPO not disclosed at all vs (b) RPO known but
            # prior-year missing so growth rate can't be derived.
            rpo_known = m.get("rpo") is not None
            qualifier_note = "rpo_prior_year_missing" if rpo_known else "rpo_not_disclosed"
        elif spread > SPREAD_POS_PP:
            qualifier_state = "forward_demand_ahead"
            qualifier_note = "primary multiple may understate (RPO outpacing revenue)"
        elif spread < 0 and prior_spread is not None and prior_spread < 0:
            qualifier_state = "forward_demand_decelerating"
            qualifier_note = "primary multiple likely overstates (RPO trailing revenue for 2+ quarters)"
        else:
            qualifier_state = "neutral"
            if spread < 0:
                qualifier_note = "single-quarter negative spread; not yet persistent"
            else:
                qualifier_note = "spread roughly flat"

        cell_multiples["rpo_qualifier"]       = qualifier_state
        cell_multiples["rpo_qualifier_role"]  = qualifier_role
        cell_multiples["rpo_qualifier_note"]  = qualifier_note
        cell_multiples["rpo_spread_pp"]       = spread
        cell_multiples["rpo_spread_prior_pp"] = prior_spread

    computed_at = datetime.utcnow().isoformat()

    cursor.execute("""
        INSERT OR REPLACE INTO valuations (
            ticker, as_of_date, matrix_cell,
            ev_revenue, ps_ratio, pe_ratio, ev_fcf, fcf_yield, ev_ebitda,
            peg_ratio, capex_adj_ev_ebit, ev_gross_profit, capex_revenue,
            ntm_revenue, rule_of_40, cycle_adj_pe, ev_ntm_revenue,
            arr, ntm_arr, ev_ntm_arr, mau, tcv,
            primary_method, valuation_json, computed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        ticker, m["as_of_date"], matrix_cell,
        ev_revenue, ps_ratio, pe_ratio, ev_fcf, fcf_yield, ev_ebitda,
        peg_ratio, capex_adj_ev_ebit, ev_gross_profit, capex_revenue,
        ntm_revenue, rule_of_40, cycle_adj_pe, ev_ntm_revenue,
        arr, ntm_arr, ev_ntm_arr, mau, tcv,
        primary_method, json.dumps(cell_multiples, default=str), computed_at,
    ))
    conn.commit()

    log.info(f"[{ticker}] Valuation → {primary_method}  multiples={cell_multiples}")
    return True


def run_classification(conn: sqlite3.Connection, ticker: str) -> bool:
    """
    Loads the latest computed_metrics row for ticker,
    runs both classifiers, and persists to classifications table.
    """
    if ticker in EXCLUDED_TICKERS_NON_USD:
        log.warning(f"[{ticker}] excluded (non-USD reporting currency) — skipping classification.")
        return False

    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM computed_metrics WHERE ticker = ?
        ORDER BY as_of_date DESC LIMIT 1
    """, (ticker,))
    row = cursor.fetchone()
    if not row:
        log.warning(f"[{ticker}] No computed metrics found — run compute_and_store_metrics first.")
        return False

    cols0 = [d[0] for d in cursor.description]
    metrics0 = dict(zip(cols0, row))
    if metrics0.get("reported_currency") and metrics0["reported_currency"] != "USD":
        log.warning(f"[{ticker}] excluded (reported_currency={metrics0['reported_currency']}).")
        EXCLUDED_TICKERS_NON_USD.add(ticker)
        return False

    cols   = [d[0] for d in cursor.description]
    metrics = dict(zip(cols, row))

    fh = classify_financial_health(metrics)
    bm = classify_business_model(ticker, metrics)

    matrix_cell = f"{bm['bm_category']}-{fh['fh_stage']}"
    classified_at = datetime.utcnow().isoformat()

    # Stage-change detection: read the PREVIOUS stage before we overwrite it.
    _prev_row = cursor.execute(
        "SELECT fh_stage FROM classifications WHERE ticker = ? ORDER BY as_of_date DESC LIMIT 1",
        (ticker,)
    ).fetchone()
    _prev_stage = _prev_row[0] if _prev_row else None

    cursor.execute("""
        INSERT OR REPLACE INTO classifications (
            ticker, as_of_date,
            fh_fcf_stage, fh_gm_stage, fh_runway_stage, fh_oplev_stage,
            fh_sbc_stage, fh_de_stage, fh_roic_stage, fh_weighted_score, fh_stage,
            fh_fcf_hard_cap_applied,
            bm_category, bm_method, bm_confidence, bm_decision_trace,
            bm_validators_json, bm_llm_rationale, bm_demotion_reason,
            matrix_cell, classified_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        ticker, metrics["as_of_date"],
        fh["fh_fcf_stage"], fh["fh_gm_stage"], fh["fh_runway_stage"],
        fh["fh_oplev_stage"], fh["fh_sbc_stage"], fh["fh_de_stage"],
        fh["fh_roic_stage"], fh["fh_weighted_score"], fh["fh_stage"],
        fh["fh_fcf_hard_cap_applied"],
        bm["bm_category"], bm["bm_method"], bm["bm_confidence"],
        bm["bm_decision_trace"], bm["bm_validators_json"], bm["bm_llm_rationale"],
        bm.get("bm_demotion_reason"),
        matrix_cell, classified_at,
    ))
    conn.commit()
    log.info(
        f"[{ticker}] Classified → {matrix_cell}  "
        f"(FH score={fh['fh_weighted_score']:.3f}, "
        f"BM method={bm['bm_method']}, cap={'yes' if fh['fh_fcf_hard_cap_applied'] else 'no'})"
    )

    # Step 3: Level 3 — compute valuation multiples for this matrix cell
    compute_valuation(conn, ticker, matrix_cell)

    # Step 4: refresh historical FH-stage trajectory (last 8 quarters).
    # Reuses cached fundamentals; zero AV credits. Wrapped so a failure
    # here never breaks the classification flow.
    try:
        n_written = backfill_fh_history(conn, ticker, n_quarters=8)
        log.info(f"[{ticker}] FH trajectory refreshed: {n_written} quarter(s) written.")
    except Exception as e:
        log.warning(f"[{ticker}] FH history refresh failed: {e}")

    # Step 5: stage-change detection → Supabase stage_change_log + alert emails.
    # Powers the app's "Recently moved" section and watchlist alerts.
    # Fully wrapped: an offline run or unconfigured Supabase never breaks the pipeline.
    if _prev_stage is not None and _prev_stage != fh["fh_stage"]:
        log.info(f"[{ticker}] STAGE CHANGE detected: {_prev_stage} → {fh['fh_stage']}")
        change = {
            "ticker": ticker,
            "previous_stage": _prev_stage,
            "new_stage": fh["fh_stage"],
            "matrix_cell": matrix_cell,
        }
        STAGE_CHANGES_THIS_RUN.append(change)
        try:
            _sbc = _get_supabase_client_module()
            if _sbc is not None:
                _sbc.log_stage_change(ticker, _prev_stage, fh["fh_stage"], matrix_cell)
                _sbc.send_stage_change_alerts([change])
        except Exception as e:
            log.warning(f"[{ticker}] Supabase stage-change hook failed: {e}")

    return True


# ==========================================
# 6. REPORTING HELPER
# ==========================================
def print_classification_summary(conn: sqlite3.Connection) -> None:
    """Prints a summary table of all latest classifications to stdout."""
    df = pd.read_sql_query("""
        SELECT
            c.ticker,
            c.as_of_date,
            c.bm_category,
            c.bm_method,
            c.fh_stage,
            c.matrix_cell,
            c.fh_weighted_score,
            c.fh_fcf_hard_cap_applied,
            m.fcf_margin_adj,
            m.gross_margin,
            m.revenue_growth_yoy,
            m.cash_runway_months,
            m.roic
        FROM classifications c
        JOIN computed_metrics m ON c.ticker = m.ticker AND c.as_of_date = m.as_of_date
        ORDER BY c.bm_category, c.fh_stage DESC, c.ticker
    """, conn)

    if df.empty:
        print("No classifications found.")
        return

    pd.set_option("display.max_rows",    200)
    pd.set_option("display.max_columns",  20)
    pd.set_option("display.width",       200)
    pd.set_option("display.float_format", lambda x: f"{x:.1f}" if x is not None else "—")

    print("\n" + "=" * 120)
    print("VALOURA — CLASSIFICATION SUMMARY (v2: stage-varying weights + balance sheet validators)")
    print("=" * 120)
    print(df.to_string(index=False))
    print("=" * 120)

    # Matrix distribution
    print("\nMatrix cell distribution:")
    print(df["matrix_cell"].value_counts().to_string())
    print(f"\nBM method breakdown: {df['bm_method'].value_counts().to_dict()}")


# ==========================================
# MAIN EXECUTION ORCHESTRATOR
# ==========================================
def main():
    if ALPHA_VANTAGE_API_KEY == "demo":
        log.warning(
            "Using demo AV key — limited to a handful of tickers. "
            "Set ALPHA_VANTAGE_API_KEY env variable for full run."
        )

    conn = sqlite3.connect(DB_NAME)
    setup_database(conn)

    for i, ticker in enumerate(TICKERS):
        log.info(f"\n{'─'*50}\nProcessing {ticker} ({i+1}/{len(TICKERS)})\n{'─'*50}")

        # Step 1: Price data (yfinance — no rate limit concern)
        fetch_and_store_price_data(conn, ticker)

        # Step 2: Check if fundamentals already stored — skip AV calls if so
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COUNT(DISTINCT statement_type) FROM fundamentals WHERE ticker = ?
        """, (ticker,))
        stored_types = cursor.fetchone()[0]

        if stored_types >= 3:
            log.info(f"[{ticker}] Fundamentals already stored ({stored_types} types) — skipping AV fetch.")
        else:
            # Fetch from Alpha Vantage only for missing tickers
            for statement in ["INCOME_STATEMENT", "CASH_FLOW", "BALANCE_SHEET"]:
                count = fetch_and_store_fundamentals(conn, ticker, statement)
                if count == 0:
                    log.warning(f"[{ticker}] {statement} returned 0 records — check API key/limit.")
                time.sleep(AV_SLEEP)  # respect rate limit precisely

        # Step 3: Compute LTM metrics from raw quarterly data
        ok = compute_and_store_metrics(conn, ticker)
        if not ok:
            log.warning(f"[{ticker}] Metrics computation failed — classification skipped.")
            continue

        # Step 4: Run classification engine
        run_classification(conn, ticker)

    # Step 5: Print summary
    print_classification_summary(conn)

    conn.close()
    log.info("Pipeline complete. Database ready for backtesting.")


if __name__ == "__main__":
    main()
