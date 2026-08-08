import sqlite3
import pandas as pd
import yfinance as yf
import json
from datetime import datetime, timedelta
import logging
import sys

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger(__name__)

# Import local modules
from valuation_models import calculate_target_price
from logic_engine import compute_sector_baselines, evaluate_buy_sell, get_multiple_key_from_method
from data_pipeline1 import DB_NAME, classify_financial_health, classify_business_model

REPORTING_LAG_DAYS = 45

def _safe_float(val):
    try:
        if val is None or val == "None" or val == "": return None
        return float(val)
    except (ValueError, TypeError):
        return None

def _ltm_sum(series: list[dict], field: str, n: int = 4) -> float:
    vals = [_safe_float(q.get(field)) for q in series[:n]]
    return sum(v for v in vals if v is not None)

def get_point_in_time_fundamentals(conn, ticker, target_date, statement_type):
    """Fetch fundamentals known to the market ON or BEFORE target_date.

    Uses the true filing date (`reported_date`, from AV EARNINGS) when present.
    Rows predating the backfill have reported_date = NULL and fall back to the
    old flat 45-day-after-fiscal-end approximation, so behaviour is unchanged
    for un-backfilled data but exact once backfill_fundamentals.py has run.
    """
    cursor = conn.cursor()
    cursor.execute("""
        SELECT fiscal_date, raw_data_json FROM fundamentals
        WHERE ticker = ? AND statement_type = ?
          AND COALESCE(reported_date, date(fiscal_date, ?)) <= ?
        ORDER BY COALESCE(reported_date, date(fiscal_date, ?)) DESC
        LIMIT 8
    """, (ticker, statement_type,
          f"+{REPORTING_LAG_DAYS} days", target_date, f"+{REPORTING_LAG_DAYS} days"))
    rows = cursor.fetchall()
    return [{"fiscal_date": r[0], **json.loads(r[1])} for r in rows]

def get_price_on_date(conn, ticker, target_date):
    """Get the closing price of the ticker on exactly target_date, or most recent prior trading day."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT close FROM price_data
        WHERE ticker = ? AND date <= ?
        ORDER BY date DESC LIMIT 1
    """, (ticker, target_date))
    row = cursor.fetchone()
    return row[0] if row else None

def compute_point_in_time_metrics(conn, ticker, target_date):
    """Computes all required metrics exactly as they were known on target_date."""
    inc = get_point_in_time_fundamentals(conn, ticker, target_date, "INCOME_STATEMENT")
    cf  = get_point_in_time_fundamentals(conn, ticker, target_date, "CASH_FLOW")
    bs  = get_point_in_time_fundamentals(conn, ticker, target_date, "BALANCE_SHEET")
    
    if not inc or not cf or not bs: return None
    
    as_of_date = inc[0]["fiscal_date"]
    
    rev = _ltm_sum(inc, "totalRevenue")
    gp = _ltm_sum(inc, "grossProfit")
    op_inc = _ltm_sum(inc, "operatingIncome")
    ebit = _ltm_sum(inc, "ebit")
    ni = _ltm_sum(inc, "netIncome")
    sbc = _ltm_sum(cf, "stockBasedCompensation")
    ocf = _ltm_sum(cf, "operatingCashflow")
    capex = abs(_ltm_sum(cf, "capitalExpenditures"))
    da = (_ltm_sum(cf, "depreciationDepletionAndAmortization")
          or _ltm_sum(cf, "depreciationAmortization")
          or _ltm_sum(cf, "depreciation"))
    
    rev_prior = _ltm_sum(inc, "totalRevenue", 8) - rev
    ni_prior = _ltm_sum(inc, "netIncome", 8) - ni
    op_prior = _ltm_sum(inc, "operatingIncome", 8) - op_inc
    
    latest_bs = bs[0]
    cash = _safe_float(latest_bs.get("cashAndCashEquivalentsAtCarryingValue"))
    std = _safe_float(latest_bs.get("shortTermDebt") or latest_bs.get("currentPortionOfLongTermDebt"))
    ltd = _safe_float(latest_bs.get("longTermDebtNoncurrent") or latest_bs.get("longTermDebt"))
    debt = (std or 0) + (ltd or 0)
    equity = _safe_float(latest_bs.get("totalShareholderEquity") or latest_bs.get("stockholdersEquity"))
    assets = _safe_float(latest_bs.get("totalAssets"))
    curr_liab = _safe_float(latest_bs.get("totalCurrentLiabilities") or latest_bs.get("currentNetLiabilities"))
    
    def pct(n, d): return round(n / d * 100, 4) if d else None
    
    fcf = ocf - capex
    fcf_margin_adj = pct(fcf - sbc, rev) if rev else None
    rev_growth = pct(rev - rev_prior, rev_prior) if rev_prior else None
    
    op_growth = pct(op_inc - op_prior, abs(op_prior)) if op_prior else None
    op_lev = round(op_growth - rev_growth, 2) if op_growth is not None and rev_growth is not None else None
    
    debt_eq = round(debt / equity, 4) if equity and equity > 0 else None
    mburn = abs(fcf) / 12 if fcf < 0 else None
    runway = min(round(cash / mburn, 1), 999) if mburn and mburn > 0 and cash else 999
    
    ic = (assets or 0) - (curr_liab or 0)
    roic = pct(ebit, ic) if ic > 0 and ebit is not None else None

    # Load point-in-time price to calculate market cap and EV
    price = get_price_on_date(conn, ticker, target_date)
    # Historical shares outstanding come from the point-in-time balance sheet
    # (AV `commonStockSharesOutstanding`), NOT today's count — using today's
    # share base for a 2015 valuation corrupts market cap / EV / P/E for any
    # name that issued stock (SBC-heavy tech) or bought it back. Fall back to
    # the latest computed_metrics value only if the historical field is absent.
    shares = _safe_float(latest_bs.get("commonStockSharesOutstanding"))
    if not shares or shares <= 0:
        cursor = conn.cursor()
        cursor.execute("SELECT shares_outstanding FROM computed_metrics WHERE ticker = ? ORDER BY as_of_date DESC LIMIT 1", (ticker,))
        row = cursor.fetchone()
        shares = row[0] if row else None

    if price and shares:
        mcap = price * shares
        ev = mcap + debt - (cash or 0)
        eps = round(ni / shares, 4) if shares else None
    else:
        mcap = None
        ev = None
        eps = None
        
    ebitda = (op_inc or 0) + (da or 0) if op_inc is not None else None
    
    metrics = {
        "ticker": ticker,
        "as_of_date": as_of_date,
        "revenue_ltm": rev,
        "revenue_growth_yoy": rev_growth,
        "gross_profit_ltm": gp,
        "gross_margin": pct(gp, rev),
        "operating_income_ltm": op_inc,
        "operating_margin": pct(op_inc, rev),
        "ebit_ltm": ebit,
        "net_income_ltm": ni,
        "net_income_prior": ni_prior,
        "ocf_ltm": ocf,
        "capex_ltm": capex,
        "fcf_ltm": fcf,
        "fcf_margin": pct(fcf, rev),
        "sbc_ltm": sbc,
        "sbc_pct_revenue": pct(sbc, rev),
        "fcf_margin_adj": fcf_margin_adj,
        "cash_and_equiv": cash,
        "total_debt": debt,
        "net_cash": (cash or 0) - debt,
        "total_equity": equity,
        "debt_equity_ratio": debt_eq,
        "monthly_burn": mburn,
        "cash_runway_months": runway,
        "operating_leverage": op_lev,
        "roic": roic,
        "inventory": _safe_float(latest_bs.get("inventory")),
        "total_assets": assets,
        "current_liabilities": curr_liab,
        "shares_outstanding": shares,
        "market_cap": mcap,
        "enterprise_value": ev,
        "da_ltm": da,
        "ebitda_ltm": ebitda,
        "eps_ltm": eps,
        "pe_ratio": mcap / ni if mcap and ni and ni > 0 else None,
        "ps_ratio": mcap / rev if mcap and rev and rev > 0 else None,
        "ev_revenue": ev / rev if ev and rev and rev > 0 else None,
        "ev_ebitda": ev / ebitda if ev and ebitda and ebitda > 0 else None,
        "ev_fcf": ev / (ocf - capex - sbc) if ev and (ocf - capex - sbc) > 0 else None,
        "fcf_yield": ((ocf - capex - sbc) / mcap * 100) if mcap and (ocf - capex - sbc) > 0 else None,
        "ev_gross_profit": ev / gp if ev and gp and gp > 0 else None,
        "capex_adj_ev_ebit": ev / (ebit - capex*0.5) if ev and ebit and (ebit - capex*0.5) > 0 else None,
        "rule_of_40": (rev_growth or 0) + (fcf_margin_adj or 0)
    }
    
    # Run classification
    fh = classify_financial_health(metrics)
    bm = classify_business_model(ticker, metrics)
    metrics["fh_stage"] = fh["fh_stage"]
    metrics["bm_category"] = bm["bm_category"]
    metrics["matrix_cell"] = f"{bm['bm_category']}-{fh['fh_stage']}"
    
    # Valuation method mapping
    from data_pipeline1 import VALUATION_MATRIX
    metrics["primary_method"] = VALUATION_MATRIX.get((bm['bm_category'], fh['fh_stage']), "EV/Revenue")
    
    return metrics


def run_backtest():
    log.info("Starting Valoura 5-year Backtest...")
    
    conn = sqlite3.connect(DB_NAME)
    
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=365 * 5)
    
    # QQQ benchmark — read from local price_data first (backfilled), so the
    # whole backtest runs with zero network calls. Fall back to yfinance only
    # if the local series is absent.
    s_str, e_str = start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")
    start_qqq_price = get_price_on_date(conn, "QQQ", s_str)
    end_qqq_price = get_price_on_date(conn, "QQQ", e_str)
    if start_qqq_price and end_qqq_price:
        log.info("QQQ benchmark loaded from local price_data (no network call).")
    else:
        log.info(f"QQQ not local — downloading benchmark {s_str}..{e_str}...")
        qqq = yf.download("QQQ", start=s_str, end=e_str, progress=False)
        if qqq.empty:
            log.error("Failed to download QQQ data.")
            return
        start_qqq_price = float(qqq['Close'].iloc[0])
        end_qqq_price = float(qqq['Close'].iloc[-1])
    qqq_return = (end_qqq_price / start_qqq_price - 1) * 100
    
    portfolio_cash = 10000.0
    portfolio_shares = {}
    underperf_counts = {}
    portfolio_history = []
    
    # Get all tickers
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT ticker FROM fundamentals")
    tickers = [row[0] for row in cursor.fetchall()]
    
    current_date = start_date
    step = timedelta(days=90)
    
    while current_date <= end_date:
        d_str = current_date.strftime("%Y-%m-%d")
        log.info(f"--- Quarter: {d_str} ---")
        
        # 1. Fetch universe metrics for this point in time
        universe_metrics = []
        ticker_prices = {}
        for t in tickers:
            p = get_price_on_date(conn, t, d_str)
            if not p: continue
            ticker_prices[t] = p
            m = compute_point_in_time_metrics(conn, t, d_str)
            if m:
                universe_metrics.append(m)
                
        if not universe_metrics:
            log.warning("No fundamental data available yet. Skipping quarter.")
            current_date += step
            continue
            
        # Phase 1: Sector Baselines
        baselines = compute_sector_baselines(universe_metrics)
        
        # Calculate total equity before trading
        total_equity = sum(portfolio_shares.get(t, 0) * ticker_prices.get(t, 0) for t in portfolio_shares)
        total_value = portfolio_cash + total_equity
        log.info(f"Pre-trade Value: ${total_value:,.2f} (Cash: ${portfolio_cash:,.2f})")
        
        # Process Sells
        for m in universe_metrics:
            t = m["ticker"]
            if t not in portfolio_shares:
                continue
                
            p = ticker_prices[t]
            method = m["primary_method"]
            mult_key = get_multiple_key_from_method(method)
            median = baselines.get(m["bm_category"], {}).get("multiples", {}).get(mult_key)
            
            target = calculate_target_price(m, median, method)
            underperf = underperf_counts.get(t, 0)
            
            sig = evaluate_buy_sell(t, p, target, m, baselines, underperf)
            underperf_counts[t] = sig["new_underperf_count"]
            
            if sig["action"] == "SELL":
                shares_to_sell = portfolio_shares[t]
                value = shares_to_sell * p
                portfolio_cash += value
                del portfolio_shares[t]
                log.info(f"SELL {t}: {shares_to_sell:,.2f} shares @ ${p:.2f} (${value:,.2f}) - {sig['reason']}")

        # Process Buys
        for m in universe_metrics:
            t = m["ticker"]
            p = ticker_prices[t]
            if not p: continue
            
            method = m["primary_method"]
            mult_key = get_multiple_key_from_method(method)
            median = baselines.get(m["bm_category"], {}).get("multiples", {}).get(mult_key)
            
            target = calculate_target_price(m, median, method)
            underperf = underperf_counts.get(t, 0)
            
            sig = evaluate_buy_sell(t, p, target, m, baselines, underperf)
            underperf_counts[t] = sig["new_underperf_count"]
            
            if sig["action"] == "BUY" and t not in portfolio_shares:
                # Recalculate total value to get 10% sizing
                current_equity = sum(portfolio_shares.get(tk, 0) * ticker_prices.get(tk, 0) for tk in portfolio_shares)
                current_tot = portfolio_cash + current_equity
                max_alloc = current_tot * 0.10
                trade_alloc = min(max_alloc, portfolio_cash)
                
                if trade_alloc > 100: # Min trade size
                    shares_to_buy = trade_alloc / p
                    portfolio_cash -= trade_alloc
                    portfolio_shares[t] = shares_to_buy
                    log.info(f"BUY {t}: {shares_to_buy:,.2f} shares @ ${p:.2f} (${trade_alloc:,.2f}) - {sig['reason']}")

        # End of Quarter accounting
        total_equity = sum(portfolio_shares.get(t, 0) * ticker_prices.get(t, 0) for t in portfolio_shares)
        total_value = portfolio_cash + total_equity
        portfolio_history.append({"date": d_str, "value": total_value})
        
        current_date += step
        
    # Phase 4: Final Output
    valoura_return = (total_value / 10000.0 - 1) * 100
    
    summary = {
        "start_date": start_date.strftime("%Y-%m-%d"),
        "end_date": end_date.strftime("%Y-%m-%d"),
        "starting_cash": 10000.0,
        "ending_value": total_value,
        "valoura_total_return_pct": round(valoura_return, 2),
        "qqq_total_return_pct": round(qqq_return, 2),
        "alpha_pct": round(valoura_return - qqq_return, 2),
        "final_positions": {k: {"shares": round(v, 2), "value": round(v * ticker_prices[k], 2)} for k, v in portfolio_shares.items()}
    }
    
    print("\n" + "=" * 80)
    print("VALOURA BACKTEST SUMMARY")
    print("=" * 80)
    print(json.dumps(summary, indent=2))
    
    with open("backtest_results.json", "w") as f:
        json.dump(summary, f, indent=2)

if __name__ == "__main__":
    run_backtest()
