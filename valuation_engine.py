"""The valuation matrix and THE verdict function. One implementation, one truth.

Before this module existed the rule lived in two places:

  * `Valuora_Previous_UI/app.py`  — FAIR_RANGES_FULL + derive_verdict(), used at
    display time, keyed on whatever `data_pipeline1` happened to write into
    `valuations.valuation_json`.
  * `build_panel.py`             — PANEL_MATRIX + verdict_for(), which computed
    each cell's documented primary metric directly because the app's key
    mapping was broken for 7 of 20 cells.

Two implementations of one rule always drift, and these did: integrity test R5
found the app and the panel returning different verdicts for the same ticker,
quarter and cell (ABNB saas-3: "undervalued" in the panel, "fair" in the app),
plus 7 cells where the app rendered nothing at all. Both callers now import from
here and neither carries its own copy.

THE KEY-MAPPING BUG THIS FIXES
------------------------------
`FAIR_RANGES_FULL` pointed every "EV/NTM Rev" cell at the key `ev_ntm_arr`, but
`data_pipeline1` writes that key ONLY when the routed method is "EV/NTM ARR"
(30 rows of 400 sampled, against 114 for `ev_ntm_revenue`). So hyperscale-1,
saas-1, semi_hardware-1, semi_hardware-2, consumer_internet-1,
consumer_internet-2 and deep_tech-2 looked up a key that was never written and
silently returned None — 873 panel rows and 7 of 20 cells with no verdict.

Fixed structurally rather than by renaming one key: every metric declares an
ORDERED list of acceptable keys, and lookup takes the first one present. A
revenue multiple accepts `ev_ntm_revenue` then `ev_ntm_arr`; an ARR multiple
accepts `ev_ntm_arr` then `ev_ntm_revenue` (the documented ARR-substitute
behaviour the panel already relied on for saas-2). Key strings are normalised
before matching, so `EV/NTM_ARR`, `ev ntm arr` and `ev_ntm_arr` all resolve.
"""

MATRIX_VERSION = "v2-2026-08"

# Ordered candidate keys per metric. First present wins.
METRIC_KEYS = {
    "EV/NTM Rev":    ("ev_ntm_revenue", "ev_ntm_rev", "ev_ntm_arr"),
    "EV/NTM ARR":    ("ev_ntm_arr", "ev_ntm_revenue", "ev_ntm_rev"),
    "EV/FCF":        ("ev_fcf",),
    "FCF Yield":     ("fcf_yield",),
    "CapEx EV/EBIT": ("capex_adj_ev_ebit",),
    "Cycle P/E":     ("cycle_adj_pe",),
    "EV/EBITDA":     ("ev_ebitda",),
    "P/E":           ("pe_ratio",),
    "EV/GP":         ("ev_gross_profit",),
    "EV/Cash":       ("ev_to_cash", "ev_cash"),
    "PEG":           ("peg_ratio",),
    "Rule of 40":    ("rule_of_40",),
}

# cell -> ordered metric list; FIRST ENTRY IS THE PRIMARY and drives the verdict.
#   (display_name, lo, hi, unit, kind)
#   kind: "multiple" lower is cheaper · "yield"/"score" higher is better
CELL_MATRIX = {
    "hyperscale-1":        [("EV/NTM Rev", 6, 15, "x", "multiple"),
                            ("Rule of 40", 0, 20, "%", "score")],
    "hyperscale-2":        [("EV/NTM Rev", 3, 12, "x", "multiple")],
    "hyperscale-3":        [("CapEx EV/EBIT", 20, 35, "x", "multiple"),
                            ("PEG", 0.5, 1.5, "x", "multiple"),
                            ("Rule of 40", 25, 45, "%", "score")],
    "hyperscale-4":        [("FCF Yield", 2.0, 4.0, "%", "yield"),
                            ("PEG", 0.5, 1.5, "x", "multiple"),
                            ("Rule of 40", 30, 50, "%", "score")],
    "saas-1":              [("EV/NTM Rev", 8, 20, "x", "multiple"),
                            ("Rule of 40", 0, 20, "%", "score")],
    "saas-2":              [("EV/NTM ARR", 8, 18, "x", "multiple"),
                            ("Rule of 40", 15, 35, "%", "score")],
    "saas-3":              [("EV/FCF", 25, 45, "x", "multiple"),
                            ("PEG", 0.8, 2.0, "x", "multiple"),
                            ("Rule of 40", 30, 50, "%", "score")],
    "saas-4":              [("FCF Yield", 2.0, 3.5, "%", "yield"),
                            ("EV/FCF", 20, 35, "x", "multiple"),
                            ("PEG", 0.5, 1.5, "x", "multiple"),
                            ("Rule of 40", 40, 60, "%", "score")],
    "semi_hardware-1":     [("EV/NTM Rev", 0.5, 2.0, "x", "multiple")],
    "semi_hardware-2":     [("EV/NTM Rev", 1.5, 4.0, "x", "multiple")],
    "semi_hardware-3":     [("Cycle P/E", 20, 35, "x", "multiple"),
                            ("PEG", 0.5, 1.5, "x", "multiple")],
    "semi_hardware-4":     [("FCF Yield", 2.5, 4.5, "%", "yield"),
                            ("PEG", 0.5, 1.2, "x", "multiple")],
    "consumer_internet-1": [("EV/NTM Rev", 1, 5, "x", "multiple")],
    "consumer_internet-2": [("EV/NTM Rev", 4, 10, "x", "multiple"),
                            ("Rule of 40", 10, 30, "%", "score")],
    "consumer_internet-3": [("EV/EBITDA", 18, 30, "x", "multiple"),
                            ("PEG", 1.0, 2.5, "x", "multiple"),
                            ("Rule of 40", 20, 40, "%", "score")],
    "consumer_internet-4": [("P/E", 15, 25, "x", "multiple"),
                            ("EV/EBITDA", 12, 18, "x", "multiple"),
                            ("PEG", 0.8, 1.5, "x", "multiple")],
    "deep_tech-1":         [("EV/NTM Rev", 20, 60, "x", "multiple")],
    "deep_tech-2":         [("EV/NTM Rev", 5, 12, "x", "multiple")],
    "deep_tech-3":         [("EV/GP", 15, 30, "x", "multiple"),
                            ("Rule of 40", 10, 30, "%", "score")],
    "deep_tech-4":         [("FCF Yield", 2.0, 4.0, "%", "yield"),
                            ("PEG", 0.5, 1.5, "x", "multiple")],
}

# deep_tech-1 pre-revenue fallback: EV/NTM Rev divides by ~zero when there is no
# revenue, so the cell switches lens (documented in claude.md.txt).
DEEP_TECH1_FALLBACK = ("EV/Cash", 1.0, 5.0, "x", "multiple")

VERDICTS = ("undervalued", "fair", "overvalued")

# ---------------------------------------------------------------------------
# CELLS WHERE THE CHEAP/EXPENSIVE VERDICT IS SUPPRESSED
#
# Not disabled for caution — disabled because the evidence says the reading is
# actively misleading. In these cohorts a LOW multiple has been associated with
# WORSE subsequent returns, robustly and with the sign against us:
#
#   saas-2               -15.0pp at terciles, -23.0pp at deciles, p=0.000 against
#                        a 500-permutation placebo null, and STRENGTHENING since
#                        2019 (-7.3pp pre-2019 -> -18.2pp post-2019).
#   consumer_internet-2  -10.3pp at terciles, negative at every split width.
#
# Showing "undervalued" here would tell a user the opposite of what the data
# supports. The signal is NOT inverted — inverting would be fitting to a single
# result, and one robust negative is not a tradable positive. The multiple is
# still displayed; only the judgement is withheld.
#
# To lift a suppression: re-run analyze_bands.py + cve_checks.py and show the
# effect no longer holds. Do not lift it because it looks untidy.
# ---------------------------------------------------------------------------
SUPPRESSED_CELLS = {
    "saas-2": ("In this cohort a low multiple has historically signalled "
               "decelerating growth rather than value: the cheapest names "
               "underperformed the dearest by 15-23 percentage points over the "
               "following year, and the effect has strengthened since 2019. "
               "The multiple is shown; the cheap/expensive judgement is withheld."),
    "consumer_internet-2": (
        "In this cohort a low multiple has historically signalled decelerating "
        "growth rather than value: the cheapest names underperformed the "
        "dearest by about 10 percentage points over the following year. "
        "The multiple is shown; the cheap/expensive judgement is withheld."),
}

# Business models the engine has no calibrated valuation model for. Membership
# is a statement about the COMPANY, not about its price: these names were
# falling into `deep_tech` because it acts as the residual bucket, so users were
# told Jack Henry and Leidos are deep-tech companies. They are labelled honestly
# and carry no verdict, because no fair range has been calibrated for them.
NO_MODEL_CATEGORIES = {
    "it_services": ("Labour-based IT services and vertical software. The engine "
                    "has five business models and none of them fit this one, so "
                    "no fair range has been calibrated. Previously these names "
                    "were absorbed into deep_tech, which described them "
                    "incorrectly."),
}

CATEGORY_LABELS = {
    "hyperscale": "Hyperscale",
    "saas": "Pure SaaS",
    "semi_hardware": "Semi / Hardware",
    "consumer_internet": "Consumer Internet",
    # RESIDUAL BUCKET — renamed 2026-08-16. The rule for this category is "no
    # dominant revenue stream >=40%, or pre-revenue", which makes it the place
    # everything unclaimed lands: 48 tickers across 14 AV industries, mostly
    # software and semiconductors, with genuine deep tech a minority. Calling it
    # "Deep Tech" told users something false about the company.
    #
    # The KEY stays `deep_tech` deliberately. It is embedded in matrix_cell
    # strings across classifications, valuations, backtest_panel, cell_bands and
    # every stored 1.1-RR / 1.2-ROBUST result; renaming it would orphan all of
    # that for a cosmetic gain. The label is what users read, and the label is
    # now accurate.
    "deep_tech": "Mixed / Pre-Revenue",
    "it_services": "IT Services",
}

# Shown next to the category so a residual bucket is never mistaken for a thesis.
CATEGORY_NOTES = {
    "deep_tech": ("Residual category. A company lands here when no single "
                  "revenue stream reaches 40% of the total, or when it is "
                  "pre-revenue — so this bucket mixes frontier hardware with "
                  "software, semiconductors and platforms that the other four "
                  "models did not claim. Treat the grouping as 'not yet "
                  "classified', not as a statement that these are deep-tech "
                  "companies."),
    "it_services": NO_MODEL_CATEGORIES["it_services"],
}


def category_note(bm):
    return CATEGORY_NOTES.get(bm)


# Display order for the universe grid and any category loop.
CATEGORY_ORDER = ("hyperscale", "saas", "semi_hardware", "consumer_internet",
                  "deep_tech", "it_services")


def category_label(bm):
    return CATEGORY_LABELS.get(bm, (bm or "").replace("_", " ").title())


def suppression_reason(cell):
    """Why this cell shows no verdict, or None if it does."""
    if not cell:
        return None
    if cell in SUPPRESSED_CELLS:
        return SUPPRESSED_CELLS[cell]
    bm = cell.rsplit("-", 1)[0]
    if bm in NO_MODEL_CATEGORIES:
        return NO_MODEL_CATEGORIES[bm]
    return None


def _norm(key):
    """`EV/NTM_ARR`, `ev ntm arr` and `ev_ntm_arr` are the same key."""
    return str(key).strip().lower().replace("/", "_").replace(" ", "_").replace("-", "_")


def lookup(metrics, metric_name):
    """First present value among a metric's accepted keys. (value, key_used)."""
    if not metrics:
        return None, None
    norm = {_norm(k): v for k, v in metrics.items()}
    for key in METRIC_KEYS.get(metric_name, ()):
        v = norm.get(_norm(key))
        if v is None:
            continue
        try:
            return float(v), key
        except (TypeError, ValueError):
            continue
    return None, None


def classify(value, lo, hi, kind):
    """Where a value sits against its band. Returns a lowercase verdict."""
    if value is None:
        return None
    if kind in ("yield", "score"):          # higher is better
        return "fair" if lo <= value <= hi else ("undervalued" if value > hi else "overvalued")
    return "fair" if lo <= value <= hi else ("undervalued" if value < lo else "overvalued")


def load_bands(conn):
    """Live rolling bands from `cell_bands`, or {} if the table is absent.

    Built by build_cell_bands.py from the historical panel and shipped in the
    slim DB. The app cannot compute these at render time — it does not carry the
    panel — so a missing table falls back to the static v2 bands rather than
    failing.
    """
    try:
        rows = conn.execute(
            "SELECT matrix_cell, fair_low, fair_high, median, source, "
            "window_start, window_end, n_obs FROM cell_bands").fetchall()
    except Exception:
        return {}
    return {r[0]: dict(lo=r[1], hi=r[2], median=r[3], source=r[4],
                       window_start=r[5], window_end=r[6], n_obs=r[7]) for r in rows}


def primary_spec(cell, metrics=None, bands=None):
    """(display_name, lo, hi, unit, kind) for the cell's PRIMARY metric.

    Applies the deep_tech-1 pre-revenue switch when revenue is absent, so both
    callers make that decision identically instead of one of them forgetting.
    """
    rows = CELL_MATRIX.get(cell)
    if not rows:
        return None
    spec = rows[0]
    if cell == "deep_tech-1" and metrics is not None:
        rev, _ = lookup(metrics, "EV/NTM Rev")
        raw_rev = metrics.get("revenue_ltm")
        if rev is None and not (raw_rev and float(raw_rev) > 0):
            spec = DEEP_TECH1_FALLBACK
    # A rolling band replaces only the NUMBERS. The metric, its direction and
    # the pre-revenue switch are properties of the cell and never move.
    if bands:
        b = bands.get(cell)
        if b and b.get("lo") is not None and b.get("hi") is not None:
            name, _lo, _hi, unit, kind = spec
            spec = (name, b["lo"], b["hi"], unit, kind)
    return spec


def verdict(cell, metrics, bands=None, respect_suppression=True):
    """THE verdict function. One rule, one implementation.

    Returns (verdict, display_name, value, lo, hi) with verdict lowercase and
    one of undervalued | fair | overvalued, or None when the cell's primary
    metric cannot be resolved from `metrics`.

    Pass `bands` (from load_bands) to judge against the cohort's own trailing
    5-year median instead of the static v2 numbers. This is a statement about
    where a name sits versus its cohort's recent history — NOT a claim that the
    cheapest names in a cohort outperform. Robustness testing (1.2-ROBUST) found
    no such cross-sectional effect at any split width, in either sub-period, or
    out-of-sample.
    """
    spec = primary_spec(cell, metrics, bands)
    if not spec:
        return None, None, None, None, None
    if respect_suppression and suppression_reason(cell):
        # Return the metric and its band so the value can still be shown —
        # withhold only the judgement. Research callers pass
        # respect_suppression=False so the effect stays measurable.
        name, lo, hi, _unit, _kind = spec
        value, _key = lookup(metrics, name)
        return None, name, value, lo, hi
    name, lo, hi, _unit, kind = spec
    value, _key = lookup(metrics, name)
    if value is None:
        return None, name, None, lo, hi
    return classify(value, lo, hi, kind), name, value, lo, hi


def secondary_specs(cell):
    """Everything after the primary — display context, never the verdict."""
    return CELL_MATRIX.get(cell, [])[1:]


def cells():
    return sorted(CELL_MATRIX)


# --- back-compat shim for app.py's display code -----------------------------
# app.py renders a fair-range table from tuples shaped
# (display, key, lo, hi, unit, kind). Rebuild that shape from the registry so
# the display keeps working without a second copy of the ranges.
FAIR_RANGES_FULL = {
    cell: [(name, METRIC_KEYS[name][0], lo, hi, unit, kind)
           for (name, lo, hi, unit, kind) in rows]
    for cell, rows in CELL_MATRIX.items()
}
