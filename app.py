import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import datetime
import time
import requests
import xml.etree.ElementTree as ET
from yahooquery import Ticker as YQTicker
import json

# --- Page Configuration ---
st.set_page_config(page_title="Contextual Valuation Engine", page_icon=None, layout="wide")

# --- CVE DB Connection ---
@st.cache_resource
def get_db_connection():
    import sqlite3, os
    # DB lives one level up in the scratch directory (parent of this UI folder)
    app_dir = os.path.dirname(os.path.abspath(__file__))
    db_path = os.path.join(app_dir, "valoura_backtest.db")
    if not os.path.exists(db_path):
        # Also try one directory up (scratch folder)
        db_path = os.path.join(os.path.dirname(app_dir), "valoura_backtest.db")
    if not os.path.exists(db_path):
        return None
    return sqlite3.connect(db_path, check_same_thread=False)


def render_dark_table(headers, rows, highlight_first_col=None):
    """Render a cold-themed HTML table via st.markdown.

    Streamlit's st.dataframe renders to a canvas (glide-data-grid) which
    ignores page CSS, so it always shows light. This helper renders a real
    HTML <table> that inherits the black / cold-navy / ice-blue scheme.

    headers:            list of column header strings.
    rows:               list of row-lists; each cell is a pre-formatted string.
    highlight_first_col: if set, any row whose first cell equals this value is
                         highlighted (solid ice-blue background, black bold text).
    """
    th_s = ("padding:11px 16px;text-align:left;color:#7dd3fc;font-size:0.9em;"
            "font-weight:700;background:#050d1a;white-space:nowrap;"
            "border-bottom:1px solid rgba(56,189,248,0.3);")
    td_base = ("padding:10px 16px;font-size:0.88em;color:#e2e8f0;"
               "border-bottom:1px solid rgba(56,189,248,0.08);")
    td_hl = ("padding:10px 16px;font-size:0.88em;color:#000000;font-weight:800;"
             "background:rgba(56,189,248,0.85);"
             "border-bottom:1px solid rgba(56,189,248,0.3);")

    header_html = "".join(f'<th style="{th_s}">{h}</th>' for h in headers)
    body_html = ""
    for row in rows:
        _hl = (highlight_first_col is not None
               and len(row) > 0 and str(row[0]) == str(highlight_first_col))
        _td = td_hl if _hl else td_base
        cells = "".join(f'<td style="{_td}">{c}</td>' for c in row)
        body_html += f"<tr>{cells}</tr>"

    st.markdown(
        '<div style="overflow-x:auto;margin:8px 0 4px 0;'
        'border:1px solid rgba(56,189,248,0.2);border-radius:8px;background:#0a1628;">'
        '<table style="border-collapse:collapse;width:100%;">'
        f'<thead><tr>{header_html}</tr></thead>'
        f'<tbody>{body_html}</tbody>'
        '</table></div>',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Fair-range reference per matrix cell — shared by Analysis, Home, Watchlist.
# Each tuple: (display_name, val_data_key, low, high, unit, kind)
# kind: "multiple" (higher=expensive), "yield" (higher=cheaper), "score" (higher=better)
# ---------------------------------------------------------------------------
FAIR_RANGES_FULL = {
    "hyperscale-4": [
        ("FCF Yield",    "fcf_yield",          2.0, 4.0, "%", "yield"),
        ("PEG",          "peg_ratio",          0.5, 1.5, "x", "multiple"),
        ("Rule of 40",   "rule_of_40",         30,  50,  "%", "score"),
    ],
    "hyperscale-3": [
        ("CapEx EV/EBIT","capex_adj_ev_ebit",  20,  35,  "x", "multiple"),
        ("PEG",          "peg_ratio",          0.5, 1.5, "x", "multiple"),
        ("Rule of 40",   "rule_of_40",         25,  45,  "%", "score"),
    ],
    "hyperscale-2": [
        # Floor lowered 6 -> 3 (v2.1): mature hyperscalers (AMZN 3.4x, IBM 3.6x)
        # were scoring "Undervalued" against a floor calibrated for growth names.
        ("EV/NTM Rev",   "ev_ntm_revenue",     3,   12,  "x", "multiple"),
    ],
    "hyperscale-1": [
        ("EV/NTM Rev",   "ev_ntm_arr",         6,   15,  "x", "multiple"),
        ("Rule of 40",   "rule_of_40",         0,   20,  "%", "score"),
    ],
    "saas-4": [
        ("FCF Yield",    "fcf_yield",          2.0, 3.5, "%", "yield"),
        ("EV/FCF",       "ev_fcf",             20,  35,  "x", "multiple"),
        ("PEG",          "peg_ratio",          0.5, 1.5, "x", "multiple"),
        ("Rule of 40",   "rule_of_40",         40,  60,  "%", "score"),
    ],
    "saas-3": [
        ("EV/FCF",       "ev_fcf",             25,  45,  "x", "multiple"),
        ("PEG",          "peg_ratio",          0.8, 2.0, "x", "multiple"),
        ("Rule of 40",   "rule_of_40",         30,  50,  "%", "score"),
    ],
    "saas-2": [
        ("EV/NTM ARR",   "ev_ntm_arr",         8,   18,  "x", "multiple"),
        ("Rule of 40",   "rule_of_40",         15,  35,  "%", "score"),
    ],
    "saas-1": [
        ("EV/NTM Rev",   "ev_ntm_arr",         8,   20,  "x", "multiple"),
        ("Rule of 40",   "rule_of_40",         0,   20,  "%", "score"),
    ],
    "semi_hardware-4": [
        ("FCF Yield",    "fcf_yield",          2.5, 4.5, "%", "yield"),
        ("PEG",          "peg_ratio",          0.5, 1.2, "x", "multiple"),
    ],
    "semi_hardware-3": [
        ("Cycle P/E",    "cycle_adj_pe",       20,  35,  "x", "multiple"),
        ("PEG",          "peg_ratio",          0.5, 1.5, "x", "multiple"),
    ],
    "semi_hardware-2": [
        ("EV/NTM Rev",   "ev_ntm_arr",         1.5, 4.0, "x", "multiple"),
    ],
    "semi_hardware-1": [
        ("EV/NTM Rev",   "ev_ntm_arr",         0.5, 2.0, "x", "multiple"),
    ],
    "consumer_internet-4": [
        ("P/E",          "pe_ratio",           15,  25,  "x", "multiple"),
        ("EV/EBITDA",    "ev_ebitda",          12,  18,  "x", "multiple"),
        ("PEG",          "peg_ratio",          0.8, 1.5, "x", "multiple"),
    ],
    "consumer_internet-3": [
        ("EV/EBITDA",    "ev_ebitda",          18,  30,  "x", "multiple"),
        ("PEG",          "peg_ratio",          1.0, 2.5, "x", "multiple"),
        ("Rule of 40",   "rule_of_40",         20,  40,  "%", "score"),
    ],
    "consumer_internet-2": [
        ("EV/NTM Rev",   "ev_ntm_arr",         4,   10,  "x", "multiple"),
        ("Rule of 40",   "rule_of_40",         10,  30,  "%", "score"),
    ],
    "consumer_internet-1": [
        ("EV/NTM Rev",   "ev_ntm_arr",         1,   5,   "x", "multiple"),
    ],
    "deep_tech-4": [
        ("FCF Yield",    "fcf_yield",          2.0, 4.0, "%", "yield"),
        ("PEG",          "peg_ratio",          0.5, 1.5, "x", "multiple"),
    ],
    "deep_tech-3": [
        ("EV/GP",        "ev_gross_profit",    15,  30,  "x", "multiple"),
        ("Rule of 40",   "rule_of_40",         10,  30,  "%", "score"),
    ],
    "deep_tech-2": [
        ("EV/NTM Rev",   "ev_ntm_arr",         5,   12,  "x", "multiple"),
    ],
    "deep_tech-1": [
        ("EV/NTM Rev",   "ev_ntm_revenue",     20,  60,  "x", "multiple"),
    ],
}


def derive_verdict(matrix_cell, val_data):
    """Verdict from the cell's PRIMARY metric (first FAIR_RANGES_FULL row).

    Returns (verdict, metric_display_name, value) where verdict is
    "Undervalued" | "Fair" | "Overvalued" | None (no data).
    """
    fair_rows = FAIR_RANGES_FULL.get(matrix_cell, [])
    if not fair_rows:
        return None, None, None
    disp, key, lo, hi, unit, kind = fair_rows[0]
    v = (val_data or {}).get(key)
    if v is None:
        return None, disp, None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None, disp, None
    if kind in ("yield", "score"):
        verdict = "Fair" if lo <= f <= hi else ("Undervalued" if f > hi else "Overvalued")
    else:  # multiple — lower is cheaper
        verdict = "Fair" if lo <= f <= hi else ("Undervalued" if f < lo else "Overvalued")
    return verdict, disp, f


def get_active_flags(conn, ticker):
    """Synthesized risk flags (the DB has no FLAG_ rows — derive from quality signals).

    Returns list of (flag_name, one_line_description).
    """
    flags = []
    try:
        m = conn.execute(
            "SELECT net_income_quality_flag, is_self_funded FROM computed_metrics WHERE ticker=?",
            (ticker,)).fetchone()
        c = conn.execute(
            "SELECT fh_fcf_hard_cap_applied FROM classifications WHERE ticker=?",
            (ticker,)).fetchone()
        v = conn.execute(
            "SELECT valuation_json FROM valuations WHERE ticker=?", (ticker,)).fetchone()
        if m and m[0]:
            flags.append(("Net income quality",
                          "Reported net income diverges sharply from operating income — P/E-style multiples may mislead."))
        if m and m[1] == 0:
            flags.append(("Not self-funded",
                          "Negative free cash flow — the company depends on external financing."))
        if c and c[0]:
            flags.append(("FCF hard-cap applied",
                          "Deeply negative FCF margin capped the health stage at Stage 2."))
        if v and v[0]:
            _vj = json.loads(v[0])
            if _vj.get("rpo_qualifier") == "forward_demand_decelerating":
                flags.append(("Forward demand decelerating",
                              "RPO growth is trailing revenue growth — forward demand may be softening."))
            if _vj.get("FLAG_peg_value_trap"):
                flags.append(("PEG value trap", _vj["FLAG_peg_value_trap"]))
    except Exception:
        pass
    return flags


# --- SPLASH SCREEN LOGIC ---
def splash_screen():
    # Custom CSS for the Splash Screen
    st.markdown("""
    <style>
        /* Hide default Streamlit elements during splash */
        [data-testid="stSidebar"], [data-testid="stHeader"], [data-testid="stToolbar"] {
            display: none !important;
        }
        
        /* Full Screen Overlay */
        .splash-container {
            position: fixed;
            top: 0;
            left: 0;
            width: 100vw;
            height: 100vh;
            background: linear-gradient(135deg, #0f172a 0%, #1e3a8a 50%, #00b4db 100%);
            z-index: 999999;
            display: flex;
            flex-direction: column;
            justify-content: center;
            align-items: center;
            overflow: hidden;
        }
        
        /* Typewriter Text */
        .typewriter h1 {
            color: #fff;
            font-family: 'Courier New', Courier, monospace;
            overflow: hidden; 
            border-right: .15em solid #fbbf24; /* Orange/Gold cursor */
            white-space: nowrap; 
            margin: 0 auto; 
            letter-spacing: .15em;
            animation: 
                typing 3.5s steps(30, end),
                blink-caret .75s step-end infinite;
            font-size: 4vw;
            font-weight: bold;
            text-shadow: 0 0 15px rgba(0,0,0,0.5);
            z-index: 1000000;
        }
        
        /* Animations */
        @keyframes typing {
            from { width: 0 }
            to { width: 100% }
        }
        
        @keyframes blink-caret {
            from, to { border-color: transparent }
            50% { border-color: #fbbf24 }
        }
        
        /* Ocean Wave Animation */
        .ocean {
            height: 200px;
            width: 100%;
            position: absolute;
            bottom: 0;
            left: 0;
            overflow: hidden;
        }
        
        .wave {
            background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 1440 320'%3E%3Cpath fill='%23ffffff' fill-opacity='0.2' d='M0,192L48,197.3C96,203,192,213,288,229.3C384,245,480,267,576,250.7C672,235,768,181,864,160C960,139,1056,149,1152,165.3C1248,181,1344,203,1392,213.3L1440,224L1440,320L1392,320C1344,320,1248,320,1152,320C1056,320,960,320,864,320C768,320,672,320,576,320C480,320,384,320,288,320C192,320,96,320,48,320L0,320Z'%3E%3C/path%3E%3C/svg%3E");
            background-size: 1440px 200px;
            position: absolute;
            bottom: 0;
            width: 200%;
            height: 100%;
            animation: wave 15s cubic-bezier( 0.36, 0.45, 0.63, 0.53) infinite;
            transform: translate3d(0, 0, 0);
        }
        
        .wave:nth-of-type(2) {
            bottom: 15px;
            opacity: 0.6;
            animation: wave 18s cubic-bezier( 0.36, 0.45, 0.63, 0.53) -.125s infinite, swell 7s ease -1.25s infinite;
        }
        
        @keyframes wave {
            0% { transform: translateX(0); }
            100% { transform: translateX(-50%); } 
        }
        
        @keyframes swell {
            0%, 100% { transform: translateY(-5px); }
            50% { transform: translateY(5px); }
        }

    </style>
    
    <div class="splash-container">
        <div class="typewriter">
            <h1>Valuora</h1>
            <p style="color: #fbbf24; font-family: 'Courier New', monospace; font-size: 1.5vw; margin-top: 10px; opacity: 0.8;">Made by Om</p>
        </div>
        <div class="ocean">
            <div class="wave"></div>
            <div class="wave"></div>
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    # Placeholder for a short loading time
    progress_bar = st.empty()
    time.sleep(4.5) # Let animation play
    progress_bar.empty()
    
    # Update state and rerun to show dashboard
    st.session_state.splash_complete = True
    st.rerun()

# --- HELPER: GOOGLE NEWS RSS FETCHER ---
def fetch_google_news_rss(ticker):
    """
    Fetches recent news from Google News RSS for the given ticker, 
    specifically searching for major financial outlets or general stock news.
    """
    try:
        # Search query: "{ticker} stock" to get broad coverage including major outlets
        url = f"https://news.google.com/rss/search?q={ticker}+stock&hl=en-US&gl=US&ceid=US:en"
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        
        root = ET.fromstring(response.content)
        items = []
        for item in root.findall('.//item'):
            title = item.find('title').text if item.find('title') is not None else 'No Title'
            link = item.find('link').text if item.find('link') is not None else '#'
            pub_date_str = item.find('pubDate').text if item.find('pubDate') is not None else ''
            source = item.find('source').text if item.find('source') is not None else 'Google News'
            
            # Parse Date
            try:
                # RFC 822 format used by RSS (e.g., "Wed, 02 Oct 2024 13:00:00 GMT")
                pub_date = datetime.strptime(pub_date_str, '%a, %d %b %Y %H:%M:%S %Z')
                timestamp = pub_date.timestamp()
            except:
                timestamp = time.time() # Fallback to now
            
            items.append({
                'title': title,
                'link': link,
                'publisher': source,
                'providerPublishTime': timestamp,
                'type': 'RSS'
            })
        return items
    except Exception as e:
        # st.error(f"RSS Fetch Error: {e}") # Debugging
        return []

# --- HELPER: COMPETITOR MAPPING ---
def get_competitors(ticker, info):
    """
    Returns the industry and a list of 5 competitor tickers.
    Priority: (1) CVE DB — same matrix_cell peers; (2) hardcoded sector map; (3) generic ETFs.
    """
    industry = info.get('industry', 'Unknown Industry')
    sector = info.get('sector', 'Unknown Sector')

    # --- CVE matrix_cell peer lookup ---
    try:
        conn_vb = get_db_connection()
        if conn_vb is not None:
            cell_row = conn_vb.execute(
                "SELECT matrix_cell FROM classifications WHERE ticker=?", (ticker,)
            ).fetchone()
            if cell_row:
                matrix_cell = cell_row[0]
                peer_rows = conn_vb.execute(
                    "SELECT ticker FROM classifications WHERE matrix_cell=? AND ticker!=? ORDER BY ticker",
                    (matrix_cell, ticker)
                ).fetchall()
                peers = [r[0] for r in peer_rows]
                if peers:
                    return f"{industry} (CVE: {matrix_cell})", peers[:5]
    except Exception:
        pass  # Fall through to hardcoded map on any DB error

    # Simple hardcoded map for demonstration (expand as needed)
    competitor_map = {
        'Technology': ['MSFT', 'AAPL', 'NVDA', 'GOOGL', 'ORCL'],
        'Financial Services': ['JPM', 'BAC', 'WFC', 'C', 'GS'],
        'Healthcare': ['JNJ', 'PFE', 'LLY', 'MRK', 'ABBV'],
        'Consumer Cyclical': ['AMZN', 'TSLA', 'HD', 'MCD', 'NKE'],
        'Consumer Defensive': ['WMT', 'PG', 'KO', 'PEP', 'COST'],
        'Energy': ['XOM', 'CVX', 'SHEL', 'TTE', 'BP'],
        'Industrials': ['CAT', 'HON', 'UPS', 'GE', 'BA'],
        'Communication Services': ['GOOG', 'META', 'NFLX', 'DIS', 'TMUS']
    }

    # Try sector match first
    comps = competitor_map.get(sector, [])

    # Fallback if sector map fails or empty
    if not comps:
        comps = ['SPY', 'QQQ', 'DIA', 'IWM', 'VTI']

    # Ensure the main ticker isn't in the competitor list (replace if found)
    if ticker in comps:
        comps.remove(ticker)
        if sector == 'Technology': comps.append('ADBE')
        elif sector == 'Financial Services': comps.append('MS')
        else: comps.append('VOO')

    return industry, comps[:5]

# --- HELPER: FETCH COMPARISON DATA ---
def fetch_comparison_data(main_ticker, competitors):
    """
    Fetches P/E, PEG, 1Y Return, 5Y Return for main ticker and competitors.
    Returns a DataFrame.
    """
    tickers = [main_ticker] + competitors
    data = []
    
    # Batch fetch might be faster for some things, but info/history is per ticker object usually in yfinance 
    # (unless using Tickers object which has limits in structure). Iteration is safer for 'info'.
    
    for t in tickers:
        try:
            stock = yf.Ticker(t)
            info = stock.info
            hist = stock.history(period="5y")
            
            # Metrics
            pe = info.get('trailingPE')
            peg = info.get('pegRatio')
            roe = info.get('returnOnEquity')
            ev_ebitda = info.get('enterpriseToEbitda')
            
            # Growth Metrics
            ps = info.get('priceToSalesTrailing12Months')
            ev_rev = info.get('enterpriseToRevenue')
            rev_growth = info.get('revenueGrowth')
            
            # Returns (ROI)
            roi_1y = None
            roi_5y = None
            
            if not hist.empty:
                curr = hist['Close'].iloc[-1]
                # 1 Year (approx 252 trading days)
                if len(hist) > 252:
                    price_1y = hist['Close'].iloc[-252]
                    roi_1y = (curr - price_1y) / price_1y
                
                # 5 Years (approx 1260 trading days)
                if len(hist) > 1250: # Tolerance
                    price_5y = hist['Close'].iloc[0] # Start of 5y period
                    roi_5y = (curr - price_5y) / price_5y
            
            data.append({
                "Ticker": t,
                "P/E": pe if pe else np.nan,
                "PEG": peg if peg else np.nan,
                "ROE": roe if roe else np.nan,
                "EV/EBITDA": ev_ebitda if ev_ebitda else np.nan,
                "P/S": ps if ps else np.nan,
                "EV/Revenue": ev_rev if ev_rev else np.nan,
                "Rev Growth": rev_growth if rev_growth else np.nan,
                "1Y ROI": roi_1y,
                "5Y ROI": roi_5y
            })
        except:
            pass
            
    df = pd.DataFrame(data)
    return df

# --- HELPER: CUSTOM METRIC DISPLAY ---
def display_custom_metric(label, value, prefix="", suffix="", help_text=None, color=None):
    """
    Renders a metric with the label OUTSIDE (above) the glassmorphism box.
    """
    st.markdown(f"<div style='color: white; font-weight: bold; margin-bottom: 5px; font-size: 1.1em;'>{label}</div>", unsafe_allow_html=True)
    if help_text:
        st.caption(help_text)
    
    # Determine color style if provided
    text_color = "white"
    if color == "green": text_color = "#4ade80"
    elif color == "red": text_color = "#f87171"
    elif color == "yellow": text_color = "#facc15"
    elif color == "orange": text_color = "#fb923c"
    
    st.markdown(f"""
    <div style="
        background: rgba(255, 255, 255, 0.05);
        backdrop-filter: blur(10px);
        border: 1px solid rgba(255, 255, 255, 0.1);
        padding: 15px;
        border-radius: 5px;
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
        color: {text_color};
        font-family: 'Courier New', monospace;
        font-size: 1.5em;
        font-weight: bold;
    ">
        {prefix}{value}{suffix}
    </div>
    """, unsafe_allow_html=True)

# --- HELPER: DCF CALCULATOR ---
def calculate_dcf_value(fcf_input, growth_rate, terminal_growth, discount_rate, debt_input, cash_input, shares):
    """
    Calculates Intrinsic Value based on DCF inputs.
    Returns: intrinsic_share_price (float)
    """
    try:
        if fcf_input <= 0:
            return 0.0
            
        future_fcf = []
        for i in range(1, 6):
            fcf = fcf_input * ((1 + growth_rate) ** i)
            future_fcf.append(fcf)
        
        terminal_val = future_fcf[-1] * (1 + terminal_growth) / (discount_rate - terminal_growth)
        
        dcf_value = 0
        for i, cash in enumerate(future_fcf):
            dcf_value += cash / ((1 + discount_rate) ** (i + 1))
        
        pv_terminal = terminal_val / ((1 + discount_rate) ** 5)
        
        # Enterprise Value
        enterprise_value = dcf_value + pv_terminal
        
        # Equity Value = EV - Debt + Cash
        equity_value = enterprise_value - debt_input + cash_input
        
        if not shares: shares = 1
        
        intrinsic_share_price = equity_value / shares
        return intrinsic_share_price
    except:
        return 0.0

# --- HELPER: ROBUST VALUATION FETCHER ---
def get_valuation_data(stock, info):
    """
    Tries to get P/E, EPS, and PEG from 'The Books' (Income Statement) first,
    falling back to Yahoo Finance 'Info' if needed.
    """
    data = {
        'pe': None,
        'pe_source': None,
        'eps': None,
        'eps_source': None,
        'peg': None,
        'peg_source': None,
        'yahoo_pe': info.get('trailingPE'), # For crosscheck
        'yahoo_eps': info.get('trailingEps') # For crosscheck
    }
    
    current_price = info.get('currentPrice')
    
    # 1. Try fetching from Income Statement (The Books)
    try:
        inc = stock.income_stmt
        eps_row = None
        if not inc.empty:
            if "Diluted EPS" in inc.index:
                eps_row = inc.loc["Diluted EPS"]
            elif "Basic EPS" in inc.index:
                eps_row = inc.loc["Basic EPS"]
        
        if eps_row is not None and not eps_row.empty:
            # Latest EPS (TTM roughly approximated by latest annual or just latest annual)
            # Note: Income stmt is usually annual. 'info' trailingEPS is TTM.
            # Ideally we want TTM for P/E. But for "The Books" crosscheck we use what we have.
            # Let's use the latest annual as the "Book Value" proxy for calculation if TTM isn't available in financials.
            # Actually, calculating P/E based on latest Annual EPS is a common simple metric.
            
            latest_annual_eps = float(eps_row.iloc[0])
            data['eps'] = latest_annual_eps
            data['eps_source'] = "Company Filings (Annual)"
            
            if current_price:
                data['pe'] = current_price / latest_annual_eps
                data['pe_source'] = "Calculated from Filings"
                
            # Calculate Growth for PEG (Up to 5 years / 5 periods)
            # eps_row is ordered roughly [Latest, Y-1, Y-2, Y-3, Y-4]
            # We need at least 2 data points for 1 growth period.
            valid_growths = []
            
            # Iterate up to 5 times (comparing i to i+1)
            # e.g., i=0 (Latest) vs i=1 (Y-1) -> Growth 1
            for i in range(min(5, len(eps_row) - 1)):
                try:
                    current_val = float(eps_row.iloc[i])
                    prev_val = float(eps_row.iloc[i+1])
                    
                    # Check for NaNs
                    if np.isnan(current_val) or np.isnan(prev_val):
                        continue

                    # Avoid division by zero or massive spikes from near-zero
                    if prev_val != 0:
                        g = (current_val / prev_val) - 1
                        valid_growths.append(g)
                except:
                    pass
            
            if valid_growths:
                avg_growth = sum(valid_growths) / len(valid_growths)
                
                # We need growth as a percentage (e.g., 0.10 -> 10) for the PEG formula (PEG = PE / Growth_Rate_Percent)
                if avg_growth != 0 and data['pe']:
                    data['peg'] = data['pe'] / (avg_growth * 100)
                    years_used = len(valid_growths)
                    data['peg_source'] = f"Calculated ({years_used}yr Avg Growth)"

    except Exception as e:
        pass

    # 2. Fallbacks (Yahoo Finance)
    if data['eps'] is None:
        data['eps'] = info.get('trailingEps')
        data['eps_source'] = "Yahoo Finance"
    
    if data['pe'] is None:
        data['pe'] = info.get('trailingPE')
        data['pe_source'] = "Yahoo Finance"
        
    if data['peg'] is None:
        data['peg'] = info.get('pegRatio')
        data['peg_source'] = "Yahoo Finance"
        
    return data

def classify_cash_position(stock):
    """
    Diagnoses if a company is 'Cash Stable' or 'Cash Burning'.
    Returns: status (str), runway (months), burn_rate (monthly)
    """
    try:
        cf = stock.cashflow
        bs = stock.balance_sheet
        
        # 1. Get Latest Annual Free Cash Flow (FCF)
        if not cf.empty and 'Free Cash Flow' in cf.index:
            latest_fcf = cf.loc['Free Cash Flow'].iloc[0]
        else:
            latest_fcf = -1 # Fallback to burning if data missing
            
        # 2. Get Total Cash on Hand
        cash_on_hand = bs.loc['Cash And Cash Equivalents'].iloc[0] if 'Cash And Cash Equivalents' in bs.index else 0
        
        # 3. Logic: If FCF is negative, it's a Cash Burner
        if latest_fcf < 0:
            monthly_burn = abs(latest_fcf) / 12
            runway_months = cash_on_hand / monthly_burn if monthly_burn > 0 else 999
            return "Cash Burning", runway_months, monthly_burn
        else:
            return "Cash Stable", None, 0
            
    except:
        return "Unknown", None, 0

# --- 1. THE ROBUST SECRET HELPER ---
def get_av_key():
    try:
        return st.secrets["ALPHA_VANTAGE_KEY"]
    except:
        return None # Graceful fallback

def get_secret(key, default=None):
    """Read a secret from Streamlit secrets (cloud) or env var (local).
    Used by CVE Interpretation engine to fetch GEMINI_API_KEY."""
    import os
    try:
        if key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass
    return os.getenv(key, default)


# --- VALORA INTERPRETATION ENGINE (Gemini-powered structured synthesis) ---
_VALOURA_INTERP_SYSTEM_PROMPT = """You are a financial analyst writing the interpretation section of an
equity research note. You have access to quantitative classification
data, valuation metrics, fair-value ranges, and peer comparisons for
a specific stock.

Your job is to synthesise this data into a structured, factual
interpretation. You must:
- State what the data shows, not what an investor should do
- Compare the ticker to its peer group and fair ranges explicitly
- Identify the one or two most significant signals (positive or negative)
- Flag any tensions or contradictions in the data
- Never use the words "buy", "sell", "overweight", "underweight",
  "recommend", or any equivalent
- Write in plain English, no jargon without explanation
- Maximum 200 words total across all sections"""

_VALOURA_INTERP_OUTPUT_SCHEMA = """Return JSON only, no preamble, no markdown fences. Exact schema:
{
  "classification_summary": "<1-2 sentences: what the classification tells us about this business at this stage>",
  "valuation_position": "<1-2 sentences: where the primary metric sits vs fair range and vs cell peers — use the actual numbers>",
  "strongest_signal": "<1 sentence: the single most notable data point, positive or negative, with the number>",
  "tension": "<1 sentence: any contradiction in the data worth noting, or null if none>",
  "peer_context": "<1 sentence: how this ticker compares to others in the same matrix cell — use peer count and relative position>",
  "data_caveats": "<1 sentence: any active flags or data quality issues that affect interpretation reliability, or null if clean>"
}"""


def _generate_valoura_interpretation(context_dict):
    """Call Gemini to produce a structured interpretation from the context payload.
    Returns (parsed_json_dict, None) on success, or (None, error_message) on failure.
    """
    api_key = get_secret("GEMINI_API_KEY")
    if not api_key:
        return None, "GEMINI_API_KEY not configured"

    try:
        import google.generativeai as genai
    except ImportError:
        return None, "google-generativeai package not installed"

    try:
        genai.configure(api_key=api_key)
        # gemini-2.0-flash deprecated; use 2.5-flash-lite (no-thinking, fast, free-tier friendly).
        # Matches the model used by the data pipeline's BM tiebreaker.
        model = genai.GenerativeModel("gemini-2.5-flash-lite")
        prompt = (
            _VALOURA_INTERP_SYSTEM_PROMPT
            + "\n\nContext payload (JSON):\n"
            + json.dumps(context_dict, indent=2, default=str)
            + "\n\n"
            + _VALOURA_INTERP_OUTPUT_SCHEMA
        )
        resp = model.generate_content(
            prompt,
            generation_config={"max_output_tokens": 400, "temperature": 0.3},
        )
        text = (resp.text or "").strip()
        # Strip code fences if Gemini wraps the JSON
        if text.startswith("```"):
            text = text.strip("`").strip()
            if text.lower().startswith("json"):
                text = text[4:].strip()
            # Remove trailing fence remnants
            if text.endswith("```"):
                text = text[:-3].strip()
        parsed = json.loads(text)
        return parsed, None
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:120]}"

# --- 2. THE SYSTEM HEALTH TRAY (Place in Sidebar) ---
def render_system_health():
    st.sidebar.markdown("---")
    st.sidebar.markdown("### 🛰️ **System Health**")

    # Check Alpha Vantage
    av_key = get_av_key()
    if av_key:
        av_status = st.session_state.get('av_status', "🟢 Online")
    else:
        av_status = "🟡 Demo Mode (No Key)"

    yf_status = st.session_state.get('yf_status', "🟢 Online")

    st.sidebar.write(f"**Alpha Vantage:** {av_status}")
    st.sidebar.write(f"**Yahoo Finance:** {yf_status}")

    if not av_key:
        st.sidebar.warning("Add 'ALPHA_VANTAGE_KEY' to Streamlit Secrets for live macro data.")

# --- 3. THE HYBRID FETCH LOGIC ---
@st.cache_data(ttl=3600)
def fetch_macro_command_center():
    """
    Hybrid: Current prices via Alpha Vantage (Stable),
    History via yfinance (Deep).
    """
    av_key = get_av_key()
    macro_data = {"oil": 92.50, "spx": 5100, "gold": 2150} # Default/Demo Values

    # A. Current 'Heartbeat' via Alpha Vantage
    if av_key:
        try:
            # Only hit Alpha for the most critical triggers
            oil_url = f'https://www.alphavantage.co/query?function=WTI&interval=daily&apikey={av_key}'
            r = requests.get(oil_url).json()
            macro_data["oil"] = float(r['data'][0]['value'])
            st.session_state.av_status = "🟢 Online"
        except:
            st.session_state.av_status = "🔴 Rate Limited"

    # B. Historical Patterns via yfinance
    current_prices, hist_macro = fetch_macro_context() # Your existing function

    # Overwrite Yahoo's Oil/SPX with Alpha's 'Clean' data if available
    current_prices["Crude Oil (WTI)"] = macro_data["oil"]

    return current_prices, hist_macro

def fetch_macro_context():
    """
    Fetches 60 days of historical data for key global macro indicators
    and returns both the latest prices and the historical DataFrame.
    """
    tickers = {
        "Crude Oil (WTI)": "CL=F",
        "Gold": "GC=F",
        "Copper": "HG=F",
        "10Y Treasury Yield": "^TNX",
        "S&P 500": "^GSPC",
        "NASDAQ": "^IXIC",
        "Hang Seng": "^HSI"
    }
    try:
        data = yf.download(list(tickers.values()), period="60d")['Close']
        inv_map = {v: k for k, v in tickers.items()}
        data = data.rename(columns=inv_map)

        # Latest prices for the ticker tape
        latest_data = {}
        for name in tickers.keys():
            if name in data.columns:
                latest_data[name] = data[name].dropna().iloc[-1] if not data[name].dropna().empty else None
            else:
                latest_data[name] = None

        return latest_data, data
    except Exception:
        return {}, pd.DataFrame()

# --- NEW HELPER FOR GEOPOLITICAL NEWS ---
def get_ai_geopol_summary(location, news_items):
    """Generates a contextual AI summary based on headlines."""
    if not news_items:
        return "No recent intelligence gathered for this sector."

    # In a production app, you'd send this to an LLM API.
    # For the 'Vibe Code' version, we create a Smart Template Summary:
    headlines = [n['title'] for n in news_items[:3]]
    summary = f"**Intelligence Report:** {location} is currently seeing high volatility. "
    summary += f"Key developments include: '{headlines[0]}'. "
    summary += "CVE predicts continued pressure on global shipping rates if this trend persists."
    return summary

# --- ALPHA VANTAGE FALLBACK (for cloud deployments where Yahoo IP-blocks) ---
class _AVStockProxy:
    """Mimics yf.Ticker — exposes .info, .history(), .news for downstream code."""
    def __init__(self, ticker, info_dict, hist_df):
        self.ticker = ticker
        self.info   = info_dict
        self._hist  = hist_df
        self.news   = []

    def history(self, period="1y", interval="1d", **kwargs):
        if self._hist is None or self._hist.empty:
            return self._hist
        if period in (None, "max"):
            return self._hist
        days_map = {
            "1d": 1, "5d": 5, "1mo": 30, "3mo": 90, "6mo": 180,
            "1y": 365, "2y": 730, "5y": 1825, "10y": 3650, "ytd": 365,
        }
        days = days_map.get(period, 365)
        from datetime import timedelta
        cutoff = self._hist.index.max() - timedelta(days=days)
        return self._hist[self._hist.index >= cutoff]


def _fetch_av_stock(ticker_symbol):
    """Build a yf-compatible (stock, info) tuple from Alpha Vantage OVERVIEW + TIME_SERIES_DAILY."""
    av_key = get_av_key()
    if not av_key:
        return None, None

    def _safe_f(v):
        try:
            if v in (None, "None", "-", "", "NaN"):
                return None
            return float(v)
        except (ValueError, TypeError):
            return None

    try:
        # OVERVIEW → fundamentals + descriptive
        ov_url = f"https://www.alphavantage.co/query?function=OVERVIEW&symbol={ticker_symbol}&apikey={av_key}"
        ov = requests.get(ov_url, timeout=15).json()
        if not isinstance(ov, dict) or "Symbol" not in ov:
            note = ov.get("Information") or ov.get("Note") or "unknown ticker / rate limit" if isinstance(ov, dict) else "bad response"
            st.warning(f"Alpha Vantage OVERVIEW failed for {ticker_symbol}: {str(note)[:140]}")
            return None, None

        # TIME_SERIES_DAILY → price history
        ts_url = f"https://www.alphavantage.co/query?function=TIME_SERIES_DAILY&symbol={ticker_symbol}&outputsize=full&apikey={av_key}"
        ts_data = requests.get(ts_url, timeout=15).json()
        ts_series = ts_data.get("Time Series (Daily)", {}) if isinstance(ts_data, dict) else {}

        if ts_series:
            df = pd.DataFrame.from_dict(ts_series, orient="index")
            df.columns = ["Open", "High", "Low", "Close", "Volume"]
            df = df.astype(float)
            df.index = pd.to_datetime(df.index)
            df = df.sort_index()
            df["Adj Close"] = df["Close"]
        else:
            df = pd.DataFrame()

        info = {
            "symbol":                ov.get("Symbol"),
            "shortName":             ov.get("Name"),
            "longName":              ov.get("Name"),
            "longBusinessSummary":   ov.get("Description") or "",
            "sector":                ov.get("Sector"),
            "industry":              ov.get("Industry"),
            "country":               ov.get("Country"),
            "city":                  ov.get("Address"),
            "website":               ov.get("OfficialSite"),
            "currency":              ov.get("Currency"),
            "exchange":              ov.get("Exchange"),
            "marketCap":             _safe_f(ov.get("MarketCapitalization")),
            "enterpriseValue":       None,
            "trailingPE":            _safe_f(ov.get("PERatio")),
            "forwardPE":             _safe_f(ov.get("ForwardPE")),
            "pegRatio":              _safe_f(ov.get("PEGRatio")),
            "priceToBook":           _safe_f(ov.get("PriceToBookRatio")),
            "priceToSalesTrailing12Months": _safe_f(ov.get("PriceToSalesRatioTTM")),
            "beta":                  _safe_f(ov.get("Beta")),
            "sharesOutstanding":     _safe_f(ov.get("SharesOutstanding")),
            "dividendYield":         _safe_f(ov.get("DividendYield")),
            "dividendRate":          _safe_f(ov.get("DividendPerShare")),
            "fiftyTwoWeekHigh":      _safe_f(ov.get("52WeekHigh")),
            "fiftyTwoWeekLow":       _safe_f(ov.get("52WeekLow")),
            "fiftyDayAverage":       _safe_f(ov.get("50DayMovingAverage")),
            "twoHundredDayAverage":  _safe_f(ov.get("200DayMovingAverage")),
            "targetMeanPrice":       _safe_f(ov.get("AnalystTargetPrice")),
            "ebitda":                _safe_f(ov.get("EBITDA")),
            "profitMargins":         _safe_f(ov.get("ProfitMargin")),
            "operatingMargins":      _safe_f(ov.get("OperatingMarginTTM")),
            "returnOnAssets":        _safe_f(ov.get("ReturnOnAssetsTTM")),
            "returnOnEquity":        _safe_f(ov.get("ReturnOnEquityTTM")),
            "trailingEps":           _safe_f(ov.get("EPS")),
            "revenuePerShare":       _safe_f(ov.get("RevenuePerShareTTM")),
            "regularMarketPrice":    float(df["Close"].iloc[-1]) if not df.empty else None,
            "previousClose":         float(df["Close"].iloc[-2]) if len(df) >= 2 else None,
            "fullTimeEmployees":     int(_safe_f(ov.get("FullTimeEmployees"))) if _safe_f(ov.get("FullTimeEmployees")) else None,
            "_data_source":          "alpha_vantage",
        }

        stock = _AVStockProxy(ticker_symbol, info, df)
        return stock, info

    except Exception as e:
        st.warning(f"Alpha Vantage fallback exception for {ticker_symbol}: {e}")
        return None, None


# --- ROBUST 3-TIER DATA FETCHER (yfinance → yahooquery → Alpha Vantage) ---
@st.cache_resource(ttl=3600)
def fetch_stock_data_v2(ticker_symbol):
    """
    3-tier hybrid:
      1. yfinance (best on residential IPs)
      2. yahooquery (different endpoints — sometimes survives Yahoo throttle)
      3. Alpha Vantage (always works on cloud IPs — requires ALPHA_VANTAGE_KEY secret)
    """
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
    })

    # --- ATTEMPT 1: yfinance ---
    try:
        stock = yf.Ticker(ticker_symbol)
        hist  = stock.history(period="1d")
        if not hist.empty and stock.info and stock.info.get("symbol"):
            st.session_state['yf_status'] = "🟢 Online"
            return stock, stock.info
    except Exception:
        pass

    # --- ATTEMPT 2: yahooquery ---
    try:
        st.toast(f"yfinance blocked — trying yahooquery for {ticker_symbol}...")
        yq      = YQTicker(ticker_symbol, session=session)
        modules = yq.all_modules
        if isinstance(modules, dict) and ticker_symbol in modules and isinstance(modules[ticker_symbol], dict):
            st.session_state['yf_status'] = "🟡 yahooquery fallback"
            return yf.Ticker(ticker_symbol), modules[ticker_symbol]
    except Exception:
        pass

    # --- ATTEMPT 3: Alpha Vantage (cloud-friendly) ---
    st.toast(f"Yahoo blocked — falling back to Alpha Vantage for {ticker_symbol}...")
    av_stock, av_info = _fetch_av_stock(ticker_symbol)
    if av_stock is not None and av_info is not None:
        st.session_state['yf_status'] = "🔵 Alpha Vantage"
        return av_stock, av_info

    st.session_state['yf_status'] = "🔴 All sources blocked"
    return None, None


# ==========================================================================
# AUTH — login / signup pages (Supabase Auth, email + password)
# ==========================================================================
_AUTH_CSS = """
<style>
    .stApp {
        background: linear-gradient(160deg, #000000 0%, #050d1a 40%, #0a1628 75%, #0f2040 100%);
        color: #ffffff;
    }
    [data-testid="stSidebar"], [data-testid="stSidebarCollapseButton"],
    [data-testid="collapsedControl"] { display: none !important; }
    .main .block-container { padding-top: 2.5rem !important; max-width: 480px !important; }
    .auth-brand {
        font-family: 'Times New Roman', Times, serif;
        font-size: 1.6em; font-weight: 700; color: #ffffff;
        letter-spacing: 2px; text-transform: uppercase; text-align: center;
        padding: 18px 0 4px 0;
    }
    .auth-sub { color: #7dd3fc; text-align: center; font-size: 0.9em;
                margin-bottom: 18px; font-style: italic; }
    .stTextInput input {
        color: #FFFFFF !important; background-color: #0a1628 !important;
        border: 1px solid rgba(56,189,248,0.25) !important;
        -webkit-text-fill-color: #FFFFFF !important;
    }
    .stMarkdown p { color: #e2e8f0 !important; }
    h1,h2,h3 { color: #ffffff !important; font-family: 'Times New Roman', Times, serif; }
    div[data-testid="stButton"] > button[kind="primary"] {
        background: linear-gradient(135deg, #0ea5e9 0%, #0284c7 50%, #0369a1 100%) !important;
        color: #ffffff !important; border: 1px solid rgba(125,211,252,0.6) !important;
        border-radius: 8px !important; font-weight: 700 !important; width: 100%;
    }
    div[data-testid="stButton"] > button[kind="secondary"] {
        background: rgba(255,255,255,0.06) !important; color: #cbd5e1 !important;
        border: 1px solid rgba(255,255,255,0.14) !important; border-radius: 8px !important;
        width: 100%;
    }
</style>
"""


def render_auth_page():
    """Login / signup wall. Sets st.session_state.user on success."""
    from supabase_client import sign_in, sign_up

    st.markdown(_AUTH_CSS, unsafe_allow_html=True)
    st.markdown('<div class="auth-brand">Contextual Valuation Engine</div>', unsafe_allow_html=True)
    st.markdown('<div class="auth-sub">Stage-aware valuation for technology stocks</div>', unsafe_allow_html=True)

    if 'auth_mode' not in st.session_state:
        st.session_state.auth_mode = "login"

    if st.session_state.auth_mode == "login":
        st.subheader("Log in")
        with st.form("login_form"):
            email = st.text_input("Email", placeholder="you@example.com")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Log in", type="primary", use_container_width=True)
        if submitted:
            if not email or not password:
                st.error("Enter both email and password.")
            else:
                user, err = sign_in(email.strip(), password)
                if user:
                    st.session_state.user = user
                    st.session_state.pop("auth_view", None)
                    st.session_state.active_page = "Home"
                    st.rerun()
                else:
                    st.error(err)
        st.markdown("---")
        if st.button("New here? Create an account", use_container_width=True):
            st.session_state.auth_mode = "signup"
            st.rerun()
        if st.button("← Continue browsing without an account", use_container_width=True,
                     key="auth_browse_login"):
            st.session_state.pop("auth_view", None)
            st.rerun()

    else:  # signup
        st.subheader("Create account")
        with st.form("signup_form"):
            display_name = st.text_input("Display name", placeholder="How should we greet you?")
            email = st.text_input("Email", placeholder="you@example.com")
            password = st.text_input("Password", type="password",
                                     help="At least 6 characters")
            confirm = st.text_input("Confirm password", type="password")
            submitted = st.form_submit_button("Sign up", type="primary", use_container_width=True)
        if submitted:
            if not (display_name and email and password and confirm):
                st.error("All fields are required.")
            elif password != confirm:
                st.error("Passwords do not match.")
            elif len(password) < 6:
                st.error("Password must be at least 6 characters.")
            else:
                user, err = sign_up(email.strip(), password, display_name.strip())
                if user:
                    st.session_state.user = user
                    st.session_state.pop("auth_view", None)
                    st.session_state.active_page = "Home"
                    st.rerun()
                else:
                    # err may be the "confirm your email" info, not a failure
                    if err and err.startswith("Account created"):
                        st.info(err)
                    else:
                        st.error(err)
        st.markdown("---")
        if st.button("Already have an account? Log in", use_container_width=True):
            st.session_state.auth_mode = "login"
            st.rerun()
        if st.button("← Continue browsing without an account", use_container_width=True,
                     key="auth_browse_signup"):
            st.session_state.pop("auth_view", None)
            st.rerun()


# ==========================================================================
# HOME (landing) + WATCHLIST pages
# ==========================================================================

def _classification_lookup(conn, tickers=None):
    """Rows of (ticker, matrix_cell, fh_stage, classified_at, primary_method,
    valuation_json) from SQLite — for all classified tickers or a subset."""
    if conn is None:
        return []
    q = ("SELECT c.ticker, c.matrix_cell, c.fh_stage, c.classified_at, "
         "v.primary_method, v.valuation_json "
         "FROM classifications c LEFT JOIN valuations v ON c.ticker = v.ticker ")
    try:
        if tickers:
            marks = ",".join("?" for _ in tickers)
            return conn.execute(q + f"WHERE c.ticker IN ({marks}) ORDER BY c.ticker",
                                tuple(tickers)).fetchall()
        return conn.execute(q + "ORDER BY c.ticker").fetchall()
    except Exception:
        return []


def _fmt_primary(verdict_tuple):
    """'EV/EBITDA 30.45' style string from derive_verdict output."""
    _, disp, val = verdict_tuple
    if disp is None:
        return "—"
    if val is None:
        return f"{disp} —"
    return f"{disp} {val:,.2f}"


def render_classified_universe(conn):
    """Classified-ticker grid grouped by business model, pills coloured by
    FH stage. Shared by the Home and About pages."""
    st.subheader("Classified Universe")
    st.caption("Tickers currently covered by the CVE classification engine, grouped by business model and financial health stage.")

    if conn is None:
        st.info("Database not connected. Ensure `valoura_backtest.db` is in the app directory.")
        return

    _grid_rows = conn.execute(
        "SELECT ticker, matrix_cell, bm_category, fh_stage "
        "FROM classifications ORDER BY bm_category ASC, fh_stage ASC, ticker ASC"
    ).fetchall()
    if not _grid_rows:
        st.info("No tickers classified yet. Run the pipeline to populate the database.")
        return

    _by_bm = {}
    for _t, _cell, _bm, _fh in _grid_rows:
        _by_bm.setdefault(_bm, []).append((_t, _cell, _fh))

    _BM_LABELS = {
        "hyperscale":        "Hyperscale",
        "saas":              "SaaS",
        "semi_hardware":     "Semi / Hardware",
        "consumer_internet": "Consumer Internet",
        "deep_tech":         "Deep Tech",
    }
    _STAGE_COLOURS = {1: "#ef4444", 2: "#f59e0b", 3: "#3b82f6", 4: "#22c55e"}
    _STAGE_LABELS = {1: "Stage 1", 2: "Stage 2", 3: "Stage 3", 4: "Stage 4"}

    for _bm_key in ["hyperscale", "saas", "semi_hardware", "consumer_internet", "deep_tech"]:
        if _bm_key not in _by_bm:
            continue
        _tickers_in_bm = _by_bm[_bm_key]
        _label = _BM_LABELS.get(_bm_key, _bm_key.title())
        st.markdown(
            f"<p style='color:#7dd3fc;font-size:1em;font-weight:700;"
            f"letter-spacing:1px;text-transform:uppercase;margin:18px 0 8px 0;"
            f"padding-bottom:4px;border-bottom:1px solid rgba(56,189,248,0.2);'>"
            f"{_label} — {len(_tickers_in_bm)} tickers</p>",
            unsafe_allow_html=True,
        )
        _pills_html = '<div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:4px;">'
        for _tick, _cell, _fh in _tickers_in_bm:
            _col = _STAGE_COLOURS.get(_fh, "#64748b")
            _pills_html += (
                f'<div title="{_cell}" style="'
                f'background:{_col}22;border:1px solid {_col}88;'
                f'border-radius:6px;padding:5px 12px;display:inline-block;">'
                f'<span style="color:#f1f5f9;font-weight:700;font-size:0.88em;">{_tick}</span>'
                f'<span style="color:{_col};font-size:0.72em;margin-left:6px;">{_cell.split("-")[1] if "-" in _cell else ""}</span>'
                f'</div>'
            )
        _pills_html += '</div>'
        st.markdown(_pills_html, unsafe_allow_html=True)

    st.markdown(
        "<div style='margin-top:16px;display:flex;gap:20px;flex-wrap:wrap;'>"
        + "".join(
            f'<span style="font-size:0.82em;color:#94a3b8;">'
            f'<span style="display:inline-block;width:10px;height:10px;border-radius:2px;'
            f'background:{_STAGE_COLOURS[s]};margin-right:4px;"></span>{_STAGE_LABELS[s]}</span>'
            for s in [1, 2, 3, 4]
        )
        + "</div>",
        unsafe_allow_html=True,
    )
    st.caption(f"Total: {len(_grid_rows)} classified tickers across {len(_by_bm)} business model categories.")


def render_home_page(user):
    """Landing page: watchlist status board, classified universe, recently moved."""
    from supabase_client import get_stage_changes_for_tickers, get_recent_stage_changes

    conn = get_db_connection()
    _logged_in = bool(user.get("id"))
    if _logged_in:
        display_name = user.get("display_name") or "there"
        st.markdown(f"<div class='fun-header'>Welcome back, {display_name}</div>",
                    unsafe_allow_html=True)
    else:
        st.markdown("<div class='fun-header'>Contextual Valuation Engine</div>",
                    unsafe_allow_html=True)

    # ── Section 1: Watchlist status board ────────────────────────────────
    st.subheader("Your Watchlist")
    if not _logged_in:
        st.info("Create an account to build a watchlist and get stage-change alerts.")
        if st.button("👤 Log in / Sign up", key="home_login_prompt", type="primary"):
            st.session_state.auth_view = True
            st.rerun()
    else:
        wl_rows = st.session_state.get("_watchlist_cache", [])
        if not wl_rows:
            st.info("Add tickers to your watchlist from the analysis page.")
        else:
            wl_tickers = [r["ticker"] for r in wl_rows]
            cls_rows = {r[0]: r for r in _classification_lookup(conn, wl_tickers)}
            changed_recently = get_stage_changes_for_tickers(wl_tickers, days=7)

            headers = ["Ticker", "Matrix Cell", "Primary Metric",
                       "Stage", "Last Updated", "Stage Changed"]
            rows = []
            for t in wl_tickers:
                c = cls_rows.get(t)
                if c is None:
                    rows.append([t, "not classified", "—", "—", "—", "—"])
                    continue
                _, cell, stage, classified_at, _pm, vjson = c
                try:
                    vdata = json.loads(vjson) if vjson else {}
                except Exception:
                    vdata = {}
                vt = derive_verdict(cell, vdata)
                changed = t in changed_recently
                changed_cell = ('<span style="color:#f59e0b;font-weight:800;">YES</span>'
                                if changed else '<span style="color:#64748b;">—</span>')
                rows.append([
                    t, cell, _fmt_primary(vt),
                    f"Stage {stage}", (classified_at or "")[:10], changed_cell,
                ])
            render_dark_table(headers, rows)
            if changed_recently:
                st.caption("Amber **YES** = the financial health stage changed in the last pipeline run.")

    st.markdown("---")

    # ── Section 2: Classified universe (by business model + stage) ───────
    render_classified_universe(conn)

    st.markdown("---")

    # ── Section 3: Recently moved ────────────────────────────────────────
    st.subheader("Recently Moved")
    st.caption("Stage transitions detected by the pipeline in the last 30 days.")
    changes = get_recent_stage_changes(days=30, limit=10)
    if not changes:
        st.write("No stage changes recorded in the last 30 days.")
    else:
        rows = []
        for ch in changes:
            _arrow_colour = "#22c55e" if (ch.get("new_stage") or 0) > (ch.get("previous_stage") or 0) else "#ef4444"
            rows.append([
                ch.get("ticker", "—"),
                f'Stage {ch.get("previous_stage", "?")} '
                f'<span style="color:{_arrow_colour};font-weight:800;">→</span> '
                f'Stage {ch.get("new_stage", "?")}',
                (ch.get("changed_at") or "")[:10],
                ch.get("matrix_cell") or "—",
            ])
        render_dark_table(["Ticker", "Transition", "Date", "Matrix Cell"], rows)


def render_watchlist_page(user):
    """Dedicated watchlist page — full KPI detail + per-row remove."""
    from supabase_client import remove_from_watchlist

    conn = get_db_connection()
    st.markdown("<div class='fun-header'>Watchlist</div>", unsafe_allow_html=True)

    if not user.get("id"):
        st.info("Create an account to build a watchlist and get stage-change alerts.")
        if st.button("👤 Log in / Sign up", key="wl_page_login", type="primary"):
            st.session_state.auth_view = True
            st.rerun()
        return

    wl_rows = st.session_state.get("_watchlist_cache", [])
    if not wl_rows:
        st.info("Your watchlist is empty. Add tickers from the analysis page.")
        return

    # Pipeline freshness note
    try:
        last_run = conn.execute("SELECT MAX(classified_at) FROM classifications").fetchone()[0]
        st.caption(f"Classifications refresh when the pipeline runs — last run: "
                   f"**{(last_run or 'unknown')[:16].replace('T', ' ')}**")
    except Exception:
        pass

    wl_tickers = [r["ticker"] for r in wl_rows]
    cls_rows = {r[0]: r for r in _classification_lookup(conn, wl_tickers)}
    kpis = {}
    try:
        marks = ",".join("?" for _ in wl_tickers)
        for r in conn.execute(
            f"SELECT ticker, fcf_margin_adj, gross_margin, revenue_growth_yoy, "
            f"operating_leverage FROM computed_metrics WHERE ticker IN ({marks})",
            tuple(wl_tickers)).fetchall():
            kpis[r[0]] = r[1:]
    except Exception:
        pass

    def _n(v, nd=1):
        return "—" if v is None else f"{v:,.{nd}f}"

    headers = ["Ticker", "Matrix Cell", "Stage", "FCF Margin adj (%)",
               "Gross Margin (%)", "Rev Growth YoY (%)", "Op Leverage (pp)",
               "Primary Metric", "Alert"]
    rows = []
    for r in wl_rows:
        t = r["ticker"]
        c = cls_rows.get(t)
        k = kpis.get(t, (None, None, None, None))
        alert = "🔔" if r.get("alert_on_stage_change") else "—"
        if c is None:
            rows.append([t, "not classified", "—", "—", "—", "—", "—", "—", alert])
            continue
        _, cell, stage, _ca, _pm, vjson = c
        try:
            vdata = json.loads(vjson) if vjson else {}
        except Exception:
            vdata = {}
        vt = derive_verdict(cell, vdata)
        rows.append([
            t, cell, f"Stage {stage}",
            _n(k[0]), _n(k[1]), _n(k[2]), _n(k[3]),
            _fmt_primary(vt), alert,
        ])
    render_dark_table(headers, rows)

    # Per-row remove buttons (grid, 4 per row)
    st.markdown("**Remove from watchlist:**")
    _uid = user.get("id")
    for _start in range(0, len(wl_tickers), 4):
        _chunk = wl_tickers[_start:_start + 4]
        _cols = st.columns(4)
        for _j, _t in enumerate(_chunk):
            with _cols[_j]:
                if st.button(f"Remove {_t}", key=f"wl_rm_{_t}", use_container_width=True):
                    remove_from_watchlist(_uid, _t)
                    st.rerun()


# --- MAIN DASHBOARD LOGIC (Original Code Wrapped) ---
def main_dashboard():
    # --- CUSTOM CSS: Ocean Blue Theme & Fun Graphics ---
    # --- SURGICAL CSS FOR SIDEBAR NAVIGATION ---
    st.markdown("""
    <style>
        /* --- Formal Font Stack --- */
        html, body, [class*="css"] {
            font-family: 'Times New Roman', Times, serif;
        }
        
        /* --- Main App Background — deep black with cold-blue gradient --- */
        .stApp {
            background: linear-gradient(160deg, #000000 0%, #050d1a 40%, #0a1628 75%, #0f2040 100%);
            color: #ffffff;
        }
        
        /* --- Sidebar Styling (High Visibility) --- */
        [data-testid="stSidebar"] {
            background-color: #f0f2f5; 
            border-right: 2px solid #1c2541;
        }

        /* 1. Target the labels of the Radio Buttons in the Sidebar specifically */
        [data-testid="stSidebar"] .st-emotion-cache-6qob1r, 
        [data-testid="stSidebar"] .st-emotion-cache-17l69k,
        [data-testid="stSidebar"] [data-testid="stWidgetLabel"] p {
            color: #000000 !important;
            font-weight: 700 !important;
        }

        /* 2. Target the text of the actual Radio options (the navigation links) */
        [data-testid="stSidebar"] div[role="radiogroup"] label p {
            color: #000000 !important;
            font-size: 1.1em !important;
        }

        /* 3. Ensure the Sidebar header remains black as well */
        [data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3 {
            color: #000000 !important;
        }

        /* 4. Keep the main area Metrics and Titles as White */
        [data-testid="stMetricLabel"], [data-testid="stMetricValue"], .fun-header {
            color: #FFFFFF !important;
        }

        /* Fix for the 'Blacked Out' Input Boxes */
        .stTextInput input {
            color: #FFFFFF !important;
            background-color: #1c2541 !important;
            -webkit-text-fill-color: #FFFFFF !important;
        }

        /* Force standard markdown text in containers to White */
        .stMarkdown p, .stMarkdown div {
            color: #FFFFFF !important;
        }

        /* Ensure Tab titles are visible */
        button[data-baseweb="tab"] p {
            color: #cbd5e1 !important;
        }
        button[aria-selected="true"] p {
            color: #FFFFFF !important;
        }
        
        /* Glassmorphism Metrics Cards */
        div[data-testid="stMetric"] {
            background: rgba(255, 255, 255, 0.05);
            backdrop-filter: blur(10px);
            border: 1px solid rgba(255, 255, 255, 0.1);
            padding: 15px;
            border-radius: 5px;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
            transition: transform 0.2s;
        }
        div[data-testid="stMetric"]:hover {
            transform: translateY(-2px);
            border-color: rgba(56,189,248,0.45);
            box-shadow: 0 0 12px rgba(14,165,233,0.2);
        }

        /* --- Formal Header --- */
        .fun-header {
            font-size: 3em;
            font-weight: 800;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.5);
            font-family: 'Times New Roman', Times, serif;
            margin-bottom: 20px;
            border-bottom: 1px solid rgba(56,189,248,0.25);
            padding-bottom: 10px;
        }

        /* --- Hide sidebar entirely (replaced by horizontal nav) --- */
        [data-testid="stSidebar"],
        [data-testid="stSidebarCollapseButton"],
        [data-testid="collapsedControl"],
        button[kind="header"][aria-label*="sidebar" i] {
            display: none !important;
            width: 0 !important;
            visibility: hidden !important;
        }
        section[data-testid="stSidebar"] + section > div.block-container,
        .main .block-container {
            padding-top: 0.25rem !important;
            max-width: 100% !important;
        }

        /* --- Horizontal navigation bar — black-to-cold-navy gradient --- */
        .valoura-topbar {
            background: linear-gradient(135deg, #000000 0%, #050d1a 35%, #0a1a35 70%, #0f2855 100%);
            border-radius: 10px;
            padding: 12px 20px 10px 20px;
            margin: 0 0 12px 0;
            text-align: center;
            box-shadow: 0 2px 20px rgba(0,180,219,0.12), 0 0 0 1px rgba(56,189,248,0.1);
            border: 1px solid rgba(56,189,248,0.15);
        }
        .valoura-brand {
            font-family: 'Times New Roman', Times, serif;
            font-size: 1.4em;
            font-weight: 700;
            color: #ffffff;
            letter-spacing: 2px;
            text-transform: uppercase;
            margin-bottom: 0;
        }
        .valoura-tagline {
            display: none;
        }
        /* --- Help tooltip icon (next to st.metric label) — make it visible --- */
        [data-testid="stTooltipIcon"],
        [data-testid="stTooltipHoverTarget"],
        .stTooltipIcon,
        button[kind="tooltipIcon"] {
            color: #ffffff !important;
            opacity: 1 !important;
        }
        [data-testid="stTooltipIcon"] svg,
        [data-testid="stTooltipHoverTarget"] svg,
        .stTooltipIcon svg,
        button[kind="tooltipIcon"] svg,
        [data-testid="stMetricLabel"] svg {
            color: #ffffff !important;
            fill: #ffffff !important;
            opacity: 1 !important;
            stroke: #ffffff !important;
        }
        /* Tooltip popup itself — make sure markdown lists render with visible bullets */
        [data-baseweb="tooltip"] ul,
        [role="tooltip"] ul {
            padding-left: 18px !important;
            margin: 4px 0 !important;
        }
        [data-baseweb="tooltip"] li,
        [role="tooltip"] li {
            margin: 4px 0 !important;
            line-height: 1.45 !important;
        }

        /* --- Nav pills rendered as st.buttons (AJAX rerun, no full reload) --- */
        /* Inactive (secondary) — translucent dim pill */
        div[data-testid="stButton"] > button[kind="secondary"],
        button[data-testid="stBaseButton-secondary"] {
            background: rgba(255,255,255,0.06) !important;
            color: #cbd5e1 !important;
            border: 1px solid rgba(255,255,255,0.14) !important;
            border-radius: 999px !important;
            font-family: 'Times New Roman', Times, serif !important;
            font-weight: 600 !important;
            font-size: 0.95em !important;
            padding: 8px 18px !important;
            transition: all 0.15s ease-in-out !important;
        }
        div[data-testid="stButton"] > button[kind="secondary"]:hover,
        button[data-testid="stBaseButton-secondary"]:hover {
            background: rgba(56,189,248,0.14) !important;
            color: #bae6fd !important;
            border-color: rgba(56,189,248,0.4) !important;
        }
        /* Active (primary) — ice-blue gradient pill */
        div[data-testid="stButton"] > button[kind="primary"],
        button[data-testid="stBaseButton-primary"] {
            background: linear-gradient(135deg, #0ea5e9 0%, #0284c7 50%, #0369a1 100%) !important;
            color: #ffffff !important;
            border: 1px solid rgba(125,211,252,0.6) !important;
            border-radius: 999px !important;
            font-family: 'Times New Roman', Times, serif !important;
            font-weight: 700 !important;
            font-size: 0.95em !important;
            padding: 8px 18px !important;
            box-shadow: 0 2px 14px rgba(14,165,233,0.35) !important;
        }
        div[data-testid="stButton"] > button[kind="primary"]:hover,
        button[data-testid="stBaseButton-primary"]:hover {
            background: linear-gradient(135deg, #38bdf8 0%, #0ea5e9 100%) !important;
            color: #ffffff !important;
            box-shadow: 0 2px 18px rgba(56,189,248,0.5) !important;
        }
        
        /* --- Headers --- */
        h1, h2, h3, h4, h5, h6 {
            color: #ffffff !important;
            font-family: 'Times New Roman', Times, serif;
            font-weight: 600;
        }
        
        /* --- General Text Readability (Main Area) --- */
        .main p, .main span, .main div {
            color: #e0e6ed;
        }
        
        /* --- Slider and Number Input Label Styling (White Text) --- */
        .stSlider label, .stNumberInput label {
            color: #ffffff !important;
        }
        .stSlider [data-testid="stMarkdownContainer"] p, .stNumberInput [data-testid="stMarkdownContainer"] p {
             color: #ffffff !important;
        }
        
        /* --- Tabs --- */
        .stTabs [data-baseweb="tab-list"] {
            gap: 8px;
        }
        .stTabs [data-baseweb="tab"] {
            height: 50px;
            background-color: rgba(11, 19, 43, 0.6);
            border: 1px solid #3a506b;
            border-radius: 4px;
            color: #cbd5e1;
            transition: all 0.3s;
            font-family: 'Helvetica Neue', sans-serif;
        }
        .stTabs [aria-selected="true"] {
            background: #3a506b !important;
            color: white !important;
            border: 1px solid #6fffe9;
            box-shadow: 0 0 10px rgba(111, 255, 233, 0.3);
        }

        /* --- Centered + spread-across tab layout (applies app-wide) --- */
        .stTabs [data-baseweb="tab-list"] {
            justify-content: space-around !important;
            width: 100% !important;
        }
        .stTabs [data-baseweb="tab"] {
            flex: 1 !important;
            text-align: center !important;
        }

        /* --- Stage-Based Analysis nav button: ice-blue outline when inactive --- */
        button[data-sba="true"] {
            border: 1.5px solid rgba(125,211,252,0.7) !important;
            box-shadow: 0 0 8px rgba(14,165,233,0.25) !important;
        }

        /* --- BM-tab "assigned category" highlight (ice-blue underline) --- */
        .stTabs [data-baseweb="tab"][data-valoura-assigned="true"] {
            border-bottom: 3px solid #38bdf8 !important;
        }
        .stTabs [data-baseweb="tab"][data-valoura-assigned="true"] p {
            color: #bae6fd !important;
            font-weight: 700 !important;
        }

        /* --- Timeline Styling --- */
        .timeline-item {
            border-left: 3px solid #6fffe9;
            padding-left: 20px;
            margin-bottom: 25px;
            position: relative;
        }
        .timeline-dot {
            width: 12px;
            height: 12px;
            background-color: #6fffe9;
            border: 2px solid #0b132b;
            border-radius: 50%;
            position: absolute;
            left: -7px;
            top: 5px;
            box-shadow: 0 0 5px #6fffe9;
        }
        .timeline-date {
            font-size: 0.9em;
            color: #5bc0be;
            margin-bottom: 5px;
            font-weight: bold;
            font-family: 'Courier New', monospace;
        }
        .timeline-content {
            background: rgba(255, 255, 255, 0.03);
            padding: 15px;
            border-radius: 4px;
            border: 1px solid rgba(255, 255, 255, 0.05);
        }

        /* Force borders on any remaining native DataFrames (other pages) */
        .stDataFrame, div[data-testid="stTable"] {
            border: 1px solid rgba(56,189,248,0.2);
            border-radius: 8px;
            overflow: hidden;
        }
        .stChart {
            border: 1px solid rgba(255, 255, 255, 0.1);
            padding: 10px;
            border-radius: 10px;
            background: rgba(0,0,0,0.2);
        }
    </style>
    
    <!-- Sound Effect Script -->
    <audio id="click-sound" src="data:audio/wav;base64,UklGRl4RAABXQVZFZm10IBAAAAABAAEARKwAAIhYAQACABAAZGF0YToRAAAAACIS0SOyNG9EuFJDX9NpM3I6eMt713xce2N3BnFmaLRdKVEIQ50zOCMwEt4AnO/C3qfOn7/1sfClypu4k9+NXIo+iYiKMY4jlDycUaYqsom/Js603eHtWv7HDtYeMi6OPJ9JJFXjXqtmVmzLb/hw2298bPBmVV/VVaRK/T0kMGIhBBJaArjya+PE1A7Hj7qGryqmqp4rmcaVjJR/lZiYxp3qpN2tb7hoxIfRh98g7gT95Qt5GnIoijV+QRBMDVVHXJph72Q0ZmdljmK6XQdXmE6dREk52yySH7URjQNj9YDnLNqszT7CHbh8r4eoXqMaoMuedZ8SopKm3KzNtDq+8ci51FPhfe7z+20JpxZdI00vOzruQzdM61LqVxxbc1zpW4RZU1VtT/JHCz/pNMApzB1LEX8ErPcS6/PejdMaydC/3bdpsZKscqkVqIGosqqZriK0LbuUwyrNvdcT4/PuHPtQB1ET4B7CKb8zpTxGRH1KK085UptTSlNKUahNd0jWQec51jDSJhEcyxA7BZ/5MO4q48fYO8+2xmS/aLnhtOWxgLC4sIuy7bXKugjBhMgY0ZTax+R773j6hAVoEOsa1ST2LR42JD3mQkZHMUqYS3ZLzUmoRhpCOjwpNQ0tESRlGjsQyQVG++fw4uZr3bLU48wmxpzAYbyIuSC4LrivuZu84MBoxhPNvtRA3WzmEvD/+f8D3w1tF3YgzChGML02ETwnQO1CVkRaRP1CR0BHPBM3yTCJKXohyBifDzAGrfxF8ynqiuGS2WvSN8wXxyTDcMAJv/O+L8CzwnLGVstG0SDYwt8B6LPwqvm2AqsLWRSUHDEkCiv9MOo1uzldPMM95z3LPHY69DZaMsEsRiYNHzsX+g51Btr9UvUM7TLl691d16rR7Mw9ya7GSsUYxRfGQciJy93PJdVE2xvihelb8XP5owHCCaURIxkXIFwm1CtiMPEzbjbPNw04KTcoNRYyBS4KKUAjxhy/FVAOnwbV/hr3lu9v6Mvhy9uN1izSvc5SzPTKq8p3y1HNMNAD1LXYLt5P5PjqB/JV+b0AGghEDxYWbxwsIjInaCu4LhIxazK+MgkyUzCkLQwqniVzIKYaVRSjDbEGpv+k+NHxTus+5cDf8Nrk1rPTatEV0LrPWdDu0XDU0df92+DgXeZa7LTyTfkAAKsGLQ1jEy0Zbh4LI+4mAyo6LIot7S1hLesrlCloJngi2x2pGPwS9QyxBlEA+fnG89ntUehL497eI9sr2ATWudRQ1MnUItZS2E3bAt9e40noqe1i81b5Zf9vBVgL/hBGFhUbUh/oIsUl2ycgKY8pJinpJ94lEiOTH3Ubzha1EUcMoAbdAB37ffUZ8A7rdeZl4vTeMdws2uzYedjS2PbZ3tt+3snhrOUU6ujuDvRt+ef+YAS9CeEOshMXGPsbSh/yIegjISWZJU4lQSR7IgQg6hw9GRMVgBCdC4MGTgEZ/P32FvJ87UjpjuVi4tLf7N253D7cfNxy3RrfauFW5M7nv+sU8Lf0j/mD/ncDVQgCDWcRbBX9GAkcgR5XIIQhAiLQIe4gYx84HXcaMRd2E1sP9gpcBqcB8PxN+Njzpu/O62Pod+UX40/hKeCo39DfnuAO4hfkr+bG6U3tMPFc9br5NP6wAhsHXAtcDwkTTxYdGWcbHx0/HsEepB7nHZEcqBo4GE0V9xFIDlMKLQbsAaj9c/ll9ZLxD+7t6jvoCOZe5ETjweLW4oLjwOSK5tbol+u/7jzy/fXt+fj9BwIJBucJjQ3oEOgTfhacGDgaSxvPG8IbJhv+GVEYKBaOE5IQRA21CfgFIQJD/nP6w/ZI8xLwM+246q7oIOcU5o/llOUi5jXnyOjQ6kTtFvA485f2JPrL/XgBGgWeCPELAg/CESMUGBaaF58YIxklGaUYpRcsFkMU8hFHD1EMHQm/BUYCx/5R+/j3zPTd8Tvv8+wR653pnuga6BLohuhz6dTqoezQ7lXxJPQs91/6rP3/AEsEfAeDClAN1Q8FEtUTPRU0FrgWxhZdFoEVNxSGEnYQFA5sC4sIggVgAjb/E/wH+ST2d/MN8fTuNu3b6+rqZ+pV6rPqfuuz7EvuPPB98gH1u/ed+pf9mgCXA30GPwnOCxwOHxDMERsTBhSHFJ4UShSOE20S7hAYD/cMlgoACEMFbwKS/7r89vlV9+P0rvLB8CTv4e397H3sYuyt7Fvtae7S74zxj/PQ9UT43PqN/UUA+wKdBSAIdQqSDGsO+A8vEQ0SjBKqEmgSxxHKEHcP1g3wC80JegcEBXYC3/9L/cj6Y/gp9iP0XvLh8LTv3e5g7j7uee4O7/rvOPHB8o30kvbG+Bz7if0AAHQC2AQhB0MJMQvkDFIOdA9FEMAQ5RCxECgQSw8hDq4M+woSCfwGxAR2Ah4Ayf2B+1P5Svdx9dDzcfJZ8Y7wFfDu7xvwmfBn8YDy3fN49Uf3Qflc+439x/8AAiwEQAYxCPYJhQvXDOUNqQ4hD0kPIw+uDu4N5gydCxoKYwiDBoQEcAJRADT+Ivwn+kz4m/Yc9djz1PIW8qDxdvGW8QLytfKt8+P0Ufbw97b5m/uV/Zn/nAGVA3kFPgfdCEsKggt9DDUNqA3VDbkNVg2vDMcLowpJCcAHEgZFBGUCewCR/rD84vox+aX3RvYb9Sr0d/MG89ny8PJK8+bzwPTT9Rr3jfgl+tr7ov10/0YBEAPJBGYG4QcxCVAKOAvlC1QMggxwDB0MjAu/CrwJiAgpB6YFCARXApsA4P4r/Yf7/PmS+FH3PfZe9bb0SvQa9Cn0dfT89Lz1sPbT9x/5jfoW/LL9V//9AJ0CLQSmBQAHNQg9CRQKtgogC1ALRQsAC4IKzgnoCNYHnAZBBcwDRQK1ACP/l/0Z/LL6Z/lA+EL3c/bW9W71PvVG9YX1+vWj9nv3fvin+fD6UfzE/UH/vwA4AqQD/AQ4BlIHRggNCaUJCgo7CjYK/AmQCfIIJggxBxgG4QSSAzICyABd//X9mvxS+yT6Fvks+Gz32vZ39kb2SPZ89uL2dfc1+Bz5JvpM+4r82P0w/4oA4AErA2QEhQWIBmgHIQiuCA4JPwlACRAJswgoCHQHmgafBYcEWgMdAtYAjf9H/gz94fvN+tX5/vhN+MT3Zvc29zP3Xfe19zb44Piu+Zv6o/vB/O79Jf9eAJQBwQLeA+YE0wWhBkwH0AcrCFsIYAg6COkHbwfQBg4GLgUzBCQDBgLfALb/jv5w/WD8ZPuB+rv5F/mX+D/4DvgI+Cr4dfjn+H35NPoI+/X79vwF/h3/OABSAWMCZgNXBDEF7gWMBgcHXQeNB5UHdwcxB8cGOgaNBcUE5APxAu8B5QDY/8z+yP3Q/Or7Gvtk+s35VvkC+dP4yfjk+CT5iPkM+q/6bftB/Cj9HP4Z/xkAGAEQAv0C2QOgBE4F3wVSBqQG0gbeBsUGigYtBrAFFwVkBJsDwALYAecA9P8B/xX+M/1h/KP7/Ppw+gL6svmE+Xj5jfnE+Rv6kPoh+8r7iPxY/TT+GP8AAOcAyAGfAmgDHgS+BEQFrwX8BSkGNwYkBvEFoAUyBaoECgRWA5ECwAHnAAsAMP9Z/oz9zPwe/IX7A/uc+lL6JfoX+if6Vvqi+gn7ifsg/Mv8hf1L/hr/6/+8AIkBTAIDA6oDPQS5BBwFZAWQBZ8FkQVmBR8FvwRFBLcDFgNlAqkB5QAeAFf/lP7Z/Sv9i/z/+4f7KPvi+rf6p/qz+tv6Hft4++r7cPwJ/bD9Y/4d/9r/lwBRAQMCqgJCA8kDPASYBNwEBgUWBQwF6ASqBFUE6QNqA9oCOwKSAeEALQB5/8j+Hv5//e38bPz++6b7ZPs6+yr7MvtT+4373ftC/Lr8Q/3Z/Xn+If/N/3gAIAHCAVoC5gJhA8sDIARgBIkEmgSTBHQEPwT0A5QDIwOiAhQCewHcADkAlv/2/lv+yv1F/c78afwX/Nn7sfug+6X7wfvz+zr8k/z+/Hn9//2Q/if/wv9dAPYAiQEUApMCBANmA7UD8QMYBCkEJQQMBN0DmwNHA+ICbgLuAWUB1gBCAK//Hf+R/g3+k/0m/cn8ffxD/B38C/wO/CX8UPyO/N78Pf2r/ST+pv4u/7r/RgDRAFcB1QFJArECCwNUA4wDsQPDA8EDrAOEA0oD/wKlAj4CywFQAc4ASQDE/0D/wP5I/tn9df0f/dn8o/x+/Gz8bfyA/Kb83Pwi/Xf92f1G/rr+Nf+0/zIAsAAqAZ0BCAJnArkC/QIxA1UDZgNnA1UDMwMAA70CbQIRAqoBOwHHAE4A1v9e/+r+fP4X/rv9bP0r/fn81vzE/MP80/zz/CP9Yf2t/QT+Zv7P/j3/sP8iAJQAAwFsAc0BJAJwAq8C3wIBAxMDFQMGA+kCvAKBAjoC5wGLASgBvgBSAOX/eP8P/6v+Tv77/bL9df1H/Sb9FP0S/R79Ov1k/Zv93v0s/oP+4v5G/63/FAB8AOAAQAGZAegBLgJoApYCtQLHAsoCvwKlAn4CSgILAsEBbgEVAbYAVADy/4//L//U/n/+M/7w/bj9jf1u/Vz9Wf1j/Xr9n/3Q/Qz+Uf6g/vT+Tv+s/wkAZwDCABkBagGzAfMBKQJTAnECggKGAn0CZwJFAhcC3wGdAVMBAwGtAFUA/P+j/0z/+f6r/mX+KP71/cz9r/2e/Zn9of21/dX9AP41/nT+uv4G/1f/q/8AAFUApwD3AEABgwG+AfABFwIzAkQCSQJCAi8CEQLpAbcBfAE6AfIApQBVAAQAtP9l/xn/0/6T/lr+K/4F/un92f3U/dr96/0H/i3+XP6U/tL+F/9g/6z/+f9FAJAA2AAbAVkBjwG8AeEB+wEMAhECDAL8AeIBvgGSAV0BIgHhAJwAVAALAML/ev82//X+u/6H/lv+OP4e/g7+Cf4N/hz+NP5V/n/+sf7p/if/aP+t//L/NwB8AL0A+wAzAWQBjgGwAckB2QHfAdsBzgG3AZgBcAFBAQwB0gCUAFMAEADP/47/T/8V/9/+r/6H/mb+Tv4//jn+PP5I/l3+e/6g/sz+/v42/3H/rv/t/ywAagClAN0AEQE+AWUBhAGcAasBsQGuAaMBkAF0AVEBJwH4AMMAiwBRABUA2f+e/2b/MP///tT+rv6Q/nn+a/5k/mb+cf6D/p3+vv7l/hL/RP95/7H/6v8iAFoAkADDAPIAHAFAAV0BcwGBAYgBhgF9AWwBUwE0AQ8B5AC2AIMATgAYAOP/rf95/0n/HP/0/tL+tf6g/pL+jP6N/pX+pf68/tn+/P4l/1H/gf+z/+f/GQBMAH4ArADXAP0AHgE5AU4BWwFiAWEBWgFLATUBGgH5ANMAqQB7AEwAGwDq/7r/i/9e/zX/Ef/x/tf+w/62/q/+sP63/sT+2f7y/hL/Nv9e/4n/tv/k/xIAQQBtAJgAvwDiAAABGQEsATkBQAFAAToBLQEaAQIB5ADCAJwAdABJAB0A8f/F/5r/cv9M/yv/Dv/2/uP+1v7Q/s/+1f7h/vP+Cv8l/0b/af+Q/7n/4/8MADYAXwCGAKkAyQDlAPwADgEbASEBIgEdARIBAQHsANEAswCRAGwARgAeAPb/zv+o/4P/Yf9C/yf/Ef8A//T+7f7s/vH++/4K/x//OP9U/3T/l/+8/+L/BwAtAFIAdQCWALMAzQDiAPMA/wAFAQYBAgH5AOoA1wDAAKUAhgBlAEMAHwD7/9f/tP+S/3P/V/8+/yr/Gv8O/wj/Bv8K/xP/IP8y/0j/Yv9//57/v//h/wMAJQBHAGcAhQCgALcAywDbAOYA7ADtAOoA4gDWAMUAsACYAHwAXwA/AB8A///e/77/n/+D/2n/U/9A/zH/Jv8g/x7/If8o/zT/RP9Y/2//iP+k/8L/4f8="></audio>
    <script>
        // Attach click listeners to tabs and radio buttons for sound effect
        var clickSound = document.getElementById("click-sound");

        function addSoundListeners() {
            // Select all interactive elements we want to sound-enable
            // We look for elements that do NOT yet have the data-sound-attached attribute
            const tabs = document.querySelectorAll('button[data-baseweb="tab"]:not([data-sound-attached])');
            const radios = document.querySelectorAll('div[data-testid="stRadio"] label:not([data-sound-attached])');
            
            const attach = (elements) => {
                elements.forEach(el => {
                    el.setAttribute("data-sound-attached", "true");
                    el.addEventListener('click', () => {
                        clickSound.currentTime = 0;
                        clickSound.play().catch(e => console.log("Audio play failed:", e));
                    });
                });
            };

            attach(tabs);
            attach(radios);
        }
        
        // Re-run listener attachment periodically to catch re-renders
        setInterval(addSoundListeners, 1000);
    </script>
    """, unsafe_allow_html=True)

    # --- INIT SESSION STATE FOR PERSISTENCE ---
    # Store initial values if not present
    if 'dcf_fcf' not in st.session_state: st.session_state.dcf_fcf = 0.0
    if 'dcf_growth' not in st.session_state: st.session_state.dcf_growth = 10.0
    if 'dcf_terminal' not in st.session_state: st.session_state.dcf_terminal = 2.5
    if 'dcf_wacc' not in st.session_state: st.session_state.dcf_wacc = 9.0
    if 'dcf_debt' not in st.session_state: st.session_state.dcf_debt = 0.0
    if 'dcf_cash' not in st.session_state: st.session_state.dcf_cash = 0.0

    # --- Horizontal Navigation ---
    PAGES = [
        "Home",
        "Analysis",
        "Backtest",
        "Watchlist",
        "About",
        "Other",
    ]
    # Legacy pages live under "Other"
    OTHER_SUBPAGES = ["Financial Analysis", "Company Profile", "DCF Model", "Macro Stress Test"]

    if 'active_page' not in st.session_state:
        st.session_state.active_page = "Home"
    if st.session_state.active_page not in PAGES:
        st.session_state.active_page = "Home"
    if 'other_subpage' not in st.session_state:
        st.session_state.other_subpage = "Financial Analysis"

    _user = st.session_state.get("user") or {}
    _logged_in = bool(_user.get("id"))
    from supabase_client import get_watchlist as _sb_get_watchlist, sign_out as _sb_sign_out
    _wl_rows = _sb_get_watchlist(_user.get("id")) if _logged_in else []
    _wl_count = len(_wl_rows)
    st.session_state["_watchlist_cache"] = _wl_rows

    # Header banner — brand centered; identity line only when logged in
    _identity_html = (
        f'<div style="color:#7dd3fc;font-size:0.82em;margin-top:2px;">'
        f'Signed in as <b>{_user.get("display_name", "—")}</b></div>'
        if _logged_in else ''
    )
    st.markdown(
        '<div class="valoura-topbar">'
        '<div class="valoura-brand">Contextual Valuation Engine</div>'
        + _identity_html +
        '</div>',
        unsafe_allow_html=True,
    )

    if _logged_in:
        # Nav row: one button per page + bookmark badge + logout on the right.
        # Sized off len(PAGES) — a hardcoded width silently collided with the
        # badge column when the Backtest page was added.
        _nav_cols = st.columns([1] * len(PAGES) + [0.7, 0.7])
        for _i, _p in enumerate(PAGES):
            with _nav_cols[_i]:
                _is_active = _p == st.session_state.active_page
                _btn_type  = "primary" if _is_active else "secondary"
                if st.button(
                    _p,
                    key=f"nav_btn_{_i}",
                    use_container_width=True,
                    type=_btn_type,
                ):
                    st.session_state.active_page = _p
                    st.rerun()
        with _nav_cols[len(PAGES)]:
            # Bookmark button with watchlist count badge → Watchlist page
            if st.button(f"🔖 {_wl_count}", key="nav_bookmark_badge",
                         use_container_width=True,
                         help="Your watchlist"):
                st.session_state.active_page = "Watchlist"
                st.rerun()
        with _nav_cols[len(PAGES) + 1]:
            if st.button("Logout", key="nav_logout", use_container_width=True):
                _sb_sign_out()
                for _k in ("user", "auth_mode", "_watchlist_cache", "stock_data"):
                    st.session_state.pop(_k, None)
                st.rerun()
    else:
        # Logged out: login icon TOP-LEFT with signup pitch under it, then nav
        _login_col, _nav_area = st.columns([1.3, 5])
        with _login_col:
            if st.button("👤 Log in / Sign up", key="nav_login_icon",
                         type="primary", use_container_width=True):
                st.session_state.auth_view = True
                st.rerun()
            st.caption(
                "Create an account to be notified when a stock changes stage (1–4), "
                "or to add stocks to a watchlist for our newsletter!"
            )
        with _nav_area:
            _nav_cols = st.columns(len(PAGES))
            for _i, _p in enumerate(PAGES):
                with _nav_cols[_i]:
                    _is_active = _p == st.session_state.active_page
                    _btn_type  = "primary" if _is_active else "secondary"
                    if st.button(
                        _p,
                        key=f"nav_btn_{_i}",
                        use_container_width=True,
                        type=_btn_type,
                    ):
                        st.session_state.active_page = _p
                        st.rerun()

    # JS: tag the Analysis button so CSS can give it an outline when inactive
    st.markdown(
        """
        <script>
        (function() {
            setTimeout(function() {
                document.querySelectorAll('button[data-testid="baseButton-secondary"]').forEach(function(btn) {
                    if ((btn.textContent || '').trim() === 'Analysis') {
                        btn.setAttribute('data-sba', 'true');
                    } else {
                        btn.removeAttribute('data-sba');
                    }
                });
            }, 80);
        })();
        </script>
        """,
        unsafe_allow_html=True,
    )

    active = st.session_state.active_page

    # Subpage selector inside "Other"
    if active == "Other":
        _sub_cols = st.columns([1, 1, 1, 1, 2])
        for _j, _sp in enumerate(OTHER_SUBPAGES):
            with _sub_cols[_j]:
                if st.button(
                    _sp,
                    key=f"other_sub_{_j}",
                    use_container_width=True,
                    type=("primary" if _sp == st.session_state.other_subpage else "secondary"),
                ):
                    st.session_state.other_subpage = _sp
                    st.rerun()

    # --- Control Row: classified-ticker selectbox + unclassified free-text ---
    _CAT_NAV_DISPLAY = {
        "hyperscale":        "Hyperscale",
        "saas":              "Pure SaaS",
        "semi_hardware":     "Semi / Hardware",
        "consumer_internet": "Consumer Internet",
        "deep_tech":         "Deep Tech",
    }
    _tick_conn = get_db_connection()
    _ticker_options = []          # list of display strings
    _ticker_map = {}              # display string -> raw ticker
    if _tick_conn is not None:
        try:
            for _t, _bm, _fh in _tick_conn.execute(
                "SELECT ticker, bm_category, fh_stage FROM classifications ORDER BY ticker"
            ).fetchall():
                _disp = f"{_t} — {_CAT_NAV_DISPLAY.get(_bm, _bm)} · S{_fh}"
                _ticker_options.append(_disp)
                _ticker_map[_disp] = _t
        except Exception:
            pass

    ctrl1, ctrl2, ctrl3 = st.columns([3, 1, 4])
    with ctrl1:
        _prev_ticker = st.session_state.get("last_ticker", "AAPL")
        _default_idx = 0
        for _ix, _d in enumerate(_ticker_options):
            if _ticker_map[_d] == _prev_ticker:
                _default_idx = _ix
                break
        _sel = st.selectbox(
            "Classified ticker",
            options=_ticker_options,
            index=_default_idx if _ticker_options else None,
            label_visibility="collapsed",
            key="ticker_selectbox",
        )
    with ctrl2:
        analyze_now = st.button("Fetch live data", use_container_width=True,
                                help="Loads live market data (needed for the pages under Other)")
    with ctrl3:
        st.caption(f"{len(_ticker_options)} classified tickers available")

    # Free-text path for unclassified tickers
    with st.expander("Analyse unclassified ticker"):
        _free_col1, _free_col2 = st.columns([3, 1])
        with _free_col1:
            _free_text = st.text_input(
                "Unclassified ticker", label_visibility="collapsed",
                placeholder="e.g. TSLA — not yet classified by the engine",
                key="unclassified_input",
            ).strip().upper()
        with _free_col2:
            _use_free = st.button("Analyse", use_container_width=True, key="unclassified_go")
        if _use_free and _free_text:
            st.session_state.unclassified_ticker = _free_text
            analyze_now = True
        if st.session_state.get("unclassified_ticker"):
            if st.button(f"← Back to classified tickers (currently viewing "
                         f"{st.session_state.unclassified_ticker})", key="clear_unclassified"):
                st.session_state.pop("unclassified_ticker", None)
                st.rerun()

    # Resolve active ticker: unclassified free-text wins if set
    if st.session_state.get("unclassified_ticker"):
        ticker_symbol = st.session_state.unclassified_ticker
        st.info(f"**{ticker_symbol}** — this ticker has not been classified by the "
                "engine yet — showing live data only.")
    elif _sel and _ticker_map:
        ticker_symbol = _ticker_map.get(_sel, "AAPL")
    else:
        ticker_symbol = _prev_ticker

    page = active

    # --- Ticker Persistence & Reset Logic ---
    # If ticker changes, reset DCF inputs so they can be re-fetched
    if 'last_ticker' not in st.session_state:
        st.session_state.last_ticker = ticker_symbol
    
    if st.session_state.last_ticker != ticker_symbol:
        st.session_state.dcf_fcf = 0.0
        st.session_state.dcf_debt = 0.0
        st.session_state.dcf_cash = 0.0
        # Optional: Reset growth/WACC to defaults if desired, or keep user preference?
        # Let's reset to defaults to be safe for a new company
        st.session_state.dcf_growth = 10.0
        st.session_state.dcf_terminal = 2.5
        st.session_state.dcf_wacc = 9.0
        st.session_state.last_ticker = ticker_symbol
        
        # Force widgets to reload by clearing shadow keys
        keys_to_clear = ['widget_dcf_fcf', 'widget_dcf_growth', 'widget_dcf_terminal', 'widget_dcf_wacc', 'widget_dcf_debt', 'widget_dcf_cash']
        for k in keys_to_clear:
            if k in st.session_state:
                del st.session_state[k]

    # Dispatch "Other" to its selected subpage before routing

    # --- ABOUT PAGE (formerly Introduction — no ticker needed) ---
    if page == "About":
        _cve_conn = get_db_connection()

        # Header
        st.markdown(
            "<div class='fun-header'>Contextual Valuation Engine</div>",
            unsafe_allow_html=True,
        )
        st.markdown(
            "<p style='color:#94a3b8;font-size:1em;margin-top:-12px;margin-bottom:24px;"
            "font-style:italic;'>Please read before proceeding.</p>",
            unsafe_allow_html=True,
        )

        # --- Introduction text ---
        st.markdown("""
This introduction serves to explain how to navigate this platform. Here are the primary dashboards:

- **Financial Analysis** — live price data, chart, historical returns, and balance sheet.
- **Stage-Based Analysis** — the classification and contextual valuation engine.
- **Company Profile** — business summary, sector, and recent news.
- **Other** — DCF model and macro stress test tools.
        """)

        st.markdown("---")

        # Stage-Based Analysis explanation block
        st.subheader("Stage-Based Analysis")
        st.markdown("""
The Stage-Based Analysis layer has been in the works for months. This tab shows the functional interpretation layer and valuation logic that fits each company at any point in its development. It serves as a contextual engine — built on the premise that *valuation without context is noise*.

The right metric for a cash-burning deep tech startup is not the same metric for a cash-compounding enterprise software platform, and applying one framework universally is how private investors end up with misleading conclusions.

A semiconductor company mid-cycle is valued differently from one at cycle peak. A SaaS company with 40% stock-based compensation is not the same business as one with 3%. A hyperscale platform requires segment-level decomposition before a meaningful multiple can be applied. That institutional knowledge is not accessible in most tools built for private investors.
        """)

        st.subheader("How it works")
        st.markdown("""
Every stock ticker is classified across two independent axes: **business model category** and **financial health stage**. The classification determines which valuation metrics apply, what the fair ranges are for those metrics, and which peer group is the meaningful comparison. The result is an analysis that reflects what the company actually is — not a generic score applied uniformly across an entire index.

A synthesis layer reads the full classification, the valuation output, and the peer comparison to produce a plain-English interpretation of where the company stands. Not a buy or sell signal. A clear, evidence-based picture of what the data says.
        """)

        st.subheader("Status")
        st.markdown("""
Currently covers the US technology sector across five business model categories and four financial health stages. Expansion into retail, financial services, healthcare, and industrials is in development. A backtesting layer to validate the classification framework against historical returns is also in progress.
        """)

        st.subheader("Who it is for")
        st.markdown("""
Private investors who want to understand a stock rather than simply be told what to do with it. Anyone who has looked at a P/E ratio and wondered whether it actually means anything for the company they are looking at. Analysts and researchers who want a structured first-pass classification before building their own view.
        """)

        st.markdown("---")

        # --- Classified Ticker Grid (shared helper) ---
        render_classified_universe(_cve_conn)

    # --- HOME PAGE (landing — Supabase watchlist + SQLite classifications) ---
    if page == "Home":
        render_home_page(st.session_state.get("user", {}))

    # --- BACKTEST PAGE (read-only; displays stored portfolio_sim.py results) ---
    if page == "Backtest":
        from backtest_page import render_backtest_page
        render_backtest_page(get_db_connection(), render_dark_table)

    # --- WATCHLIST PAGE ---
    if page == "Watchlist":
        render_watchlist_page(st.session_state.get("user", {}))

    # --- OTHER → dispatch to legacy subpage; these need LIVE market data ---
    if page == "Other":
        page = st.session_state.get("other_subpage", "Financial Analysis")

        # Live-data gate: auto-fetch when ticker changed or nothing cached.
        _cached_tkr = st.session_state.get("stock_data_ticker")
        _need_fetch = (
            analyze_now
            or "stock_data" not in st.session_state
            or _cached_tkr != ticker_symbol
        )
        if _need_fetch:
            clean_ticker = str(ticker_symbol).strip().upper()
            if not clean_ticker:
                st.error("Ticker symbol is empty. Please enter a valid symbol.")
                return
            with st.spinner(f"Fetching live market data for {clean_ticker}..."):
                stock, info = fetch_stock_data_v2(clean_ticker)
            if stock is None or info is None:
                st.error(
                    f"CVE cannot reach any data source for **{clean_ticker}**.\n\n"
                    f"All three fetchers failed: yfinance, yahooquery, and Alpha Vantage. "
                    f"This usually means the **ALPHA_VANTAGE_KEY** secret is not configured on Streamlit Cloud. "
                    f"Go to App Settings → Secrets and add:\n\n"
                    f"```toml\nALPHA_VANTAGE_KEY = \"your_key_here\"\n```"
                )
                st.stop()
            st.session_state.stock_data = (stock, info)
            st.session_state.stock_data_ticker = clean_ticker
        else:
            stock, info = st.session_state.stock_data

    # --- PAGE 1: Financial Analysis ---
    if page == "Financial Analysis":
        st.markdown(f'<div class="fun-header">{info.get("longName", ticker_symbol)} ({ticker_symbol})</div>', unsafe_allow_html=True)

        with st.spinner("Loading market data..."):
            hist = stock.history(period="max")
            chart_hist = hist.tail(504)  # 2y for chart
            news = stock.news

        # Header Metrics — split across 3 rows so nothing clips on any screen
        _price = info.get('currentPrice') or info.get('regularMarketPrice')
        _prev  = info.get('previousClose') or info.get('regularMarketPreviousClose')
        _chg   = round((_price - _prev) / _prev * 100, 2) if (_price and _prev) else None

        # Row 1: core price stats
        _r1a, _r1b, _r1c = st.columns(3)
        _r1a.metric("Price", f"${_price:.2f}" if _price else "N/A", delta=f"{_chg:+.2f}%" if _chg is not None else None)
        _r1b.metric("Market Cap", f"${info.get('marketCap', 0)/1e9:.1f}B" if info.get('marketCap') else "N/A")
        _r1c.metric("Beta", f"{info.get('beta', 'N/A')}")

        # Row 2: 52-week range (centered with blank outer columns)
        _r2a, _r2b, _r2c, _r2d = st.columns(4)
        _r2b.metric("52W High", f"${info.get('fiftyTwoWeekHigh', 'N/A')}")
        _r2c.metric("52W Low",  f"${info.get('fiftyTwoWeekLow',  'N/A')}")

        # Row 3: valuation + yield
        _r3a, _r3b, _r3c = st.columns(3)
        _r3a.metric("P/E (TTM)", f"{info.get('trailingPE', 'N/A'):.1f}x" if isinstance(info.get('trailingPE'), (int, float)) else "N/A")
        _r3b.metric("EPS (TTM)", f"${info.get('trailingEps', 'N/A'):.2f}" if isinstance(info.get('trailingEps'), (int, float)) else "N/A")
        _r3c.metric("Div Yield", f"{info.get('dividendYield', 0)*100:.2f}%" if info.get('dividendYield') else "None")

        st.markdown("---")

        tabs = st.tabs(["Live Chart", "Financials"])

        # TAB 1: Chart
        with tabs[0]:
            st.subheader("Price Action")
            if not chart_hist.empty:
                fig = go.Figure()
                fig.add_trace(go.Candlestick(x=chart_hist.index,
                                open=chart_hist['Open'], high=chart_hist['High'],
                                low=chart_hist['Low'], close=chart_hist['Close'],
                                name='Price'))
                chart_hist['MA50'] = chart_hist['Close'].rolling(window=50).mean()
                fig.add_trace(go.Scatter(x=chart_hist.index, y=chart_hist['MA50'], line=dict(color='#60a5fa', width=2), name='50 MA'))
                fig.update_layout(template="plotly_dark", plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)', height=500, xaxis_rangeslider_visible=False)
                st.plotly_chart(fig, use_container_width=True)
            
            # --- Returns Display ---
            if not hist.empty:
                st.markdown("##### Historical Returns")
                # Calculate Returns
                current_price = hist['Close'].iloc[-1]
                
                def get_return(days_back, label):
                    try:
                        if len(hist) > days_back:
                            past_price = hist['Close'].iloc[-days_back-1]
                            ret = (current_price - past_price) / past_price * 100
                            return ret
                        else:
                            return None
                    except:
                        return None

                # YTD Logic
                try:
                    current_year = hist.index[-1].year
                    ytd_start = hist[hist.index.year == current_year].iloc[0]['Open']
                    ytd_ret = (current_price - ytd_start) / ytd_start * 100
                except:
                    ytd_ret = 0.0

                returns_data = [
                    ("1 Week", get_return(5, "1 Week")),
                    ("1 Month", get_return(21, "1 Month")),
                    ("1 Year", get_return(252, "1 Year")),
                    ("YTD", ytd_ret),
                    ("5 Years", get_return(1260, "5 Years")),
                    ("All Time", (current_price - hist['Close'].iloc[0]) / hist['Close'].iloc[0] * 100)
                ]
                
                cols = st.columns(6)
                for i, (label, val) in enumerate(returns_data):
                    with cols[i]:
                        if val is not None:
                            color = "#4ade80" if val >= 0 else "#f87171"
                            arrow = "▲" if val >= 0 else "▼"
                            st.markdown(f"""
                            <div style="text-align: center;">
                                <span style="color: white; font-size: 0.9em; font-weight: bold;">{label}</span><br>
                                <span style="color: {color}; font-size: 1.1em; font-weight: bold;">
                                    {arrow} {abs(val):.2f}%
                                </span>
                            </div>
                            """, unsafe_allow_html=True)
                        else:
                            st.markdown(f"""
                            <div style="text-align: center;">
                                <span style="color: white; font-size: 0.9em; font-weight: bold;">{label}</span><br>
                                <span style="color: #94a3b8; font-size: 1.1em;">N/A</span>
                            </div>
                            """, unsafe_allow_html=True)

        # TAB 2: Financials
        with tabs[1]:
            fin_tabs = st.tabs(["Detailed View", "Simplified View"])

            # Safely fetch balance sheet — _AVStockProxy (AV fallback) has no .balance_sheet
            try:
                _raw_bs = getattr(stock, 'balance_sheet', None)
                if _raw_bs is None:
                    raise AttributeError("no balance sheet")
                bs = _raw_bs.copy()
            except Exception:
                bs = None

            if bs is None or bs.empty:
                with fin_tabs[0]:
                    st.info("Balance sheet data not available for this ticker or data source. Try refreshing or switching to a direct market data source.")
                with fin_tabs[1]:
                    st.info("Balance sheet data not available.")
            else:
                # Add Debt/Equity row
                try:
                    t_debt = None
                    if 'Total Debt' in bs.index:
                        t_debt = bs.loc['Total Debt']
                    elif 'Long Term Debt' in bs.index and 'Current Debt' in bs.index:
                        t_debt = bs.loc['Long Term Debt'] + bs.loc['Current Debt']

                    t_equity = None
                    if 'Total Equity Gross Minority Interest' in bs.index:
                        t_equity = bs.loc['Total Equity Gross Minority Interest']
                    elif 'Stockholders Equity' in bs.index:
                        t_equity = bs.loc['Stockholders Equity']

                    if t_debt is not None and t_equity is not None:
                        de_ratio = t_debt / t_equity.replace(0, np.nan)
                        de_row = pd.DataFrame(de_ratio).T
                        de_row.index = ["Debt to Equity Ratio"]
                        bs = pd.concat([de_row, bs])
                except Exception:
                    pass

                def highlight_de_row(s):
                    if s.name == "Debt to Equity Ratio":
                        return ['background-color: #facc15; color: black; font-weight: bold' for _ in s]
                    return ['' for _ in s]

                with fin_tabs[0]:
                    try:
                        if "Debt to Equity Ratio" in bs.index:
                            styler = bs.style.format("{:,.2f}", subset=pd.IndexSlice[["Debt to Equity Ratio"], :]) \
                                             .format("{:,.0f}", subset=bs.index.difference(["Debt to Equity Ratio"])) \
                                             .apply(highlight_de_row, axis=1)
                        else:
                            styler = bs.style.format("{:,.0f}")
                        st.dataframe(styler)
                    except Exception:
                        st.dataframe(bs)

                with fin_tabs[1]:
                    try:
                        def simplify_number(n):
                            try:
                                abs_n = abs(n)
                                if abs_n < 1000:
                                    return f"{n:.2f}"
                                if abs_n >= 1e9:
                                    return f"{n/1e9:.2f}B"
                                elif abs_n >= 1e6:
                                    return f"{n/1e6:.2f}M"
                                elif abs_n >= 1e3:
                                    return f"{n/1e3:.2f}K"
                                else:
                                    return f"{n:.2f}"
                            except Exception:
                                return n

                        simple_df = bs.applymap(simplify_number)
                        if "Debt to Equity Ratio" in simple_df.index:
                            st.dataframe(simple_df.style.apply(highlight_de_row, axis=1))
                        else:
                            st.dataframe(simple_df)
                    except Exception:
                        st.dataframe(bs)

    # --- PAGE 2: DCF Model ---
    elif page == "DCF Model":
        st.markdown(f'<div class="fun-header">DCF Model: {ticker_symbol}</div>', unsafe_allow_html=True)
        st.subheader("Discounted Cash Flow Calculator")
        st.markdown("Determine the fair value of the stock based on its future cash flow projections.")

        # Robust FCF, Debt, Cash Fetching (Only if keys are 0.0/default, otherwise respect user input)
        # Note: If user wants to reset, they refresh. If they switch tabs, we keep their input.
        # But if they switch tickers, we probably want to update? 
        # For this request, "save data inputted" implies session persistence. 
        # But "init with real data" is also needed.
        # Strategy: If the user hasn't touched the inputs (value is default 0.0 or from prev ticker?), update it?
        # Simpler: Always update persistent defaults when ticker changes? 
        # Complex. For now, I will prioritize fetching on first load or if values are 0.
        
        latest_fcf = 0.0
        total_debt = 0.0
        total_cash = 0.0
        
        # Only fetch if we haven't set a value yet or it's zero (fresh start)
        # However, checking against session state:
        if st.session_state.dcf_fcf == 0.0:
            try:
                cashflow = stock.cashflow
                if not cashflow.empty:
                    if 'Free Cash Flow' in cashflow.index:
                            latest_fcf = float(cashflow.loc['Free Cash Flow'].iloc[0])
                    elif 'Total Cash From Operating Activities' in cashflow.index and 'Capital Expenditures' in cashflow.index:
                            latest_fcf = float(cashflow.loc['Total Cash From Operating Activities'].iloc[0] + cashflow.loc['Capital Expenditures'].iloc[0])
                st.session_state.dcf_fcf = latest_fcf
            except: pass
        
        if st.session_state.dcf_debt == 0.0:
            try:
                bs = getattr(stock, 'balance_sheet', None)
                if bs is not None and not bs.empty:
                    if 'Total Debt' in bs.index:
                        total_debt = float(bs.loc['Total Debt'].iloc[0])
                    elif 'Long Term Debt' in bs.index:
                        total_debt = float(bs.loc['Long Term Debt'].iloc[0])
                    st.session_state.dcf_debt = total_debt
            except: pass

        if st.session_state.dcf_cash == 0.0:
            try:
                bs = getattr(stock, 'balance_sheet', None)
                if bs is not None and not bs.empty:
                    if 'Cash And Cash Equivalents' in bs.index:
                        total_cash = float(bs.loc['Cash And Cash Equivalents'].iloc[0])
                    st.session_state.dcf_cash = total_cash
            except: pass

        # Persistence Callback
        def update_dcf_state(key, widget_key):
            st.session_state[key] = st.session_state[widget_key]

        # Inputs (Use shadow keys + callback for true persistence across tabs)
        st.markdown("#### 🛠️ Model Inputs")
        c1, c2, c3 = st.columns(3)
        with c1:
            fcf_input = st.number_input("Latest Free Cash Flow ($)", value=st.session_state.dcf_fcf, key="widget_dcf_fcf", format="%.2f", on_change=update_dcf_state, args=('dcf_fcf', 'widget_dcf_fcf'))
        with c2:
            # Session state stores percentage (e.g., 10.0), slider uses 10.0. DCF logic needs 0.10.
            growth_val = st.slider("Growth Rate (5 Yr) %", 0.0, 30.0, st.session_state.dcf_growth, key="widget_dcf_growth", on_change=update_dcf_state, args=('dcf_growth', 'widget_dcf_growth'))
            growth_rate = growth_val / 100.0
            
            with st.expander("🔎 How to estimate Growth Rate?"):
                st.markdown("""
                <div style="color: white; font-size: 0.9em;">
                Look at historical revenue or earnings growth (CAGR) from the "Financials" tab. 
                Alternatively, check analyst estimates for "Next 5 Years" on sites like Yahoo Finance under the "Analysis" tab.
                </div>
                """, unsafe_allow_html=True)
        with c3:
            term_val = st.slider("Terminal Growth %", 1.0, 5.0, st.session_state.dcf_terminal, key="widget_dcf_terminal", on_change=update_dcf_state, args=('dcf_terminal', 'widget_dcf_terminal'))
            terminal_growth = term_val / 100.0
            
            with st.expander("🔎 How to estimate Terminal Growth?"):
                st.markdown("""
                <div style="color: white; font-size: 0.9em;">
                This represents the long-term stable growth of the company after 5 years. 
                It is typically aligned with the long-term GDP growth or inflation rate (e.g., 2% - 3%). 
                <b>Caution:</b> Do not set this higher than the Discount Rate (WACC) or the Risk-Free Rate.
                </div>
                """, unsafe_allow_html=True)
        
        wacc_val = st.slider("Discount Rate (WACC) %", 5.0, 15.0, st.session_state.dcf_wacc, key="widget_dcf_wacc", help="See below for calculation help", on_change=update_dcf_state, args=('dcf_wacc', 'widget_dcf_wacc'))
        discount_rate = wacc_val / 100.0

        # Debt/Cash Inputs for Equity Value Calc
        st.markdown("#### ⚖️ Net Debt Adjustment (for Equity Value)")
        c_d1, c_d2 = st.columns(2)
        with c_d1:
            debt_input = st.number_input("Total Debt ($)", value=st.session_state.dcf_debt, key="widget_dcf_debt", format="%.2f", on_change=update_dcf_state, args=('dcf_debt', 'widget_dcf_debt'))
        with c_d2:
            cash_input = st.number_input("Cash & Equivalents ($)", value=st.session_state.dcf_cash, key="widget_dcf_cash", format="%.2f", on_change=update_dcf_state, args=('dcf_cash', 'widget_dcf_cash'))

        st.caption("Total Debt: Found on Balance Sheet under Liabilities (Current Debt + Long Term Debt).")
        st.caption("Cash & Equivalents: Found on Balance Sheet under Assets (often the top line).")

        with st.expander("ℹ️ How to calculate WACC?"):
            st.markdown("""
            <div style="color: white;">
            <strong>Weighted Average Cost of Capital (WACC) Formula:</strong><br>
            <code>WACC = (E/V * Re) + (D/V * Rd * (1 - T))</code>
            <br><br>
            <ul>
                <li><strong>E</strong> = Market value of Equity (Market Cap)</li>
                <li><strong>D</strong> = Market value of Debt (Total Debt)</li>
                <li><strong>V</strong> = Total Value (E + D)</li>
                <li><strong>Re</strong> = Cost of Equity (Calculated via CAPM: RiskFree + Beta * (MarketReturn - RiskFree))</li>
                <li><strong>Rd</strong> = Cost of Debt (Interest Rate on Debt)</li>
                <li><strong>T</strong> = Corporate Tax Rate</li>
            </ul>
            <p><strong>Resources:</strong><br>
            <a href="https://www.investopedia.com/terms/w/wacc.asp" target="_blank" style="color: #60a5fa;">Investopedia: WACC Guide</a><br>
            <a href="https://people.stern.nyu.edu/adamodar/New_Home_Page/datafile/wacc.htm" target="_blank" style="color: #60a5fa;">Damodaran Online: WACC by Sector</a>
            </p>
            </div>
            """, unsafe_allow_html=True)

        st.markdown("---")

        # Calculation
        if fcf_input > 0:
            try:
                shares = info.get('sharesOutstanding', 1)
                intrinsic_share_price = calculate_dcf_value(fcf_input, growth_rate, terminal_growth, discount_rate, debt_input, cash_input, shares)
                
                current_p = info.get('currentPrice', 0)

                # Results Display
                st.subheader("🏷️ Valuation Result")
                
                col_res1, col_res2 = st.columns(2)
                
                with col_res1:
                    st.markdown("**Intrinsic Value (Fair Price)**")
                    
                    # Formatting based on comparison
                    if current_p > 0:
                        if intrinsic_share_price > current_p:
                            st.markdown(f"<h2 style='color: #4ade80;'>${intrinsic_share_price:,.2f}</h2>", unsafe_allow_html=True)
                            st.success("The stock appears to be **UNDERVALUED**.")
                        else:
                            st.markdown(f"<h2 style='color: #f87171;'>${intrinsic_share_price:,.2f}</h2>", unsafe_allow_html=True)
                            st.error("The stock appears to be **OVERVALUED**.")
                    else:
                        st.markdown(f"<h2>${intrinsic_share_price:,.2f}</h2>", unsafe_allow_html=True)

                with col_res2:
                    st.metric("Actual Market Price", f"${current_p:,.2f}")
                    if current_p > 0:
                         diff = ((intrinsic_share_price - current_p) / current_p) * 100
                         st.metric("Potential Upside/Downside", f"{diff:.2f}%")

            except Exception as e:
                st.error(f"Calculation Error: {e}")
        else:
            st.warning("Need positive Cash Flow for this model.")

        st.markdown("---")
        st.subheader("📚 Useful Resources for Data")
        st.markdown("""
        *   [Yahoo Finance](https://finance.yahoo.com) - Comprehensive financial news, data, and stock quotes.
        *   [Investing.com](https://www.investing.com) - Real-time data, quotes, charts, financial tools, breaking news and analysis.
        *   [TradingView](https://www.tradingview.com) - Advanced charting platform and social network for traders and investors.
        *   [Bloomberg Markets](https://www.bloomberg.com/markets) - Business and financial market news, data, analysis, and video.
        """)
    # --- PAGE 4: Macro Stress Test ---
    elif page == "Macro Stress Test":
        st.markdown('<div class="fun-header">Macro Stress Test</div>', unsafe_allow_html=True)

        current_prices, hist_macro = fetch_macro_command_center()
        oil_price = current_prices.get("Crude Oil (WTI)", 0)
        if oil_price is None:
            oil_price = 0

        # Extract only the main commodities/yield for the ticker tape to avoid overcrowding
        exclude_from_tape = ["S&P 500", "NASDAQ", "Hang Seng"]
        ticker_items = {k: v for k, v in current_prices.items() if k not in exclude_from_tape}

        # --- GLOBAL COMMODITY TICKER TAPE ---
        cols = st.columns(len(ticker_items))
        for i, (name, val) in enumerate(ticker_items.items()):
            if val is not None:
                cols[i].metric(name, f"${val:.2f}" if name != "10Y Treasury Yield" else f"{val:.2f}%")
            else:
                cols[i].metric(name, "N/A")

        st.markdown("---")

        # --- THE NEAT TABLE (Indices) ---
        st.subheader("📊 Global Market Benchmarks")

        # Format a clean table for the Indices
        bench_df = pd.DataFrame([current_prices]).T.rename(columns={0: "Current Price"})
        st.dataframe(bench_df.style.format("${:,.2f}"), use_container_width=True)

        if not hist_macro.empty:
            # --- 60-DAY LINE CHART FIX ---
            st.subheader("60-Day Macro Trajectory")

            # Clean the data: drop NaNs so the lines are continuous
            chart_data = (hist_macro / hist_macro.iloc[0]) * 100
            chart_data = chart_data.interpolate(method='linear').ffill().bfill()
            st.line_chart(chart_data)
            st.caption("Normalized Growth Index (Base 100). All assets synced to S&P 500 timeframe.")

            st.markdown("---")

            # --- S&P 500 CORRELATION ANALYSIS ---
            st.subheader("🔗 S&P 500 Correlation Analysis")

            corr_matrix = hist_macro.corr()

            col_corr, col_insight = st.columns([1, 1.5])

            with col_corr:
                st.write("**Correlation Matrix**")
                st.dataframe(corr_matrix.style.background_gradient(cmap='RdYlGn', axis=None))

            with col_insight:
                st.write("**Strategic Insight**")
                if "Crude Oil (WTI)" in corr_matrix.index and "S&P 500" in corr_matrix.columns:
                    oil_spx_corr = corr_matrix.loc["Crude Oil (WTI)", "S&P 500"]
                    if not pd.isna(oil_spx_corr) and oil_spx_corr < -0.3:
                        st.error(f"⚠️ **Inverse Oil Correlation ({oil_spx_corr:.2f}):** Oil is rising while the S&P 500 falls. This suggests that energy supply shocks (like the Hormuz closure) are actively devaluing equities.")

                if "Gold" in corr_matrix.index and "S&P 500" in corr_matrix.columns:
                    gold_spx_corr = corr_matrix.loc["Gold", "S&P 500"]
                    if not pd.isna(gold_spx_corr) and gold_spx_corr < 0:
                        st.success(f"🛡️ **Safe Haven Confirmation:** Gold is negatively correlated with stocks ({gold_spx_corr:.2f}). Gold is effectively acting as a hedge during this period of instability.")

            st.markdown("---")

        # --- GEOPOLITICAL CHOKEPOINTS + AI NEWS ---
        st.subheader("🚩 Active Geopolitical Intelligence")

        chokepoints = {
            "Strait of Hormuz": "Strait of Hormuz Iran blockade",
            "Suez Canal": "Suez Canal Red Sea shipping",
            "Malacca Strait": "Malacca Strait shipping news"
        }

        for name, query in chokepoints.items():
            with st.container():
                st.markdown(f"#### 🚢 {name}")
                news_items = fetch_google_news_rss(query)

                # AI SUMMARY BLOCK
                ai_sum = get_ai_geopol_summary(name, news_items)
                st.info(ai_sum)

                # STRATEGIC NEWS PLACEMENT
                cols = st.columns(2)
                for i, item in enumerate(news_items[:2]):
                    with cols[i]:
                        st.markdown(f"**{item['publisher']}**")
                        st.markdown(f"[{item['title']}]({item['link']})")
                st.markdown("<br>", unsafe_allow_html=True)

        # --- THE 'RECESSION RISK' ALERT ---
        if oil_price > 100:
            st.markdown(f"""
            <div style="background-color: #7f1d1d; padding: 20px; border-radius: 10px; border: 2px solid #f87171;">
                <h3 style="color: white; margin-top: 0;">🚨 RECESSION RISK ALERT: OIL > $100</h3>
                <p style="color: #fca5a5;">Oil is currently at <b>${oil_price:.2f}</b>. Historically, sustained prices over $100 act as a massive tax on consumers,
                drastically reducing discretionary spending.</p>
                <hr style="border-color: #f87171;">
                <p><b>Impact on Consumer Cyclicals:</b> Expect significant margin contraction and lower demand for non-essential goods/services.</p>
            </div>
            """, unsafe_allow_html=True)

    # --- PAGE 5: Company Profile ---
    elif page == "Company Profile":
        st.markdown(f"<div class='fun-header'>Company Profile: {info.get('longName', ticker_symbol)}</div>", unsafe_allow_html=True)
        
        col_prof1, col_prof2 = st.columns([2, 1])
        with col_prof1:
            st.subheader("Who are they?")
            st.write(info.get('longBusinessSummary', "No summary available."))
            
            st.markdown(f"""
            **📍 HQ:** {info.get('city', 'N/A')}, {info.get('country', 'N/A')}  
            **👨‍👩‍👧‍👦 Team:** {info.get('fullTimeEmployees', 'N/A')} employees  
            **🌐 Web:** [{info.get('website', 'N/A')}]({info.get('website', '#')})
            """)
        
        with col_prof2:
            logo = info.get('logo_url', '')
            if logo:
                st.image(logo, width=150)
            st.metric("Sector", info.get('sector', 'N/A'))
            st.metric("Industry", info.get('industry', 'N/A'))

        st.markdown("---")
        st.subheader("News & Strategic Updates")
        st.markdown("Recent events shaping the company's future:")
        
        with st.spinner("Fetching latest news..."):
            # 1. Get Yahoo News
            yahoo_news = []
            try:
                yahoo_news = stock.news
            except:
                pass
            
            # 2. Get Google RSS News (Fallback/Supplement)
            google_news = fetch_google_news_rss(ticker_symbol)
            
            # 3. Combine & Sort
            all_news = []
            seen_links = set()
            
            # Process Yahoo
            for item in yahoo_news:
                link = item.get('link')
                if link not in seen_links:
                    all_news.append({
                        'title': item.get('title'),
                        'link': link,
                        'publisher': item.get('publisher', 'Yahoo Finance'),
                        'time': item.get('providerPublishTime', 0)
                    })
                    seen_links.add(link)
            
            # Process Google
            for item in google_news:
                link = item.get('link')
                if link not in seen_links:
                    all_news.append({
                        'title': item.get('title'),
                        'link': link,
                        'publisher': item.get('publisher', 'Google News'),
                        'time': item.get('providerPublishTime', 0)
                    })
                    seen_links.add(link)
            
            # Sort by time descending
            all_news.sort(key=lambda x: x['time'], reverse=True)
            
            # Filter Logic: Top 2 stories per date
            filtered_news = []
            news_by_date_count = {}
            
            for item in all_news:
                try:
                    date_str = datetime.fromtimestamp(item['time']).strftime('%Y-%m-%d')
                except:
                    date_str = "Unknown"
                
                current_count = news_by_date_count.get(date_str, 0)
                if current_count < 2:
                    filtered_news.append(item)
                    news_by_date_count[date_str] = current_count + 1
            
            # Limit total display to keep UI clean (e.g., top 12 items)
            display_news = filtered_news[:12]

            # Display
            if display_news:
                for item in display_news:
                    try:
                        pub_time = datetime.fromtimestamp(item['time']).strftime('%Y-%m-%d %H:%M')
                    except:
                        pub_time = "Recent"
                    
                    st.markdown(f"""
                    <div class="timeline-item">
                        <div class="timeline-dot"></div>
                        <div class="timeline-date">{pub_time} • {item['publisher']}</div>
                        <div class="timeline-content">
                            <strong>{item['title']}</strong><br>
                            <a href="{item['link']}" target="_blank" style="color: #60a5fa; text-decoration: none; font-size: 0.9em;">Read Source →</a>
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
            else:
                st.info("No recent news found from major sources.")

    # --- PAGE 6: Analysis (formerly Stage-Based Analysis) ---
    elif page == "Analysis":
        st.markdown(f"<div class='fun-header'>Analysis: {ticker_symbol}</div>", unsafe_allow_html=True)

        # ── Constants ────────────────────────────────────────────────────────
        BM_CATEGORIES = ["hyperscale", "saas", "semi_hardware", "consumer_internet", "deep_tech"]
        CAT_DISPLAY = {
            "hyperscale":        "Hyperscale",
            "saas":              "SaaS",
            "semi_hardware":     "Semi / HW",
            "consumer_internet": "Consumer Internet",
            "deep_tech":         "Deep Tech",
        }
        # Plain-English one-paragraph description per BM category — shown inside Section 5 tabs.
        BM_DESCRIPTIONS = {
            "hyperscale":
                "A cloud-infrastructure operator that owns physical data centres and earns "
                "usage-based revenue from compute, storage, and AI services. CapEx-heavy, with "
                "services revenue typically above 25% of total. Valuation anchors: CapEx-adjusted "
                "EV/EBIT and PEG.",
            "saas":
                "A pure software business that charges customers a recurring subscription fee "
                "with no physical product. Asset-light, with high gross margins, low CapEx, and "
                "predictable revenue. Valuation anchors range from EV/NTM ARR (early stage) to "
                "EV/FCF and FCF Yield (mature).",
            "semi_hardware":
                "Designs or manufactures physical semiconductors, equipment, or hardware "
                "components. Revenue is cyclical, tied to industry capital-spending cycles. "
                "Valuation anchors: Cycle-adjusted P/E (mature) and EV/NTM Revenue (early stage).",
            "consumer_internet":
                "A consumer-facing digital platform — social network, marketplace, streaming "
                "service, or app — monetised through advertising, transactions, or subscriptions. "
                "Valuation anchors: EV/EBITDA (mature) or EV/NTM Revenue (growth stage).",
            "deep_tech":
                "A frontier-technology company (quantum, photonics, aerospace, biotech-adjacent) "
                "where commercial revenue lags scientific or intellectual-property value. Often "
                "pre-profit and R&D-heavy. Valuation anchors: P/S (Stage 1), EV/NTM Revenue "
                "(Stage 2), EV/Gross Profit (Stage 3+).",
        }
        STAGE_LABELS = {
            1: "Stage 1 — Pre-revenue / speculative",
            2: "Stage 2 — Growth / scaling",
            3: "Stage 3 — Mature growth / compounding",
            4: "Stage 4 — Cash generation / FCF harvest",
        }
        METRIC_LABELS = {
            "peg_ratio":         ("PEG Ratio",        "x"),
            "capex_adj_ev_ebit": ("CapEx EV/EBIT",    "x"),
            "ev_ebitda":         ("EV/EBITDA",         "x"),
            "ev_fcf":            ("EV/FCF",            "x"),
            "fcf_yield":         ("FCF Yield",         "%"),
            "pe_ratio":          ("P/E Ratio",         "x"),
            "ev_ntm_arr":        ("EV/NTM ARR",        "x"),
            "cycle_adj_pe":      ("Cycle-adj P/E",     "x"),
            "ev_gross_profit":   ("EV/Gross Profit",   "x"),
            "ps_ratio":          ("P/S Ratio",         "x"),
            "rule_of_40":        ("Rule of 40",        "%"),
            "arr":               ("ARR",               "$B"),
        }
        # Per-metric tooltip text — 3 markdown bullets rendered inside the hover popup
        # (what / how used / high-low interpretation)
        METRIC_TOOLTIPS = {
            "peg_ratio": (
                "- **What it is:** P/E ratio divided by the company's earnings growth rate (trailing 2yr CAGR).\n"
                "- **How it's used:** Adjusts P/E for growth trajectory — a fairer cross-company comparison than P/E alone.\n"
                "- **High/low:** Below 1.0x suggests undervaluation vs growth; 1.0–2.0x is fair; above 2.0x means the market is pricing in significant acceleration."
            ),
            "capex_adj_ev_ebit": (
                "- **What it is:** Enterprise value plus CapEx, divided by operating income (EBIT).\n"
                "- **How it's used:** Normalises EV/EBIT for capital intensity — fairer comparison across hyperscalers with very different infrastructure investment.\n"
                "- **High/low:** Below 20x is cheap for a Stage-3 hyperscaler; 20–35x is fair; above 50x indicates extreme growth expectations."
            ),
            "ev_ebitda": (
                "- **What it is:** Enterprise value divided by earnings before interest, tax, depreciation and amortisation.\n"
                "- **How it's used:** How many years of operating cash earnings it would take to 'buy' the company; the standard multiple for mature businesses.\n"
                "- **High/low:** Below 12x is cheap; 12–25x fair; above 35x rich (typically requires high growth or a strong moat)."
            ),
            "ev_fcf": (
                "- **What it is:** Enterprise value divided by free cash flow (OCF minus CapEx minus stock-based comp).\n"
                "- **How it's used:** A purer cash multiple — preferred over EV/EBITDA when CapEx and SBC are material to the business model.\n"
                "- **High/low:** Below 20x cheap; 25–45x fair for Stage-3 SaaS; above 60x suggests aggressive growth optimism."
            ),
            "fcf_yield": (
                "- **What it is:** Free cash flow divided by market cap, expressed as a percentage (the inverse of EV/FCF).\n"
                "- **How it's used:** Tells you what percentage of your investment is 'returned' annually as cash — directly comparable to bond yields.\n"
                "- **High/low:** Above 5% indicates strong cash returns; 2–4% typical for mature compounders; below 1% means the stock is priced as a pure growth story."
            ),
            "pe_ratio": (
                "- **What it is:** Stock price divided by earnings per share (market cap divided by net income).\n"
                "- **How it's used:** Classic earnings multiple — the quickest read of how much investors pay per dollar of reported profit.\n"
                "- **High/low:** Below 15x is the value zone; 15–25x fair; above 40x has growth fully baked in. Misleads when non-cash charges or buybacks are large."
            ),
            "ev_ntm_arr": (
                "- **What it is:** Enterprise value divided by next-twelve-month annual recurring revenue.\n"
                "- **How it's used:** SaaS-specific forward multiple — accounts for the recurring, contracted nature of subscription revenue.\n"
                "- **High/low:** Below 8x is cheap for Stage 2; 8–18x is fair; above 25x typically reserved for hypergrowth or clear category leaders."
            ),
            "cycle_adj_pe": (
                "- **What it is:** Price divided by 3-year average mid-cycle earnings (smooths semiconductor cyclicality).\n"
                "- **How it's used:** Avoids overpaying at the cycle trough or appearing 'cheap' at the cycle peak in inherently cyclical industries.\n"
                "- **High/low:** Below 20x is cheap for semis; 20–35x fair; above 50x means the market is pricing in a structural shift, not just a cycle peak."
            ),
            "ev_gross_profit": (
                "- **What it is:** Enterprise value divided by gross profit.\n"
                "- **How it's used:** Used for hardware and deep-tech businesses with volatile EBIT — gross profit is a more stable anchor than operating income.\n"
                "- **High/low:** Below 15x is cheap; 15–30x fair; above 50x suggests an early-stage growth story being priced aggressively."
            ),
            "ps_ratio": (
                "- **What it is:** Market capitalisation divided by trailing-12-month revenue.\n"
                "- **How it's used:** Used for pre-profit companies where P/E doesn't apply; only compare within the same business model.\n"
                "- **High/low:** Below 5x typical for hardware; 5–15x for growth software; above 30x indicates a speculative bet on category dominance."
            ),
            "rule_of_40": (
                "- **What it is:** Revenue growth percentage plus FCF margin percentage.\n"
                "- **How it's used:** SaaS quality score combining growth and profitability — does the company offset slowing growth with rising margins?\n"
                "- **High/low:** Above 60% elite; 40–60% strong; 25–40% solid; below 20% is a quality warning, especially at Stage 3 and beyond."
            ),
            "arr": (
                "- **What it is:** Annualised recurring/subscription revenue base as reported by management (SEC 8-K earnings releases).\n"
                "- **How it's used:** The forward revenue base that drives SaaS valuations — quality signals are growth rate and net revenue retention, not the level.\n"
                "- **High/low:** Level alone tells you size; pair with growth rate (above 30% strong, below 20% slowing) and NRR (above 115% expanding, below 100% churning)."
            ),
        }
        # Addition 3: all metrics with fair ranges per cell
        # Each tuple: (display_name, val_data_key, low, high, unit, kind)
        # kind: "multiple" (higher=expensive), "yield" (higher=cheaper), "score" (higher=better)
        # FAIR_RANGES_FULL is defined at module level (shared with Home/Watchlist pages)
        # Addition 4: FH stage weights (mirrors data_pipeline1.py FH_STAGE_WEIGHTS)
        FH_STAGE_WEIGHTS_UI = {
            1: {"FCF Margin (adj)": 0.30, "Cash Runway": 0.40, "Gross Margin": 0.15, "Op. Leverage": 0.00, "SBC % Rev": 0.10, "D/E Ratio": 0.05, "ROIC": 0.00},
            2: {"FCF Margin (adj)": 0.30, "Cash Runway": 0.15, "Gross Margin": 0.10, "Op. Leverage": 0.25, "SBC % Rev": 0.15, "D/E Ratio": 0.05, "ROIC": 0.00},
            3: {"FCF Margin (adj)": 0.30, "Cash Runway": 0.00, "Gross Margin": 0.15, "Op. Leverage": 0.20, "SBC % Rev": 0.05, "D/E Ratio": 0.10, "ROIC": 0.20},
            4: {"FCF Margin (adj)": 0.35, "Cash Runway": 0.00, "Gross Margin": 0.15, "Op. Leverage": 0.05, "SBC % Rev": 0.05, "D/E Ratio": 0.15, "ROIC": 0.25},
        }

        conn_vb = get_db_connection()

        if conn_vb is None:
            st.info("ℹ️ Valoura database not found. Ensure `valoura_backtest.db` is in the app directory.")
        else:
            cls_row = conn_vb.execute(
                "SELECT bm_category, fh_stage, matrix_cell, bm_confidence, bm_method, bm_llm_rationale, "
                "bm_decision_trace, bm_validators_json, "
                "fh_fcf_stage, fh_gm_stage, fh_runway_stage, fh_oplev_stage, "
                "fh_sbc_stage, fh_de_stage, fh_roic_stage, fh_weighted_score, fh_fcf_hard_cap_applied, "
                "as_of_date "
                "FROM classifications WHERE ticker=?",
                (ticker_symbol,)
            ).fetchone()

            if cls_row is None:
                st.info(f"ℹ️ **{ticker_symbol}** has not been classified by the CVE engine yet. "
                        "Run the data pipeline to classify this ticker.")
            else:
                (bm_category, fh_stage, matrix_cell, bm_confidence, bm_method, bm_llm_rationale,
                 bm_decision_trace_str, bm_validators_str,
                 fh_fcf_stage, fh_gm_stage, fh_runway_stage, fh_oplev_stage,
                 fh_sbc_stage, fh_de_stage, fh_roic_stage, fh_weighted_score, fh_fcf_hard_cap,
                 as_of_date) = cls_row

                stage_label = STAGE_LABELS.get(fh_stage, f"Stage {fh_stage}")

                # ── Watchlist bookmark toggle ────────────────────────────────
                from supabase_client import is_in_watchlist, add_to_watchlist, remove_from_watchlist, set_alert
                _wl_user = st.session_state.get("user") or {}
                _wl_uid = _wl_user.get("id")
                if not _wl_uid:
                    # Anonymous browsing — prompt to create an account instead
                    _anon_c1, _anon_c2 = st.columns([1.8, 4])
                    with _anon_c1:
                        if st.button("🔖 Log in to add to watchlist",
                                     key=f"wl_login_{ticker_symbol}", use_container_width=True):
                            st.session_state.auth_view = True
                            st.rerun()
                    with _anon_c2:
                        st.caption("Accounts get stage-change alerts and a personal watchlist.")
                else:
                    _wl_entry = is_in_watchlist(_wl_uid, ticker_symbol)
                    _bm_col1, _bm_col2, _bm_col3 = st.columns([1.6, 2.4, 3])
                    with _bm_col1:
                        if _wl_entry:
                            if st.button("🔖 In watchlist — remove", key=f"wl_toggle_{ticker_symbol}",
                                         type="primary", use_container_width=True):
                                remove_from_watchlist(_wl_uid, ticker_symbol)
                                st.rerun()
                        else:
                            if st.button("Add to watchlist 🔖", key=f"wl_toggle_{ticker_symbol}",
                                         use_container_width=True):
                                _alert_pref = st.session_state.get(f"wl_alert_pref_{ticker_symbol}", False)
                                _ok, _err = add_to_watchlist(_wl_uid, ticker_symbol, matrix_cell, fh_stage, _alert_pref)
                                if not _ok:
                                    st.error(_err or "Could not add to watchlist.")
                                st.rerun()
                    with _bm_col2:
                        if _wl_entry:
                            _alert_now = bool(_wl_entry.get("alert_on_stage_change"))
                            _alert_new = st.toggle("Alert me if this ticker changes stage",
                                                   value=_alert_now, key=f"wl_alert_{ticker_symbol}")
                            if _alert_new != _alert_now:
                                set_alert(_wl_uid, ticker_symbol, _alert_new)
                                st.rerun()
                        else:
                            st.toggle("Alert me if this ticker changes stage",
                                      key=f"wl_alert_pref_{ticker_symbol}",
                                      help="Applied when you add the ticker to your watchlist")

                # Preload valuation row (interpretation + valuation cards both need it)
                val_row = conn_vb.execute(
                    "SELECT primary_method, valuation_json FROM valuations WHERE ticker=?",
                    (ticker_symbol,)
                ).fetchone()
                if val_row:
                    primary_method, val_json_str = val_row
                    val_data = json.loads(val_json_str) if val_json_str else {}
                else:
                    primary_method, val_json_str, val_data = None, None, {}

                # ── Classification badges — full-width row, readable across the page ──
                _b1, _b2, _b3, _b4 = st.columns(4)
                _b1.metric("Business Model", bm_category.replace("_", " ").title())
                _b2.metric("Matrix Cell", matrix_cell)
                _b3.metric("FH Stage", f"Stage {fh_stage}")
                _b4.metric("Confidence", bm_confidence or "—")

                # ── Top-of-page: 4 fundamental metrics vs industry median ─────────────
                # (Replaces the deleted "Comparing to Industry" section from old Valuation Analysis)
                # Tooltip text uses markdown bullets — Streamlit renders these inside the help popup.
                HEADER_METRICS = {
                    "FCF Margin Adjusted": (
                        "fcf_margin_adj", "%",
                        "- **What it is:** Free cash flow margin, adjusted to add back stock-based compensation (SBC).\n"
                        "- **How it's used:** The cleanest read of true cash profitability — how much cash the business generates per dollar of revenue, ignoring accounting noise.\n"
                        "- **High/low:** Above 20% indicates a cash-generative business with pricing power; 5–15% is typical for mature scaling; below 0% means the company is burning cash."
                    ),
                    "Gross Margin": (
                        "gross_margin", "%",
                        "- **What it is:** Gross profit divided by revenue (revenue minus cost of goods sold).\n"
                        "- **How it's used:** Measures unit economics — how much revenue is left after the direct cost of producing or delivering the product.\n"
                        "- **High/low:** Above 70% is typical for software/SaaS; 40–60% for hardware or hyperscale infrastructure; below 30% suggests a commoditised product or scale-economics business."
                    ),
                    "Revenue Growth YoY": (
                        "revenue_growth_yoy", "%",
                        "- **What it is:** Percentage change in trailing-12-month revenue versus the prior 12 months.\n"
                        "- **How it's used:** Top-line momentum check; paired with margins to assess whether growth is profitable or just spending-driven.\n"
                        "- **High/low:** Above 30% indicates strong demand and market expansion; 10–20% is mature growth; below 5% suggests market saturation or competitive pressure."
                    ),
                    "Operating Leverage": (
                        "operating_leverage", "pp",
                        "- **What it is:** Operating income growth rate minus revenue growth rate (in percentage points).\n"
                        "- **How it's used:** Shows whether costs are scaling slower than revenue — positive numbers mean margins are expanding as the business grows.\n"
                        "- **High/low:** Above +5pp means strong margin expansion; near 0pp means linear scaling; negative means costs are growing faster than revenue (margin compression)."
                    ),
                }

                _conn_top = get_db_connection()
                _ticker_in_db = False
                if _conn_top is not None:
                    _ticker_in_db = _conn_top.execute(
                        "SELECT 1 FROM computed_metrics WHERE ticker=?", (ticker_symbol,)
                    ).fetchone() is not None

                if not _ticker_in_db:
                    st.info(
                        f"ℹ️ **{ticker_symbol}** is not yet in the CVE database. "
                        "Run the data pipeline to ingest fundamentals + classify this ticker."
                    )
                else:
                    # Compute industry median for each header metric across all classified tickers
                    import statistics as _stats
                    _cols = [v[0] for v in HEADER_METRICS.values()]
                    _medians = {}
                    for _col in _cols:
                        _vals = [
                            r[0] for r in _conn_top.execute(
                                f"SELECT {_col} FROM computed_metrics WHERE {_col} IS NOT NULL"
                            ).fetchall()
                        ]
                        _medians[_col] = _stats.median(_vals) if _vals else None

                    _current_row = _conn_top.execute(
                        f"SELECT {', '.join(_cols)} FROM computed_metrics WHERE ticker=?",
                        (ticker_symbol,)
                    ).fetchone()
                    _cur_vals = dict(zip(_cols, _current_row)) if _current_row else {}

                    _hc1, _hc2, _hc3, _hc4 = st.columns(4)
                    _cols_ui = [_hc1, _hc2, _hc3, _hc4]
                    for _idx, (_label, (_col, _unit, _tip)) in enumerate(HEADER_METRICS.items()):
                        _cur = _cur_vals.get(_col)
                        _med = _medians.get(_col)
                        # Unit lives in the label, not in the value/delta
                        _label_with_unit = f"{_label} ({_unit})"
                        with _cols_ui[_idx]:
                            if _cur is None:
                                st.metric(_label_with_unit, "—", help=_tip)
                            else:
                                _value_str = f"{_cur:.1f}"
                                if _med is not None:
                                    _delta = _cur - _med
                                    _delta_str = f"{_delta:+.1f} vs median ({_med:.1f})"
                                    # delta_color="normal" → green if positive, red if negative.
                                    # All 4 metrics are "higher is better" so default is correct.
                                    st.metric(_label_with_unit, _value_str, delta=_delta_str, help=_tip)
                                else:
                                    st.metric(_label_with_unit, _value_str, help=_tip)

                    st.markdown("---")

                # ── Two-column layout: matrix (left 40%) | interpretation (right 60%) ──
                _col_L, _col_R = st.columns([2, 3], gap="large")


                with _col_L:
                    # ── Section 3: Matrix 5×4 position visualiser ─────────────────
                    # Short labels (fit narrow screens — no horizontal scroll)
                    CAT_SHORT = {
                        "hyperscale":        "Hyperscale",
                        "saas":              "SaaS",
                        "semi_hardware":     "Semi/HW",
                        "consumer_internet": "Consumer",
                        "deep_tech":         "Deep Tech",
                    }

                    grid_rows = []
                    header_ths = (
                        '<th style="background:#1e293b;color:#cbd5e1;padding:8px 6px;'
                        'font-size:0.78em;border:1px solid #64748b;"></th>'
                    )
                    for cat in BM_CATEGORIES:
                        header_ths += (
                            f'<th style="background:#1e293b;color:#e2e8f0;padding:8px 4px;'
                            f'font-size:0.82em;font-weight:700;border:1px solid #64748b;'
                            f'text-align:center;">{CAT_SHORT[cat]}</th>'
                        )
                    grid_rows.append(f'<tr>{header_ths}</tr>')

                    for s in [1, 2, 3, 4]:
                        stage_th = (
                            f'<td style="background:#1e293b;color:#e2e8f0;font-size:0.82em;'
                            f'font-weight:700;padding:8px 6px;border:1px solid #64748b;'
                            f'white-space:nowrap;">Stage {s}</td>'
                        )
                        tds = stage_th
                        for cat in BM_CATEGORIES:
                            is_current = (cat == bm_category and s == fh_stage)
                            same_stage = (s == fh_stage and not is_current)
                            same_cat   = (cat == bm_category and not is_current)
                            # Brighter labels — visible against the dark theme
                            label_line = (
                                f"{CAT_SHORT[cat]}<br>"
                                f"<span style='font-size:0.85em;opacity:0.95;'>S{s}</span>"
                            )
                            if is_current:
                                td_style  = ("background:#1b3a5c;border:2.5px solid #fbbf24;"
                                             "color:#fde68a;font-weight:700;")
                                content   = (
                                    f"{CAT_SHORT[cat]}<br>"
                                    f"<span style='font-size:0.85em;opacity:0.95;'>S{s}</span><br>"
                                    f"<span style='font-size:0.92em;color:#7dd3fc;font-weight:700;'>▶ {ticker_symbol}</span>"
                                )
                            elif same_stage:
                                # Brighter blue text on dim blue background, grey outline
                                td_style  = "background:#1e293b;border:1px solid #64748b;color:#bfdbfe;"
                                content   = label_line
                            elif same_cat:
                                # Brighter green text on dim green background, grey outline
                                td_style  = "background:#1a2e22;border:1px solid #64748b;color:#bbf7d0;"
                                content   = label_line
                            else:
                                # Lighter grey text on very dim background, grey outline
                                td_style  = "background:#0f172a;border:1px solid #475569;color:#94a3b8;"
                                content   = label_line
                            tds += (
                                f'<td style="{td_style}padding:10px 4px;text-align:center;'
                                f'font-size:0.82em;width:20%;">{content}</td>'
                            )
                        grid_rows.append(f'<tr>{tds}</tr>')

                    st.markdown(
                        '<div style="overflow-x:auto;margin:12px 0 4px 0;">'
                        '<table style="border-collapse:collapse;width:100%;table-layout:fixed;">'
                        + "".join(grid_rows)
                        + '</table></div>',
                        unsafe_allow_html=True,
                    )
                    st.caption("🟡 Current cell  ·  blue = same stage  ·  green = same category  ·  grey = other")

                    st.markdown("---")


                with _col_L:
                    # ── Section 4: Financial Health diagnosis (MAIN visible section) ─
                    st.subheader(f"Financial Health: Stage {fh_stage} — {stage_label.split('— ', 1)[-1]}")
                    st.caption(
                        f"Weighted score **{fh_weighted_score:.2f}** · "
                        f"Classification confidence **{bm_confidence or '—'}**"
                    )

                    # Always-visible sub-score table
                    _fh_sub_scores = {
                        "FCF Margin (adj)": fh_fcf_stage,
                        "Gross Margin":     fh_gm_stage,
                        "Cash Runway":      fh_runway_stage,
                        "Op. Leverage":     fh_oplev_stage,
                        "SBC % Rev":        fh_sbc_stage,
                        "D/E Ratio":        fh_de_stage,
                        "ROIC":             fh_roic_stage,
                    }
                    _weights_ui = FH_STAGE_WEIGHTS_UI.get(fh_stage, FH_STAGE_WEIGHTS_UI[3])
                    _fh_table_rows = []
                    for _m_name, _sub_score in _fh_sub_scores.items():
                        _w = _weights_ui.get(_m_name, 0)
                        _contrib = round(_w * _sub_score, 3) if _w > 0 else None
                        _fh_table_rows.append({
                            "Metric":       _m_name,
                            "Sub-score":    _sub_score,
                            "Weight":       f"{_w*100:.0f}" if _w > 0 else "—",
                            "Contribution": f"{_contrib:.3f}" if _contrib is not None else "—",
                        })
                    _fh_table_rows.append({
                        "Metric":       "Weighted total",
                        "Sub-score":    "—",
                        "Weight":       "—",
                        "Contribution": f"{fh_weighted_score:.3f} → Stage {fh_stage}",
                    })
                    render_dark_table(
                        ["Metric", "Sub-score (1–4)", "Weight (%)", "Contribution"],
                        [[r["Metric"], r["Sub-score"], r["Weight"], r["Contribution"]] for r in _fh_table_rows],
                    )

                    # Always-visible FCF hard-cap status banner
                    if fh_fcf_hard_cap:
                        st.warning("⚠️ FCF hard-cap applied: negative FCF margin capped the stage at Stage 2 regardless of other sub-scores.")
                    else:
                        st.success("✅ FCF hard-cap not applied.")

                    # Small expander — methodology + weighting breakdown only
                    with st.expander("🔎 How was this calculated?"):
                        st.markdown(
                            "The stage is a **weighted average of seven sub-scores** (each 1–4), "
                            "where the weights themselves vary by stage to reflect what matters most "
                            "at each phase. Stage 1 emphasises survival metrics (runway, FCF); Stage 4 "
                            "emphasises efficiency and capital allocation (ROIC, D/E)."
                        )
                        _weight_str = " · ".join(
                            f"**{_m}**: {_w:.0%}"
                            for _m, _w in _weights_ui.items()
                            if _w > 0
                        )
                        st.markdown(f"**Stage {fh_stage} weights →** {_weight_str}")

                    st.markdown("---")

                    # ── Section 4b: Financial Health Trajectory ───────────────────
                    st.subheader("Financial Health Trajectory")
                    _traj_rows = conn_vb.execute(
                        "SELECT as_of_date, fh_stage, fh_weighted_score "
                        "FROM fh_stage_history "
                        "WHERE ticker = ? ORDER BY as_of_date DESC LIMIT 8",
                        (ticker_symbol,),
                    ).fetchall()

                    if not _traj_rows:
                        st.info(
                            f"No historical FH stages recorded for **{ticker_symbol}** yet. "
                            "Run `python3 backfill_fh_history.py` (zero AV credits) to populate the last 8 quarters."
                        )
                    else:
                        # Reverse to chronological order (oldest → newest)
                        _traj = list(reversed(_traj_rows))

                        # Helper: format YYYY-MM-DD to Q-label (e.g. "2024-12-31" → "Q4 2024")
                        def _q_label(date_str):
                            try:
                                y, m, _ = date_str.split("-")
                                q = (int(m) - 1) // 3 + 1
                                return f"Q{q} {y}"
                            except Exception:
                                return date_str

                        # Per-stage colour palette (matches the matrix grid scheme)
                        _stage_colour = {
                            1: "#ef4444",  # red — pre-revenue / speculative
                            2: "#f59e0b",  # amber — growth / scaling
                            3: "#3b82f6",  # blue — mature growth
                            4: "#22c55e",  # green — cash generation
                        }
                        _stage_label = {
                            1: "Pre-revenue",
                            2: "Growth",
                            3: "Mature",
                            4: "FCF",
                        }

                        # Build the inline timeline as a single HTML row
                        _pills_html = '<div style="display:flex;align-items:center;justify-content:flex-start;flex-wrap:wrap;gap:4px;margin:8px 0 4px 0;overflow-x:auto;padding:6px 0;">'
                        _n_transitions = 0
                        for _i, (_d, _stage, _score) in enumerate(_traj):
                            _colour = _stage_colour.get(_stage, "#64748b")
                            _qlbl   = _q_label(_d)
                            _score_str = f"{_score:.2f}" if _score is not None else "—"
                            _pills_html += (
                                f'<div title="Weighted score: {_score_str}" '
                                f'style="background:{_colour};color:#0f172a;padding:8px 12px;'
                                f'border-radius:10px;text-align:center;font-family:Georgia,serif;'
                                f'min-width:88px;box-shadow:0 1px 4px rgba(0,0,0,0.3);'
                                f'border:1.5px solid rgba(255,255,255,0.15);">'
                                f'<div style="font-size:0.75em;opacity:0.85;font-weight:600;">{_qlbl}</div>'
                                f'<div style="font-size:0.95em;font-weight:800;line-height:1.1;">Stage {_stage}</div>'
                                f'<div style="font-size:0.65em;opacity:0.75;font-style:italic;">{_stage_label.get(_stage, "")}</div>'
                                f'</div>'
                            )
                            # Arrow between pills (only between pills, not after the last one)
                            if _i < len(_traj) - 1:
                                _next_stage = _traj[_i + 1][1]
                                _delta = (_next_stage - _stage) if (_next_stage is not None and _stage is not None) else 0
                                if _delta != 0:
                                    _n_transitions += 1
                                    _delta_colour = "#22c55e" if _delta > 0 else "#ef4444"
                                    _delta_sign = "↑" if _delta > 0 else "↓"
                                    _pills_html += (
                                        f'<div style="display:flex;flex-direction:column;align-items:center;'
                                        f'margin:0 4px;color:#38bdf8;font-size:1.4em;font-weight:800;'
                                        f'line-height:1;">'
                                        f'<div>⇒</div>'
                                        f'<div style="font-size:0.55em;color:{_delta_colour};margin-top:2px;'
                                        f'font-weight:700;">{_delta_sign} {_delta:+d}</div>'
                                        f'</div>'
                                    )
                                else:
                                    _pills_html += (
                                        '<div style="color:#64748b;font-size:1.2em;margin:0 2px;'
                                        'opacity:0.6;">→</div>'
                                    )
                        _pills_html += '</div>'

                        st.markdown(_pills_html, unsafe_allow_html=True)
                        _plural = "s" if _n_transitions != 1 else ""
                        st.caption(
                            f"{_n_transitions} stage transition{_plural} in the last {len(_traj)} quarters. "
                            f"Hover any pill for its weighted score."
                        )

                    st.markdown("---")


                with _col_R:
                    # ── Row 5: VALOURA INTERPRETATION (Gemini-powered) ────────────
                    _interp_hdr_l, _interp_hdr_r = st.columns([5, 1])
                    with _interp_hdr_l:
                        st.subheader("CVE Interpretation")
                    with _interp_hdr_r:
                        _regen_clicked = st.button(
                            "🔄 Regenerate",
                            key=f"regen_interp_{ticker_symbol}",
                            help="Clear cache and re-call Gemini",
                            use_container_width=True,
                        )

                    _interp_cache_key = f"valoura_interp_{ticker_symbol}_{as_of_date}"
                    if _regen_clicked and _interp_cache_key in st.session_state:
                        del st.session_state[_interp_cache_key]

                    if _interp_cache_key not in st.session_state:
                        # Build the context payload from the data we already have in scope
                        _cur_metrics_row = conn_vb.execute(
                            "SELECT fcf_margin_adj, gross_margin, revenue_growth_yoy, "
                            "operating_leverage, sbc_pct_revenue, debt_equity_ratio, "
                            "net_income_quality_flag, is_self_funded "
                            "FROM computed_metrics WHERE ticker=?",
                            (ticker_symbol,),
                        ).fetchone()
                        if _cur_metrics_row:
                            (_cm_fcf, _cm_gm, _cm_rg, _cm_opl, _cm_sbc, _cm_de,
                             _cm_ni_flag, _cm_self_fund) = _cur_metrics_row
                        else:
                            _cm_fcf = _cm_gm = _cm_rg = _cm_opl = _cm_sbc = _cm_de = None
                            _cm_ni_flag = _cm_self_fund = None

                        # valuation_metrics: zip fair-range table with current values
                        _val_metrics = []
                        _val_data_local = val_data if (val_row and val_json_str) else {}
                        for (_dn, _vk, _lo, _hi, _u, _kind) in FAIR_RANGES_FULL.get(matrix_cell, []):
                            _cv = _val_data_local.get(_vk)
                            _verdict = None
                            if _cv is not None:
                                try:
                                    _cvf = float(_cv)
                                    if _kind == "yield":
                                        _verdict = "fair" if _lo <= _cvf <= _hi else ("cheap" if _cvf > _hi else "rich")
                                    elif _kind == "score":
                                        _verdict = "good" if _lo <= _cvf <= _hi else ("strong" if _cvf > _hi else "weak")
                                    else:
                                        _verdict = "fair" if _lo <= _cvf <= _hi else ("cheap" if _cvf < _lo else "rich")
                                except (TypeError, ValueError):
                                    pass
                            _val_metrics.append({
                                "metric_name": _dn,
                                "metric_value": _cv,
                                "fair_range_low": _lo,
                                "fair_range_high": _hi,
                                "unit": _u,
                                "verdict": _verdict,
                            })

                        # cell_peers: tickers in same matrix_cell with primary metric value
                        _cell_peer_rows = conn_vb.execute(
                            "SELECT c.ticker, v.primary_method, v.valuation_json "
                            "FROM classifications c LEFT JOIN valuations v ON c.ticker=v.ticker "
                            "WHERE c.matrix_cell=?",
                            (matrix_cell,),
                        ).fetchall()
                        _cell_peers = []
                        _primary_keys_for_cell = [t[1] for t in FAIR_RANGES_FULL.get(matrix_cell, [])][:1]
                        _primary_key = _primary_keys_for_cell[0] if _primary_keys_for_cell else None
                        for _pr_ticker, _pr_method, _pr_json in _cell_peer_rows:
                            _pv = None
                            if _pr_json and _primary_key:
                                try:
                                    _pv = json.loads(_pr_json).get(_primary_key)
                                except Exception:
                                    pass
                            _cell_peers.append({
                                "ticker": _pr_ticker,
                                "primary_metric": _primary_key,
                                "primary_value": _pv,
                            })

                        # cell_medians: 4 header metrics for same-cell tickers
                        _cell_med_query = conn_vb.execute(
                            "SELECT m.fcf_margin_adj, m.gross_margin, "
                            "m.revenue_growth_yoy, m.operating_leverage "
                            "FROM classifications c JOIN computed_metrics m ON c.ticker=m.ticker "
                            "WHERE c.matrix_cell=?",
                            (matrix_cell,),
                        ).fetchall()
                        import statistics as _stats2
                        def _med(values):
                            vs = [v for v in values if v is not None]
                            return _stats2.median(vs) if vs else None
                        _cell_medians = {
                            "fcf_margin_adj":     _med([r[0] for r in _cell_med_query]),
                            "gross_margin":       _med([r[1] for r in _cell_med_query]),
                            "revenue_growth_yoy": _med([r[2] for r in _cell_med_query]),
                            "operating_leverage": _med([r[3] for r in _cell_med_query]),
                        }

                        # rpo_spread
                        _rpo_spread = _val_data_local.get("rpo_spread_pp")

                        # active_flags — synthesise from quality / qualifier fields
                        _flags = []
                        if _cm_ni_flag:
                            _flags.append("net_income_quality_flag")
                        if fh_fcf_hard_cap:
                            _flags.append("fh_fcf_hard_cap_applied")
                        if _val_data_local.get("rpo_qualifier_note"):
                            _flags.append(f"rpo:{_val_data_local['rpo_qualifier_note']}")
                        if _cm_self_fund == 0:
                            _flags.append("not_self_funded")

                        _context = {
                            "ticker":               ticker_symbol,
                            "as_of_date":           as_of_date,
                            "bm_category":          bm_category,
                            "fh_stage":             fh_stage,
                            "matrix_cell":          matrix_cell,
                            "fh_weighted_score":    fh_weighted_score,
                            "fcf_margin_adj":       _cm_fcf,
                            "gross_margin":         _cm_gm,
                            "revenue_growth_yoy":   _cm_rg,
                            "operating_leverage":   _cm_opl,
                            "sbc_pct_revenue":      _cm_sbc,
                            "debt_equity_ratio":    _cm_de,
                            "valuation_metrics":    _val_metrics,
                            "cell_peers":           _cell_peers,
                            "cell_medians":         _cell_medians,
                            "rpo_spread":           _rpo_spread,
                            "active_flags":         _flags,
                        }

                        with st.spinner("Generating CVE interpretation..."):
                            _result, _err = _generate_valoura_interpretation(_context)
                        if _result:
                            st.session_state[_interp_cache_key] = _result
                        else:
                            st.session_state[_interp_cache_key] = {"_error": _err}

                    _interp = st.session_state.get(_interp_cache_key, {})
                    if "_error" in _interp:
                        st.info("Interpretation unavailable — Gemini API error. Check GEMINI_API_KEY and credits.")
                        st.caption(f"_Debug: {_interp.get('_error', 'unknown')}_")
                    else:
                        # Render structured fields with labelled blocks
                        if _interp.get("classification_summary"):
                            st.markdown("**Classification summary**")
                            st.write(_interp["classification_summary"])
                        if _interp.get("valuation_position"):
                            st.markdown("**Valuation position**")
                            st.write(_interp["valuation_position"])
                        if _interp.get("strongest_signal"):
                            st.markdown("**Strongest signal**")
                            st.write(_interp["strongest_signal"])
                        if _interp.get("tension"):
                            st.markdown("**Tension in the data**")
                            st.write(_interp["tension"])
                        if _interp.get("peer_context"):
                            st.markdown("**Peer context**")
                            st.write(_interp["peer_context"])
                        if _interp.get("data_caveats"):
                            st.markdown("🔍 **Data caveats**")
                            st.write(_interp["data_caveats"])

                    st.markdown(
                        "<p style='color:#94a3b8;font-style:italic;font-size:0.82em;"
                        "margin-top:14px;border-top:1px solid #1e293b;padding-top:10px;'>"
                        "This interpretation is generated from quantitative classification data only. "
                        "It is not financial advice and does not constitute a recommendation to buy "
                        "or sell any security."
                        "</p>",
                        unsafe_allow_html=True,
                    )

                with _col_R:
                    # ── Row 2: Valuation metrics ──────────────────────────────────
                    if val_row:

                        st.subheader(f"Valuation — {primary_method or 'Context-specific'}")

                        # Only show non-None, non-RPO entries (layout unchanged)
                        display_items = [
                            (k, v) for k, v in val_data.items()
                            if v is not None and k in METRIC_LABELS
                        ]

                        if display_items:
                            st.caption("🔍 *Hover over the ⓘ icon next to each metric label for what the metric is, how it's used, and what high/low values typically mean.*")
                            metric_cols = st.columns(min(len(display_items), 4))
                            for idx, (key, value) in enumerate(display_items):
                                label, suffix = METRIC_LABELS[key]
                                col = metric_cols[idx % len(metric_cols)]

                                # 3-bullet tooltip (what / how used / interpretation) — renders on hover
                                help_text = METRIC_TOOLTIPS.get(
                                    key,
                                    "See CLAUDE.md for metric definitions."
                                )

                                try:
                                    if suffix == "%":
                                        col.metric(label, f"{float(value):.1f}%", help=help_text)
                                    elif suffix == "$B":
                                        col.metric(label, f"${float(value)/1e9:.1f}B", help=help_text)
                                    else:
                                        col.metric(label, f"{float(value):.2f}x", help=help_text)
                                except (TypeError, ValueError):
                                    col.metric(label, str(value), help=help_text)

                        # RPO qualifier banner (unchanged)
                        rpo_q = val_data.get("rpo_qualifier")
                        if rpo_q:
                            rpo_icons = {
                                "forward_demand_ahead":        "📈",
                                "neutral":                     "↔️",
                                "forward_demand_decelerating": "📉",
                            }
                            rpo_icon   = rpo_icons.get(rpo_q, "📊")
                            rpo_spread = val_data.get("rpo_spread_pp")
                            spread_str = ""
                            if rpo_spread is not None:
                                spread_str = f" (+{rpo_spread:.1f}pp)" if rpo_spread >= 0 else f" ({rpo_spread:.1f}pp)"
                            rpo_note = val_data.get("rpo_qualifier_note", "")
                            st.info(
                                f"{rpo_icon} **RPO Signal:** {rpo_q.replace('_', ' ').title()}"
                                f"{spread_str}" + (f" — {rpo_note}" if rpo_note else "")
                            )

                        # Addition 3: Full fair-range reference — 3 columns only (no Verdict)
                        fair_rows = FAIR_RANGES_FULL.get(matrix_cell, [])
                        if fair_rows:
                            th_s = ("padding:14px 18px;text-align:left;color:#7dd3fc;"
                                    "font-size:1.05em;font-weight:700;"
                                    "border-bottom:2px solid rgba(56,189,248,0.35);"
                                    "background:rgba(14,165,233,0.07);")
                            td_s = ("padding:13px 18px;font-size:1.02em;font-weight:500;"
                                    "border-bottom:1px solid #334155;color:#f1f5f9;")
                            rows_html = []
                            for (disp_name, val_key, fr_low, fr_high, fr_unit, kind) in fair_rows:
                                cur_raw = val_data.get(val_key)
                                # Unit lives in the Metric name now — strip from values
                                cur_str = "—" if cur_raw is None else (
                                    f"{float(cur_raw):.1f}"
                                    if isinstance(cur_raw, (int, float))
                                    else str(cur_raw)
                                )
                                rows_html.append(
                                    f'<tr>'
                                    f'<td style="{td_s}font-weight:600;">{disp_name} ({fr_unit})</td>'
                                    f'<td style="{td_s}text-align:center;">{fr_low}–{fr_high}</td>'
                                    f'<td style="{td_s}text-align:center;font-weight:700;color:#38bdf8;">{cur_str}</td>'
                                    f'</tr>'
                                )
                            st.markdown(
                                '<div style="background:rgba(15,23,42,0.6);'
                                'border:2px solid rgba(56,189,248,0.3);'
                                'border-radius:12px;padding:6px;margin:18px 0 8px 0;'
                                'box-shadow:0 4px 14px rgba(0,0,0,0.3);">'
                                '<div style="font-size:1.3em;font-weight:800;color:#7dd3fc;'
                                f'padding:14px 18px 8px 18px;font-family:Times New Roman,Times,serif;">'
                                f'Fair-Range Reference — {matrix_cell}'
                                '</div>'
                                '<table style="border-collapse:collapse;width:100%;">'
                                '<thead><tr>'
                                f'<th style="{th_s}">Metric</th>'
                                f'<th style="{th_s}text-align:center;">Fair Range</th>'
                                f'<th style="{th_s}text-align:center;">Current</th>'
                                '</tr></thead><tbody>'
                                + "".join(rows_html)
                                + '</tbody></table></div>',
                                unsafe_allow_html=True,
                            )

                        st.markdown("---")


                with _col_R:
                    # ── Section 10: Compare to Industry table ─────────────────────
                    # Dual mode:
                    #   Mode A: ≥3 tickers in same matrix_cell → show that cell's
                    #           primary metric for each peer + Good/Fair/Poor verdict
                    #   Mode B: <3 in cell → broaden to same bm_category and show
                    #           3–4 BM-relevant multiples side-by-side
                    _bm_label = CAT_DISPLAY.get(bm_category, bm_category.replace("_", " ").title())

                    # BM-relevant multiples (Mode B columns)
                    BM_COMPARE_COLS = {
                        "hyperscale": [
                            ("capex_adj_ev_ebit", "CapEx EV/EBIT", "x"),
                            ("peg_ratio",          "PEG",           "x"),
                            ("ev_ebitda",          "EV/EBITDA",     "x"),
                            ("rule_of_40",         "Rule of 40",    "%"),
                        ],
                        "saas": [
                            ("ev_ntm_arr",         "EV/NTM ARR",    "x"),
                            ("ev_fcf",             "EV/FCF",        "x"),
                            ("peg_ratio",          "PEG",           "x"),
                            ("rule_of_40",         "Rule of 40",    "%"),
                        ],
                        "semi_hardware": [
                            ("cycle_adj_pe",       "Cycle P/E",     "x"),
                            ("peg_ratio",          "PEG",           "x"),
                            ("ev_ebitda",          "EV/EBITDA",     "x"),
                            ("rule_of_40",         "Rule of 40",    "%"),
                        ],
                        "consumer_internet": [
                            ("ev_ebitda",          "EV/EBITDA",     "x"),
                            ("peg_ratio",          "PEG",           "x"),
                            ("pe_ratio",           "P/E",           "x"),
                            ("rule_of_40",         "Rule of 40",    "%"),
                        ],
                        "deep_tech": [
                            ("ps_ratio",           "P/S",           "x"),
                            ("ev_gross_profit",    "EV/GP",         "x"),
                            ("ev_ntm_arr",         "EV/NTM Rev",    "x"),
                            ("rule_of_40",         "Rule of 40",    "%"),
                        ],
                    }

                    # Shared formatters — UNIT-LESS (the unit lives in the column header now)
                    def _fmt_pct(v):
                        return "—" if v is None or pd.isna(v) else f"{float(v):.1f}"
                    def _fmt_pp(v):
                        return "—" if v is None or pd.isna(v) else f"{float(v):.1f}"
                    def _fmt_x(v):
                        return "—" if v is None or pd.isna(v) else f"{float(v):.2f}"
                    def _fmt_unit(v, unit):
                        if v is None or pd.isna(v):
                            return "—"
                        if unit == "%":
                            return f"{float(v):.1f}"
                        return f"{float(v):.2f}"

                    # How many tickers share the current matrix_cell?
                    _cell_count = conn_vb.execute(
                        "SELECT COUNT(*) FROM classifications WHERE matrix_cell = ?", (matrix_cell,)
                    ).fetchone()[0]

                    _MODE_A_THRESHOLD = 3

                    st.subheader(f"Compare to Industry — {_bm_label}")

                    if _cell_count >= _MODE_A_THRESHOLD:
                        # ── MODE A: same matrix cell, value on cell's primary metric ──
                        _fair_rows_for_cell = FAIR_RANGES_FULL.get(matrix_cell, [])
                        if _fair_rows_for_cell:
                            _disp_name, _val_key, _lo, _hi, _u, _kind = _fair_rows_for_cell[0]
                        else:
                            _disp_name, _val_key, _lo, _hi, _u, _kind = ("Primary", None, None, None, "x", "multiple")

                        # Get cell's primary method (consistent across cell)
                        _pm_row = conn_vb.execute(
                            "SELECT primary_method FROM valuations WHERE matrix_cell = ? LIMIT 1",
                            (matrix_cell,)
                        ).fetchone()
                        _primary_method = _pm_row[0] if _pm_row else "—"

                        st.caption(
                            f"All tickers in matrix cell **{matrix_cell}** — valued on the "
                            f"cell's primary method ({_primary_method})."
                        )

                        _select_cols = (
                            "c.ticker, c.fh_stage, "
                            "m.fcf_margin_adj, m.gross_margin, m.revenue_growth_yoy, m.operating_leverage, "
                            f"v.{_val_key} AS primary_val" if _val_key else
                            "c.ticker, c.fh_stage, "
                            "m.fcf_margin_adj, m.gross_margin, m.revenue_growth_yoy, m.operating_leverage, "
                            "NULL AS primary_val"
                        )
                        _peer_rows_raw = conn_vb.execute(
                            f"""
                            SELECT {_select_cols}
                            FROM classifications c
                            LEFT JOIN computed_metrics m ON c.ticker = m.ticker
                            LEFT JOIN valuations v ON c.ticker = v.ticker
                            WHERE c.matrix_cell = ?
                            ORDER BY c.ticker ASC
                            """,
                            (matrix_cell,),
                        ).fetchall()

                        _headers_a = ["Ticker", "FH Stage", "FCF Margin adj (%)", "Gross Margin (%)",
                                      "Rev Growth YoY (%)", "Op Leverage (pp)", f"{_disp_name} ({_u})"]
                        _rows_a = []
                        for r in _peer_rows_raw:
                            _rows_a.append([
                                r[0],
                                f"Stage {int(r[1])}" if r[1] is not None else "—",
                                _fmt_pct(r[2]),
                                _fmt_pct(r[3]),
                                _fmt_pct(r[4]),
                                _fmt_pp(r[5]),
                                _fmt_unit(r[6], _u),
                            ])
                        render_dark_table(_headers_a, _rows_a, highlight_first_col=ticker_symbol)
                        st.caption(
                            f"Peers in matrix cell **{matrix_cell}** — valued on the cell's primary method ({_primary_method}). "
                            f"Fair range for {_disp_name}: {_lo}–{_hi}{_u}."
                        )

                    elif _cell_count < _MODE_A_THRESHOLD:
                        # ── MODE B: broaden to same BM, show BM-relevant multiples ──
                        _bm_cols = BM_COMPARE_COLS.get(bm_category, [
                            ("ev_ebitda", "EV/EBITDA", "x"),
                            ("peg_ratio", "PEG",       "x"),
                            ("ev_fcf",    "EV/FCF",    "x"),
                            ("rule_of_40","Rule of 40","%"),
                        ])
                        st.caption(
                            f"Only **{_cell_count}** ticker(s) in matrix cell `{matrix_cell}` — "
                            f"broadened to all **{_bm_label}** peers. Columns show the most "
                            f"relevant valuation multiples for this BM."
                        )

                        _val_cols_sql = ", ".join(f"v.{k}" for (k, _, _) in _bm_cols)
                        _peer_rows_raw = conn_vb.execute(
                            f"""
                            SELECT c.ticker, c.matrix_cell, c.fh_stage,
                                   m.fcf_margin_adj, m.gross_margin,
                                   m.revenue_growth_yoy, m.operating_leverage,
                                   {_val_cols_sql}
                            FROM classifications c
                            LEFT JOIN computed_metrics m ON c.ticker = m.ticker
                            LEFT JOIN valuations v ON c.ticker = v.ticker
                            WHERE c.bm_category = ?
                            ORDER BY ABS(c.fh_stage - ?) ASC, c.ticker ASC
                            """,
                            (bm_category, fh_stage),
                        ).fetchall()

                        if len(_peer_rows_raw) > 1:
                            _headers_b = (["Ticker", "Matrix Cell", "FH Stage", "FCF Margin adj (%)",
                                           "Gross Margin (%)", "Rev Growth YoY (%)", "Op Leverage (pp)"]
                                          + [f"{lbl} ({_u})" for (_k, lbl, _u) in _bm_cols])
                            _rows_b = []
                            for r in _peer_rows_raw:
                                _row = [
                                    r[0],
                                    r[1],
                                    f"Stage {int(r[2])}" if r[2] is not None else "—",
                                    _fmt_pct(r[3]),
                                    _fmt_pct(r[4]),
                                    _fmt_pct(r[5]),
                                    _fmt_pp(r[6]),
                                ]
                                for _i, (_k, _label, _u) in enumerate(_bm_cols):
                                    _row.append(_fmt_unit(r[7 + _i], _u))
                                _rows_b.append(_row)
                            render_dark_table(_headers_b, _rows_b, highlight_first_col=ticker_symbol)
                            st.caption(
                                f"Showing {len(_rows_b)} **{_bm_label}** tickers. "
                                f"Same-stage peers grouped first."
                            )
                        else:
                            st.write(
                                f"No other **{_bm_label}** tickers in the universe yet — "
                                f"{ticker_symbol} is the only one classified so far."
                            )

                    st.markdown("---")


                with _col_R:
                    # ── Risk Flags panel (synthesized quality signals) ────────
                    st.subheader("Risk Flags")
                    _flags = get_active_flags(conn_vb, ticker_symbol)
                    if _flags:
                        for _fname, _fdesc in _flags:
                            st.warning(f"**{_fname}** — {_fdesc}")
                    else:
                        st.success("No active risk flags for this ticker.")

                # ── Section 5: Business Model 5-tab explainer ─────────────────
                _bm_display_now = CAT_DISPLAY.get(bm_category, bm_category.title())
                st.subheader(f"Business Model: {_bm_display_now}")
                st.caption(
                    f"Your ticker is classified as **{_bm_display_now}** — Stage {fh_stage}. "
                    "The assigned category's tab is highlighted; click other tabs to see what "
                    "each business model means."
                )

                # Tab labels carry the ⓘ glyph as a hover hint — the OS-native
                # tooltip (title= attribute) is injected via JS below using
                # BM_DESCRIPTIONS.
                _bm_tab_labels = [f"{CAT_DISPLAY[c]} ⓘ" for c in BM_CATEGORIES]
                _bm_tabs = st.tabs(_bm_tab_labels)
                for _i, _cat in enumerate(BM_CATEGORIES):
                    with _bm_tabs[_i]:
                        st.markdown(BM_DESCRIPTIONS.get(_cat, ""))
                        if _cat == bm_category:
                            st.success(
                                f"✓ **{ticker_symbol}** is classified here — "
                                f"Matrix Cell `{matrix_cell}` (Stage {fh_stage})."
                            )

                # Auto-select the assigned tab + apply the gold-underline highlight
                # + set browser-native `title=` tooltip on every BM tab using
                # BM_DESCRIPTIONS so users get the definition on hover.
                # st.tabs has no programmatic selection API, so we do it via JS.
                import json as _json
                _assigned_label_full = f"{CAT_DISPLAY[bm_category]} ⓘ"
                _bm_tooltip_map = {
                    f"{CAT_DISPLAY[_c]} ⓘ": BM_DESCRIPTIONS.get(_c, "")
                    for _c in BM_CATEGORIES
                }
                _tooltip_json = _json.dumps(_bm_tooltip_map)
                st.markdown(
                    f"""
                    <script>
                    (function() {{
                        const target = {_json.dumps(_assigned_label_full)};
                        const tooltips = {_tooltip_json};
                        const tagged = window.__valoura_bm_tagged || {{}};
                        const ticker_state = {_json.dumps(f"{ticker_symbol}|{matrix_cell}")};
                        if (tagged.__last === ticker_state && tagged.__tooltipped) return;
                        tagged.__last = ticker_state;
                        tagged.__tooltipped = true;
                        window.__valoura_bm_tagged = tagged;
                        setTimeout(function() {{
                            document.querySelectorAll('button[data-baseweb="tab"]').forEach(function(tab) {{
                                tab.removeAttribute('data-valoura-assigned');
                                const txt = (tab.textContent || '').trim();
                                // Set native tooltip if this tab is one of our BM tabs
                                if (tooltips.hasOwnProperty(txt)) {{
                                    tab.setAttribute('title', tooltips[txt]);
                                }}
                                if (txt === target) {{
                                    tab.setAttribute('data-valoura-assigned', 'true');
                                    if (tab.getAttribute('aria-selected') !== 'true') {{
                                        tab.click();
                                    }}
                                }}
                            }});
                        }}, 120);
                    }})();
                    </script>
                    """,
                    unsafe_allow_html=True,
                )

                st.markdown("---")


                # ── Section 6: "How was the Business Model decided?" expander ──
                with st.expander("🔎 How was the Business Model decided?"):
                    try:
                        trace      = json.loads(bm_decision_trace_str) if bm_decision_trace_str else {}
                        validators = json.loads(bm_validators_str)      if bm_validators_str     else []
                    except Exception:
                        trace, validators = {}, []

                    if bm_method == "override":
                        st.markdown(f"📌 **Manual override** → `{bm_category}` (MANUAL_BM_OVERRIDES dict)")
                    elif bm_method == "validator":
                        st.markdown(
                            f"✅ **Single validator passed** for `{bm_category}`: "
                            f"{trace.get('rationale', '—')}"
                        )
                    elif bm_method == "llm_tiebreaker":
                        contenders = trace.get("contenders", "—")
                        llm_info   = trace.get("llm", {})
                        llm_rat    = llm_info.get("rationale", "—") if isinstance(llm_info, dict) else str(llm_info)
                        st.markdown(f"🤖 **LLM tiebreaker** between `{contenders}` → chose `{bm_category}`")
                        st.markdown(f"*LLM rationale:* {llm_rat}")
                    else:
                        st.markdown(f"Method: `{bm_method or '—'}`")

                    if validators:
                        val_rows_display = []
                        for v in validators:
                            passed_str = "✅ yes" if v.get("passed") else ("⬜ abstain" if v.get("abstain") else "❌ no")
                            val_rows_display.append({
                                "Category":  v.get("category", "—"),
                                "Result":    passed_str,
                                "Conf.":     f"{v.get('confidence', 0):.1f}",
                                "Rationale": v.get("rationale", "—"),
                            })
                        render_dark_table(
                            ["Category", "Result", "Conf.", "Rationale"],
                            [[v["Category"], v["Result"], v["Conf."], v["Rationale"]] for v in val_rows_display],
                        )

                    if bm_method == "llm_tiebreaker":
                        st.info("🤖 LLM was invoked to break a validator tie.")
                    elif bm_method == "override":
                        st.info("📌 LLM not used — manual override took precedence.")
                    else:
                        st.success("✅ LLM not needed — rule-based classification was unambiguous.")

                st.markdown("---")



# --- Helper Function: Mock AI Analysis ---
def generate_ai_verdict(info, news, history, ticker=None):
    verdict = []
    sentiment_score = 0 # Range roughly -3 to +3

    # --- Addition 2: Prepend CVE classification context ---
    if ticker:
        try:
            conn_vb = get_db_connection()
            if conn_vb is not None:
                cls_row = conn_vb.execute(
                    "SELECT bm_category, fh_stage, matrix_cell FROM classifications WHERE ticker=?",
                    (ticker,)
                ).fetchone()
                if cls_row:
                    bm_category, fh_stage, matrix_cell = cls_row
                    stage_labels = {1: "Pre-revenue", 2: "Growth", 3: "Mature growth", 4: "Cash generation"}
                    stage_label = stage_labels.get(fh_stage, f"Stage {fh_stage}")

                    # Fetch primary metric from valuations
                    val_row = conn_vb.execute(
                        "SELECT primary_method, valuation_json FROM valuations WHERE ticker=?",
                        (ticker,)
                    ).fetchone()

                    valoura_line2 = ""
                    if val_row:
                        primary_method = val_row[0] or ""
                        try:
                            val_data = json.loads(val_row[1]) if val_row[1] else {}
                        except Exception:
                            val_data = {}

                        # Fair-range reference table (must match page definition above)
                        FAIR_RANGES = {
                            "hyperscale-4": ("FCF Yield", 1.5, 3.0, "%"),
                            "hyperscale-3": ("CapEx EV/EBIT", 20, 35, "x"),
                            "hyperscale-2": ("CapEx EV/EBIT", 40, 80, "x"),
                            "hyperscale-1": ("EV/NTM Rev", 6, 15, "x"),
                            "saas-4":       ("FCF Yield", 1.0, 2.5, "%"),
                            "saas-3":       ("EV/FCF", 25, 45, "x"),
                            "saas-2":       ("EV/NTM ARR", 8, 18, "x"),
                            "saas-1":       ("EV/NTM Rev", 8, 20, "x"),
                            "semi_hardware-4": ("FCF Yield", 2.0, 4.0, "%"),
                            "semi_hardware-3": ("Cycle P/E", 20, 35, "x"),
                            "semi_hardware-2": ("EV/NTM Rev", 1.5, 4.0, "x"),
                            "semi_hardware-1": ("EV/NTM Rev", 0.5, 2.0, "x"),
                            "consumer_internet-4": ("EV/EBITDA", 12, 18, "x"),
                            "consumer_internet-3": ("EV/EBITDA", 18, 30, "x"),
                            "consumer_internet-2": ("EV/NTM Rev", 4, 10, "x"),
                            "consumer_internet-1": ("EV/NTM Rev", 1, 5, "x"),
                            "deep_tech-4": ("FCF Yield", 1.5, 3.0, "%"),
                            "deep_tech-3": ("EV/GP", 15, 30, "x"),
                            "deep_tech-2": ("EV/NTM Rev", 5, 12, "x"),
                            "deep_tech-1": ("P/S", 20, 60, "x"),
                        }

                        METRIC_KEYS = {
                            "hyperscale-3": "capex_adj_ev_ebit", "hyperscale-2": "capex_adj_ev_ebit",
                            "saas-4": "fcf_yield", "saas-3": "ev_fcf",
                            "saas-2": "ev_ntm_arr", "saas-1": "ev_ntm_arr",
                            "semi_hardware-4": "fcf_yield", "semi_hardware-3": "cycle_adj_pe",
                            "consumer_internet-4": "ev_ebitda", "consumer_internet-3": "ev_ebitda",
                            "deep_tech-3": "ev_gross_profit", "deep_tech-2": "ev_revenue",
                            "deep_tech-1": "ps_ratio",
                        }
                        metric_key = METRIC_KEYS.get(matrix_cell)
                        metric_value = val_data.get(metric_key) if metric_key else None
                        fair = FAIR_RANGES.get(matrix_cell)
                        if metric_value is not None and fair:
                            fr_metric, fr_low, fr_high, fr_unit = fair
                            if fr_unit == "%":
                                verdict_label = "✅ in range" if fr_low <= metric_value <= fr_high else ("🔥 rich" if metric_value < fr_low else "💰 cheap")
                                valoura_line2 = (
                                    f"📊 **CVE Valuation:** {primary_method} — "
                                    f"{fr_metric} = {metric_value:.1f}% ({verdict_label}) | Fair range: {fr_low}–{fr_high}%"
                                )
                            else:
                                verdict_label = "✅ in range" if fr_low <= metric_value <= fr_high else ("🔥 rich" if metric_value > fr_high else "💰 cheap")
                                valoura_line2 = (
                                    f"📊 **CVE Valuation:** {primary_method} — "
                                    f"{fr_metric} = {metric_value:.2f}x ({verdict_label}) | Fair range: {fr_low}–{fr_high}x"
                                )
                        elif primary_method:
                            valoura_line2 = f"📊 **CVE Valuation method:** {primary_method}"

                    bm_display = bm_category.replace("_", " ").title()
                    verdict.insert(0, f"🎯 **CVE Classification:** {bm_display} — {stage_label} | Matrix cell: **{matrix_cell}**")
                    if valoura_line2:
                        verdict.insert(1, valoura_line2)
        except Exception:
            pass  # Never let DB errors break the existing verdict display

    # 1. Valuation Check
    pe = info.get('trailingPE')
    if pe is not None:
        if pe < 15:
            verdict.append(f"🟢 **Value Opportunity:** P/E of {pe:.2f} suggests it's cheap relative to earnings.")
            sentiment_score += 1
        elif pe > 50:
            verdict.append(f"🔥 **Hot / Expensive:** P/E of {pe:.2f} is very high. Priced for perfection.")
            sentiment_score -= 1
        else:
            verdict.append(f"⚖️ **Fairly Valued:** P/E of {pe:.2f} is standard.")
    
    # 2. Trend Check
    if not history.empty:
        current_price = history['Close'].iloc[-1]
        ma_50 = history['Close'].tail(50).mean()
        if current_price > ma_50:
             verdict.append(f"🚀 **Momentum:** Trading ABOVE the 50-day moving average. Bulls are in control.")
             sentiment_score += 1
        else:
             verdict.append(f"📉 **Downtrend:** Trading BELOW the 50-day moving average. Caution advised.")
             sentiment_score -= 1
    
    # 3. Political/Trend Scan
    political_keywords = ['election', 'regulation', 'tariff', 'congress', 'senate', 'biden', 'trump', 'policy', 'tax', 'lawsuit', 'antitrust']
    found_political = False
    
    verdict.append("\n**🗞️ News Scanner:**")
    if news:
        for article in news[:5]:
            title = article.get('title', '').lower()
            if any(word in title for word in political_keywords):
                verdict.append(f"- ⚠️ **Political Radar:** \"{article['title']}\"")
                found_political = True
                sentiment_score -= 0.5 # Slight penalty for uncertainty
    
    if not found_political:
        verdict.append("- 🛡️ **Clear Skies:** No major political red flags in top headlines.")
        sentiment_score += 0.5

    return verdict, sentiment_score

# --- CONTROLLER ---
if __name__ == "__main__":
    # Login is OPTIONAL: anyone can browse. The auth page shows only when
    # the user explicitly opens it (login icon, or a feature that needs it).
    if st.session_state.get("auth_view") and not st.session_state.get("user"):
        render_auth_page()
        st.stop()
    main_dashboard()
