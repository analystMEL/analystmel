"""Part E — the read-only Backtest page.

Displays results that `portfolio_sim.py` has already written to
`backtest_runs` / `backtest_trades` / `backtest_equity` / `backtest_cell_stats`.

READ-ONLY BY DESIGN. This page never runs a simulation. A multi-year run walks
17,366 panel rows against 2.2M price bars for every variant; doing that inside a
page render would block the app for the length of the run on every interaction,
and Streamlit re-renders on every widget change. The controls therefore select
between STORED runs — they do not parameterise a new one. To produce a new run:

    /usr/bin/python3 portfolio_sim.py

then `python3 build_repo_db.py` to ship the refreshed tables.

Wired into app.py as the "Backtest" nav item; `render_dark_table` is passed in
rather than imported to avoid a circular import.
"""
import json

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

CVE_COLOR = "#38bdf8"
BENCH_COLORS = {"SPY": "#94a3b8", "QQQ": "#f59e0b", "EW universe": "#a78bfa"}
BENCH_COLUMNS = {"SPY": "spy", "QQQ": "qqq", "EW universe": "ew_universe"}


def _pct(v, dp=1):
    return "—" if v is None else f"{v:,.{dp}f}%"


def _money(v):
    return "—" if v is None else f"£{v:,.0f}"


def _kpi(label, value, sub=None, tone="#e2e8f0"):
    st.markdown(
        f"<div style='background:#050d1a;border:1px solid rgba(56,189,248,0.18);"
        f"border-radius:10px;padding:14px 16px;'>"
        f"<div style='color:#7dd3fc;font-size:0.78em;font-weight:700;"
        f"text-transform:uppercase;letter-spacing:0.04em;'>{label}</div>"
        f"<div style='color:{tone};font-size:1.5em;font-weight:800;margin-top:4px;'>{value}</div>"
        + (f"<div style='color:#64748b;font-size:0.78em;margin-top:2px;'>{sub}</div>" if sub else "")
        + "</div>",
        unsafe_allow_html=True,
    )


def _tone(v):
    return "#4ade80" if (v or 0) > 0 else "#f87171"


def _load_runs(conn):
    rows = conn.execute(
        "SELECT run_id, label, params_json, start_date, end_date, start_capital, "
        "final_value, total_return, cagr, max_drawdown, sharpe, win_rate, "
        "avg_positions, cash_drag, turnover, n_trades, cap_engaged, matrix_version "
        # Headline runs first, then variants, then the diagnostic runs. Ordering
        # by final_value put a random-control run at the top, where it reads as
        # the result rather than as a control.
        "FROM backtest_runs ORDER BY CASE run_id "
        "WHEN 'pos5' THEN 0 WHEN 'pos10' THEN 1 WHEN 'pos20' THEN 2 "
        "WHEN 'stageweight' THEN 3 WHEN 'sellverdict' THEN 4 WHEN 'randommed' THEN 5 "
        "WHEN 'd3_50' THEN 6 WHEN 'd3_100' THEN 7 ELSE 8 END, final_value DESC"
    ).fetchall()
    cols = ("run_id", "label", "params_json", "start_date", "end_date", "start_capital",
            "final_value", "total_return", "cagr", "max_drawdown", "sharpe", "win_rate",
            "avg_positions", "cash_drag", "turnover", "n_trades", "cap_engaged",
            "matrix_version")
    return [dict(zip(cols, r)) for r in rows]


def _pair_round_trips(trades):
    """Match each SELL to the BUY that opened it, per ticker, in date order.

    The trade log stores legs, not positions, so entry stage and entry cell —
    the attributes the decision was actually made on — only exist on the BUY
    row. Contribution by stage is meaningless keyed on the exit row, where the
    stage may have moved or be absent entirely on a forced delisting exit.
    """
    open_legs, out = {}, []
    for t in sorted(trades, key=lambda x: x["date"]):
        if t["action"] == "BUY":
            open_legs.setdefault(t["ticker"], []).append(t)
        elif t["action"] == "SELL":
            legs = open_legs.get(t["ticker"])
            if legs:
                b = legs.pop(0)
                out.append(dict(
                    ticker=t["ticker"], entry=b["date"], exit=t["date"],
                    entry_cell=b["matrix_cell"], entry_stage=b["fh_stage"],
                    pnl_pct=t["pnl_pct"], reason=t["reason"],
                    pnl_abs=(t["value"] - t["cost"]) - (b["value"] + b["cost"])))
    return out


def render_backtest_page(conn, render_dark_table):
    st.markdown("<div class='fun-header'>Backtest</div>", unsafe_allow_html=True)

    if conn is None:
        st.error("Database unavailable.")
        return
    try:
        runs = _load_runs(conn)
    except Exception:
        st.warning("No backtest results in this database yet. "
                   "Run `portfolio_sim.py`, then `build_repo_db.py`.")
        return
    if not runs:
        st.warning("No backtest results stored yet. Run `portfolio_sim.py`.")
        return

    st.markdown(
        "<p style='color:#94a3b8;font-size:0.95em;margin-top:-10px;'>"
        "Historical simulation of the CVE decision rules. <b>Read-only</b> — these are "
        "stored results, not a live run. Read Section 7 before drawing any conclusion "
        "from the numbers above it.</p>",
        unsafe_allow_html=True)

    # ---------------- 1 · Controls -------------------------------------
    st.subheader("1 · Controls")
    labels = {r["label"]: r for r in runs}
    c1, c2 = st.columns([2, 2])
    with c1:
        pick = st.selectbox("Stored run", list(labels), key="bt_run")
        run = labels[pick]
    with c2:
        benches = st.multiselect("Benchmarks", list(BENCH_COLUMNS),
                                 default=list(BENCH_COLUMNS), key="bt_bench")

    eq = pd.DataFrame(conn.execute(
        "SELECT date, portfolio_value, cash, n_positions, spy, qqq, ew_universe "
        "FROM backtest_equity WHERE run_id=? ORDER BY date", (run["run_id"],)).fetchall(),
        columns=["date", "portfolio_value", "cash", "n_positions", "spy", "qqq", "ew_universe"])
    if eq.empty:
        st.warning("This run has no stored equity curve.")
        return

    c3, c4, c5 = st.columns([3, 1, 1])
    with c3:
        dates = list(eq["date"])
        lo, hi = st.select_slider("Date range", options=dates,
                                  value=(dates[0], dates[-1]), key="bt_range")
    with c4:
        log_scale = st.checkbox("Log scale", value=True, key="bt_log")
    with c5:
        rebase = st.checkbox("Rebase to range", value=False, key="bt_rebase",
                             help="Restart every series at the starting capital on the "
                                  "first date in range, so a sub-period is compared "
                                  "like-for-like.")

    params = json.loads(run["params_json"] or "{}")
    st.caption(
        f"**{run['label']}** · {run['start_date']} → {run['end_date']} · "
        f"capital {_money(run['start_capital'])} · "
        f"{params.get('positions', '—')} positions · "
        f"{params.get('sizing', '—')}-weight · quarterly rebalance on filing dates · "
        f"cost {params.get('trade_cost', 0)*100:.1f}%/trade · "
        f"cap {params.get('max_weight', 0)*100:.0f}% · matrix {run['matrix_version']}"
        + (f" · POOL RESTRICTED to {len(params['restrict'])} delisted names"
           if params.get("restrict") else "")
        + (f" · delisted forced to {params['delisted_terminal']:+.0%}"
           if params.get("delisted_terminal") is not None else ""))

    view = eq[(eq["date"] >= lo) & (eq["date"] <= hi)].reset_index(drop=True)
    if view.empty:
        st.warning("No data in the selected range.")
        return

    # ---------------- 2 · Equity curve ---------------------------------
    st.subheader("2 · Equity curve")
    series = {"CVE portfolio": ("portfolio_value", CVE_COLOR)}
    for b in benches:
        series[b] = (BENCH_COLUMNS[b], BENCH_COLORS[b])

    fig = go.Figure()
    for name, (col, color) in series.items():
        y = view[col].astype(float)
        if rebase and y.iloc[0]:
            y = y / y.iloc[0] * run["start_capital"]
        fig.add_trace(go.Scatter(
            x=view["date"], y=y, name=name, mode="lines",
            line=dict(color=color, width=2.5 if name == "CVE portfolio" else 1.6),
            hovertemplate="%{x}<br>" + name + " £%{y:,.0f}<extra></extra>"))
    fig.update_layout(
        template="plotly_dark", plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)", height=440,
        margin=dict(l=10, r=10, t=30, b=10),
        legend=dict(orientation="h", y=1.12, x=0),
        yaxis=dict(type="log" if log_scale else "linear", title="portfolio value (£)",
                   gridcolor="rgba(56,189,248,0.08)"),
        xaxis=dict(gridcolor="rgba(56,189,248,0.06)"))
    st.plotly_chart(fig, use_container_width=True)

    # ---------------- 3 · Headline stats -------------------------------
    st.subheader("3 · Headline stats")
    k = st.columns(5)
    with k[0]:
        _kpi("Final value", _money(run["final_value"]),
             f"from {_money(run['start_capital'])}")
    with k[1]:
        _kpi("Total return", _pct(run["total_return"]), tone=_tone(run["total_return"]))
    with k[2]:
        _kpi("CAGR", _pct(run["cagr"]), tone=_tone(run["cagr"]))
    with k[3]:
        _kpi("Max drawdown", _pct(run["max_drawdown"]), tone="#f87171")
    with k[4]:
        _kpi("Sharpe", f"{run['sharpe']:.2f}", "monthly marks, rf = 0")

    k2 = st.columns(5)
    with k2[0]:
        _kpi("Win rate", _pct(run["win_rate"]), "closed round trips")
    with k2[1]:
        _kpi("Trades", f"{run['n_trades']:,}")
    with k2[2]:
        _kpi("Turnover", _pct(run["turnover"], 0), "per year, one-way")
    with k2[3]:
        _kpi("Cash drag", _pct(run["cash_drag"]), "avg cash weight")
    with k2[4]:
        _kpi("Position cap", f"{run['cap_engaged']}", "times 35% cap engaged")

    st.markdown("**Excess vs each benchmark** (end of full run, not the selected range)")
    final_row = eq.iloc[-1]
    rows = []
    for b in BENCH_COLUMNS:
        bv = float(final_row[BENCH_COLUMNS[b]])
        exc = (run["final_value"] / bv - 1) * 100 if bv else None
        rows.append([b, _money(bv), _pct(exc),
                     "beats" if (exc or 0) > 0 else "loses to"])
    render_dark_table(["Benchmark", "Final value", "CVE excess", "Result"], rows)

    # ---------------- 4 · Trade log ------------------------------------
    st.subheader("4 · Trade log")
    tr_rows = conn.execute(
        "SELECT date, ticker, action, price, value, cost, matrix_cell, verdict, "
        "fh_stage, reason, pnl_pct FROM backtest_trades WHERE run_id=? ORDER BY date, ticker",
        (run["run_id"],)).fetchall()
    cols = ("date", "ticker", "action", "price", "value", "cost", "matrix_cell",
            "verdict", "fh_stage", "reason", "pnl_pct")
    trades = [dict(zip(cols, r)) for r in tr_rows]

    f1, f2, f3 = st.columns([1, 1, 2])
    with f1:
        act = st.selectbox("Action", ["All", "BUY", "SELL", "TRIM"], key="bt_act")
    with f2:
        cells = ["All"] + sorted({t["matrix_cell"] for t in trades if t["matrix_cell"]})
        cell_f = st.selectbox("Cell", cells, key="bt_cell")
    with f3:
        tick_f = st.text_input("Ticker filter", key="bt_tick").strip().upper()

    shown = [t for t in trades
             if (act == "All" or t["action"] == act)
             and (cell_f == "All" or t["matrix_cell"] == cell_f)
             and (not tick_f or tick_f in t["ticker"])]
    st.caption(f"{len(shown):,} of {len(trades):,} decisions")
    render_dark_table(
        ["Date", "Ticker", "Action", "Price", "Value", "Cost", "Cell", "Verdict",
         "Stage", "P&L", "Reason"],
        [[t["date"], t["ticker"], t["action"], f"{t['price']:,.2f}",
          _money(t["value"]), f"£{t['cost']:,.2f}", t["matrix_cell"] or "—",
          t["verdict"] or "—", f"S{t['fh_stage']}" if t["fh_stage"] else "—",
          _pct(t["pnl_pct"]) if t["pnl_pct"] is not None else "—", t["reason"]]
         for t in shown[:400]])
    if len(shown) > 400:
        st.caption(f"Showing the first 400 of {len(shown):,}.")

    # ---------------- 5 · Cell diagnostics -----------------------------
    st.subheader("5 · Cell diagnostics")
    st.caption("Which cells actually fired. A cell that never fires is dead weight — "
               "a candidate for deletion or wider bands.")
    cell_rows = conn.execute(
        "SELECT matrix_cell, trades, closed, mean_return, median_return, hit_rate, "
        "contribution FROM backtest_cell_stats WHERE run_id=? ORDER BY trades DESC",
        (run["run_id"],)).fetchall()
    if cell_rows:
        render_dark_table(
            ["Cell", "Buys fired", "Closed round trips", "Mean return", "Median return",
             "Hit rate", "Contribution"],
            [[c[0], f"{c[1]:,}", f"{c[2]:,}", _pct(c[3]), _pct(c[4]), _pct(c[5]),
              _money(c[6])] for c in cell_rows])
        st.caption(f"{len(cell_rows)} of 20 matrix cells traded in this run. "
                   "Sample sizes here are far below the 30 stock-quarters needed for a "
                   "conclusion — the per-cell evidence lives in Test 1 on the full "
                   "panel, not in this portfolio.")
    else:
        st.info("No cell statistics stored for this run.")

    # ---------------- 6 · Attribution ----------------------------------
    st.subheader("6 · Attribution")
    trips = _pair_round_trips(trades)
    if trips:
        ranked = sorted(trips, key=lambda x: -(x["pnl_pct"] or 0))
        hdr = ["Ticker", "Cell", "Held", "P&L"]

        def rows_for(ts):
            return [[t["ticker"], t["entry_cell"] or "—",
                     f"{t['entry']} → {t['exit']}", _pct(t["pnl_pct"])] for t in ts]

        # With 20 or fewer round trips, best-10 and worst-10 overlap and the
        # same +1,479% trade appears in both tables — one of them labelled
        # "worst". Show a single ranked table instead.
        if len(ranked) <= 20:
            st.markdown(f"**All {len(ranked)} round trips, best to worst**")
            render_dark_table(hdr, rows_for(ranked))
        else:
            a1, a2 = st.columns(2)
            with a1:
                st.markdown("**Best 10 round trips**")
                render_dark_table(hdr, rows_for(ranked[:10]))
            with a2:
                st.markdown("**Worst 10 round trips**")
                render_dark_table(hdr, rows_for(ranked[-10:][::-1]))

        st.markdown("**Contribution by entry stage** — the stage the position was "
                    "bought at, not the stage it exited at.")
        by_stage = {}
        for t in trips:
            s = t["entry_stage"]
            b = by_stage.setdefault(s, dict(n=0, pnl=0.0, wins=0))
            b["n"] += 1
            b["pnl"] += t["pnl_abs"] or 0
            b["wins"] += 1 if (t["pnl_pct"] or 0) > 0 else 0
        render_dark_table(
            ["Entry stage", "Round trips", "Net P&L", "Hit rate"],
            [[f"S{s}" if s else "—", f"{v['n']:,}", _money(v["pnl"]),
              _pct(100 * v["wins"] / v["n"] if v["n"] else None)]
             for s, v in sorted(by_stage.items(), key=lambda kv: (kv[0] is None, kv[0]))])
    else:
        st.info("No closed round trips in this run.")

    # ---------------- 7 · Limitations ----------------------------------
    st.subheader("7 · Limitations")
    st.markdown(
        "<div style='background:rgba(248,113,113,0.06);border:1px solid rgba(248,113,113,0.3);"
        "border-radius:10px;padding:16px 20px;'>"
        "<div style='color:#f87171;font-weight:800;margin-bottom:8px;'>"
        "Read these alongside the numbers, not after them.</div>"
        "<ul style='color:#cbd5e1;font-size:0.9em;line-height:1.7;margin:0;padding-left:18px;'>"

        "<li><b>NTM revenue is a proxy, not an estimate.</b> The matrix uses EV/NTM Rev, "
        "but historical analyst estimates were never available. Every EV/NTM Rev cell "
        "uses <code>TTM revenue × (1 + trailing YoY growth)</code> — a backward-looking "
        "stand-in for a forward-looking number. It is applied to ~7,000 of 17,366 panel "
        "rows and assumes growth persists, which flatters decelerating companies.</li>"

        "<li><b>Survivorship bias is mitigated, not eliminated — and the bound here is "
        "vacuous.</b> Only 12 delisted names carry usable history: Alpha Vantage keeps no "
        "fundamentals for 360 of 362 older delisted tech candidates. The D3 stress runs "
        "(delisted holdings forced to −50% / −100%) leave the headline unchanged only "
        "because the strategy never bought a delisted name — that is an absence of "
        "evidence, not a clean bill of health. The market-cap floor also biases the "
        "delisted cohort toward premium acquisitions and away from failures.</li>"

        "<li><b>Fair ranges are calibrated on recent pricing and are absolute.</b> "
        "75.6% of 2012 rows read <i>undervalued</i> against 43.1% of 2026 rows — a decade "
        "of multiple expansion drags the whole universe from cheap to expensive rather "
        "than the engine detecting anything. Expect weak fit in the early period.</li>"

        "<li><b>Business model is held static.</b> Each ticker carries its present-day "
        "classification across all history; only the financial-health stage is recomputed "
        "point-in-time. The LLM tiebreaker is non-deterministic and too costly to re-run "
        "per stock-quarter. This is a mild hindsight bias in the cell assignment.</li>"

        "<li><b>Non-USD filers are excluded entirely</b> (TSM, ASML, SAP, BABA and 38 "
        "others). No currency mixing — but the universe is US-listed USD reporters only.</li>"

        "<li><b>THE CROSS-SECTIONAL CLAIM FAILED — read this first.</b> Robustness "
        "testing (500-permutation placebo, four split widths, two sub-periods, and an "
        "out-of-sample run) found <b>no advantage to the cheapest names within a cohort</b>. "
        "Ranking a cell's stocks cheapest-to-dearest each quarter gave +0.1pp for "
        "semiconductors, and negative spreads for SaaS and consumer internet. The one "
        "apparent survivor collapsed out-of-sample once tested against its own null "
        "(+14.3pp raw, but its placebo null centres at +11.2pp; residual p=0.106). "
        "Every portfolio number on this page rests on a selection rule the evidence "
        "does not support.</li>"

        "<li><b>Bootstrap intervals understate uncertainty on unbalanced legs.</b> The "
        "bootstrap resamples within each leg, so it cannot see bias arising from "
        "cross-group weighting. Any scheme whose undervalued and overvalued legs differ "
        "in size per quarter has a null centred away from zero, and its CI will look "
        "significant when it is not. Only an even split (equal counts per group) has a "
        "null at zero by construction.</li>"

        "<li><b>Position selection is not part of the engine.</b> The verdict leaves ~140 "
        "candidates per quarter for 5 slots, so a ranking had to be invented. Picking at "
        "random from the same pool beat the ranking (£143,759 median vs £77,231), and "
        "the spread across random seeds was 9x — at 4–5 positions, luck dominates. Do not read the portfolio result as "
        "a measure of the engine; that is what Test 1 on the full panel is for.</li>"

        "<li><b>Costs are 0.5% per trade and nothing else.</b> No market impact, no "
        "taxes, no borrow, no slippage beyond that spread assumption. Turnover above "
        "60%/yr means costs matter materially here.</li>"

        "<li><b>The panel bypasses the app's own verdict plumbing.</b> A live key-mapping "
        "defect leaves 7 of 20 cells verdict-less in the Analysis page; the backtest "
        "computes each cell's documented primary metric directly instead. Backtest and "
        "app verdicts can therefore differ until that defect is fixed.</li>"

        "<li><b>Not financial advice, and not a live trading system.</b> A notional "
        "£10,000 simulation on a survivor-skewed technology universe over the strongest "
        "technology bull market on record.</li>"
        "</ul></div>",
        unsafe_allow_html=True)

    with st.expander("All stored runs — side by side"):
        render_dark_table(
            ["Run", "Final", "Total", "CAGR", "Max DD", "Sharpe", "Win rate", "Trades"],
            [[r["label"], _money(r["final_value"]), _pct(r["total_return"]),
              _pct(r["cagr"]), _pct(r["max_drawdown"]), f"{r['sharpe']:.2f}",
              _pct(r["win_rate"]), f"{r['n_trades']:,}"] for r in runs],
            highlight_first_col=run["label"])
        st.caption("Runs are stored by `portfolio_sim.py`. The concentration runs (5 / 10 / "
                   "20 positions) are the required sensitivity check; the delisted-only "
                   "runs exist to prove the Rule 1 hard-stop reaches the portfolio loop "
                   "and are not strategies.")
