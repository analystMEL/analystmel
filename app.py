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
st.set_page_config(page_title="Valuora", page_icon="🌊", layout="wide")

# --- Valoura DB Connection ---
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
    Priority: (1) Valoura DB — same matrix_cell peers; (2) hardcoded sector map; (3) generic ETFs.
    """
    industry = info.get('industry', 'Unknown Industry')
    sector = info.get('sector', 'Unknown Sector')

    # --- Addition 3: Valoura matrix_cell peer lookup ---
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
                    return f"{industry} (Valoura: {matrix_cell})", peers[:5]
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
    summary += "Valoura AI predicts continued pressure on global shipping rates if this trend persists."
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
        
        /* --- Main App Background (Dark Navy) --- */
        .stApp {
            background: linear-gradient(135deg, #0a1128 0%, #1c2541 50%, #3a506b 100%);
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
            border-color: #6fffe9;
        }

        /* --- Formal Header (No gradient text) --- */
        .fun-header {
            font-size: 3em;
            font-weight: 800;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.5);
            font-family: 'Times New Roman', Times, serif;
            margin-bottom: 20px;
            border-bottom: 2px solid #3a506b;
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
            padding-top: 1rem !important;
            max-width: 100% !important;
        }

        /* --- Horizontal navigation bar --- */
        .valoura-topbar {
            background: linear-gradient(135deg, #0f172a 0%, #1e3a8a 60%, #0a1128 100%);
            border-radius: 14px;
            padding: 22px 20px 18px 20px;
            margin: 0 0 22px 0;
            text-align: center;
            box-shadow: 0 4px 18px rgba(0,0,0,0.35);
            border: 1px solid rgba(251,191,36,0.15);
        }
        .valoura-brand {
            font-family: 'Times New Roman', Times, serif;
            font-size: 2.3em;
            font-weight: 800;
            color: #ffffff;
            letter-spacing: 1.5px;
            text-shadow: 2px 2px 6px rgba(0,0,0,0.5);
            margin-bottom: 4px;
        }
        .valoura-tagline {
            color: #cbd5e1;
            font-size: 0.85em;
            font-style: italic;
            margin-bottom: 14px;
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
            background: rgba(251,191,36,0.18) !important;
            color: #fde68a !important;
            border-color: rgba(251,191,36,0.45) !important;
        }
        /* Active (primary) — gold gradient pill */
        div[data-testid="stButton"] > button[kind="primary"],
        button[data-testid="stBaseButton-primary"] {
            background: linear-gradient(135deg, #fbbf24 0%, #f59e0b 100%) !important;
            color: #0f172a !important;
            border: 1px solid #fde68a !important;
            border-radius: 999px !important;
            font-family: 'Times New Roman', Times, serif !important;
            font-weight: 700 !important;
            font-size: 0.95em !important;
            padding: 8px 18px !important;
            box-shadow: 0 2px 12px rgba(251,191,36,0.5) !important;
        }
        div[data-testid="stButton"] > button[kind="primary"]:hover,
        button[data-testid="stBaseButton-primary"]:hover {
            background: linear-gradient(135deg, #fde68a 0%, #fbbf24 100%) !important;
            color: #0f172a !important;
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

        /* Force borders on DataFrames */
        .stDataFrame, div[data-testid="stTable"] {
            border: 2px solid #3a506b;
            border-radius: 10px;
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

    # --- Horizontal Navigation (replaces sidebar) ---
    PAGES = [
        "Financial Analysis",
        "DCF Model",
        "Macro Stress Test",
        "Company Profile & Roadmap",
        "Valoura Analysis",
    ]

    if 'active_page' not in st.session_state:
        st.session_state.active_page = "Financial Analysis"

    # Header banner (visual only — no nav links inside)
    st.markdown(
        '<div class="valoura-topbar">'
        '<div class="valoura-brand">🌊 Valuora</div>'
        '<div class="valoura-tagline">Context-aware valuation engine</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    # Nav pills as real st.buttons (AJAX rerun, no full-page reload).
    # CSS in the main style block turns primary buttons into gold pills
    # and secondary buttons into translucent pills.
    _nav_cols = st.columns(len(PAGES))
    for _i, _p in enumerate(PAGES):
        with _nav_cols[_i]:
            if st.button(
                _p,
                key=f"nav_btn_{_i}",
                use_container_width=True,
                type=("primary" if _p == st.session_state.active_page else "secondary"),
            ):
                st.session_state.active_page = _p
                st.rerun()

    active = st.session_state.active_page

    # --- Control Row: Ticker input + Run Analysis button ---
    ctrl1, ctrl2, ctrl3 = st.columns([3, 1, 4])
    with ctrl1:
        ticker_symbol = st.text_input(
            "Stock Ticker",
            value=st.session_state.get("last_ticker", "AAPL"),
            help="Try: AAPL, MSFT, NVDA, GOOGL, QBTS, ASTS",
            label_visibility="collapsed",
            placeholder="Enter ticker (e.g. AAPL)",
        ).upper()
    with ctrl2:
        analyze_now = st.button("🚀 Run Analysis", use_container_width=True)
    with ctrl3:
        st.caption("💡 Try AAPL · MSFT · NVDA · QBTS · ASTS · POET")

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

    # Fetch Data
    if analyze_now or 'stock_data' in st.session_state:
        if analyze_now:
            # 1. Clean the ticker input
            clean_ticker = str(ticker_symbol).strip().upper()
            if not clean_ticker:
                st.error("Ticker symbol is empty. Please enter a valid symbol.")
                return

            # Clear cache and fetch fresh
            st.cache_resource.clear()
            stock, info = fetch_stock_data_v2(clean_ticker)

            if stock is None or info is None:
                st.error(
                    f"⚠️ Valoura cannot reach any data source for **{clean_ticker}**.\n\n"
                    f"All three fetchers failed: yfinance, yahooquery, and Alpha Vantage. "
                    f"This usually means the **ALPHA_VANTAGE_KEY** secret is not configured on Streamlit Cloud. "
                    f"Go to App Settings → Secrets and add:\n\n"
                    f"```toml\nALPHA_VANTAGE_KEY = \"your_key_here\"\n```"
                )
                st.stop()

            st.session_state.stock_data = (stock, info)
        else:
            stock, info = st.session_state.stock_data
    else:
        st.info("👋 Enter a ticker and click 'Run Analysis' to begin.")
        st.stop()

    # --- PAGE 1: Financial Analysis ---
    if page == "Financial Analysis":
        # Formal Header
        st.markdown(f'<div class="fun-header">Valuora: {ticker_symbol}</div>', unsafe_allow_html=True)
        st.markdown(f"**{info.get('longName', ticker_symbol)}** | Made by Om")
        
        with st.spinner("🤖 AI is reading the charts..."):
            hist = stock.history(period="max") # Fetch max for "All Time" calc
            chart_hist = hist.tail(504) # 2y for chart
            news = stock.news
        
        # Header Metrics (Glassmorphism)
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Current Price", f"${info.get('currentPrice', 'N/A')}")
        m2.metric("Market Cap", f"${info.get('marketCap', 0)/1e9:.2f}B" if info.get('marketCap') else "N/A")
        m3.metric("Beta (Vol)", f"{info.get('beta', 'N/A')}")
        m4.metric("52W High", f"${info.get('fiftyTwoWeekHigh', 'N/A')}")

        st.markdown("---")

        tabs = st.tabs(["🧠 AI Verdict", "📊 Live Charts", "📑 The Books"])

        # TAB 1: AI Judgment
        with tabs[0]:
            st.subheader("🤖 Valuora Verdict")
            verdict_points, score = generate_ai_verdict(info, news, hist, ticker=ticker_symbol)
            
            # Visual Sentiment Meter
            st.write(" **Market Sentiment Score:**")
            
            # Create a visual progress bar based on score
            # Score roughly -3 to +3. Normalize to 0-100 for progress bar.
            # 0 = -3 (Bearish), 50 = 0 (Neutral), 100 = +3 (Bullish)
            normalized_score = min(max((score + 3) / 6, 0.0), 1.0)
            
            if score >= 1:
                st.progress(normalized_score, text="Sentiment: BULLISH 🐂")
                st.success("The AI detects strong positive signals!")
            elif score <= -1:
                st.progress(normalized_score, text="Sentiment: BEARISH 🐻")
                st.error("The AI detects risks and negative trends.")
            else:
                st.progress(normalized_score, text="Sentiment: NEUTRAL 🦆")
                st.warning("The AI sees a mixed bag. Proceed with caution.")
            
            with st.expander("See Analysis Details", expanded=True):
                for point in verdict_points:
                    st.markdown(point)

        # TAB 2: Chart
        with tabs[1]:
            st.subheader("📊 Price Action")
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
                st.markdown("##### 📈 Historical Returns")
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

        # TAB 3: Financials
        with tabs[2]:
            fin_tabs = st.tabs(["Detailed View", "Simplified View"])
            
            # Process Balance Sheet to add Debt/Equity
            bs = stock.balance_sheet.copy()
            
            # Calculate Debt to Equity Ratio if possible
            # Standard keys: 'Total Debt', 'Total Equity Gross Minority Interest' (or 'Stockholders Equity')
            try:
                # Find Total Debt
                t_debt = None
                if 'Total Debt' in bs.index:
                    t_debt = bs.loc['Total Debt']
                elif 'Long Term Debt' in bs.index and 'Current Debt' in bs.index:
                    t_debt = bs.loc['Long Term Debt'] + bs.loc['Current Debt']
                
                # Find Equity
                t_equity = None
                if 'Total Equity Gross Minority Interest' in bs.index:
                    t_equity = bs.loc['Total Equity Gross Minority Interest']
                elif 'Stockholders Equity' in bs.index:
                    t_equity = bs.loc['Stockholders Equity']
                
                if t_debt is not None and t_equity is not None:
                    # Avoid division by zero
                    de_ratio = t_debt / t_equity.replace(0, np.nan)
                    
                    # Create a new DataFrame for the row to append
                    # We need to ensure the new row aligns with columns (dates)
                    de_row = pd.DataFrame(de_ratio).T
                    de_row.index = ["Debt to Equity Ratio"]
                    
                    # Concatenate
                    bs = pd.concat([de_row, bs])
            except Exception as e:
                # st.error(f"D/E Ratio Error: {e}") 
                pass

            # Styler Function for highlighting
            def highlight_de_row(s):
                if s.name == "Debt to Equity Ratio":
                    return ['background-color: #facc15; color: black; font-weight: bold' for _ in s]
                return ['' for _ in s]

            # 1. Detailed View
            with fin_tabs[0]:
                try:
                    # Robust styling: Check if row exists before applying specific formatting
                    if "Debt to Equity Ratio" in bs.index:
                        styler = bs.style.format("{:,.2f}", subset=pd.IndexSlice[["Debt to Equity Ratio"], :]) \
                                         .format("{:,.0f}", subset=bs.index.difference(["Debt to Equity Ratio"])) \
                                         .apply(highlight_de_row, axis=1)
                    else:
                        styler = bs.style.format("{:,.0f}")
                    st.dataframe(styler)
                except Exception as e:
                    st.dataframe(bs) # Fallback to raw dataframe if styling fails
            
            # 2. Simplified View
            with fin_tabs[1]:
                try:
                    def simplify_number(n):
                        try:
                            abs_n = abs(n)
                            if abs_n < 1000: # Small ratios
                                return f"{n:.2f}"
                            if abs_n >= 1e9:
                                return f"{n/1e9:.2f}B"
                            elif abs_n >= 1e6:
                                return f"{n/1e6:.2f}M"
                            elif abs_n >= 1e3:
                                return f"{n/1e3:.2f}K"
                            else:
                                return f"{n:.2f}"
                        except:
                            return n

                    # Apply simplification map to the dataframe
                    simple_df = bs.applymap(simplify_number)
                    
                    # Re-apply styling only if row exists
                    if "Debt to Equity Ratio" in simple_df.index:
                        st.dataframe(simple_df.style.apply(highlight_de_row, axis=1))
                    else:
                        st.dataframe(simple_df)
                except Exception as e:
                    st.dataframe(bs) # Fallback

    # --- PAGE 2: DCF Model ---
    elif page == "DCF Model":
        st.markdown(f'<div class="fun-header">🔮 DCF Model: {ticker_symbol}</div>', unsafe_allow_html=True)
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
                bs = stock.balance_sheet
                if not bs.empty:
                    if 'Total Debt' in bs.index:
                        total_debt = float(bs.loc['Total Debt'].iloc[0])
                    elif 'Long Term Debt' in bs.index:
                        total_debt = float(bs.loc['Long Term Debt'].iloc[0])
                    st.session_state.dcf_debt = total_debt
            except: pass
            
        if st.session_state.dcf_cash == 0.0:
            try:
                bs = stock.balance_sheet
                if not bs.empty:
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
        st.markdown('<div class="fun-header">🌍 Geopolitical Command Center</div>', unsafe_allow_html=True)

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
            st.subheader("📈 60-Day Macro Trajectory")

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
                st.write("**Valoura Strategic Insight**")
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
    elif page == "Company Profile & Roadmap":
        st.markdown(f"<div class='fun-header'>🏢 Profile: {info.get('longName', ticker_symbol)}</div>", unsafe_allow_html=True)
        
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
        st.subheader("🚀 Strategic Roadmap & News")
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

    # --- PAGE 6: Valoura Analysis ---
    elif page == "Valoura Analysis":
        # ── Top-of-page: 4 fundamental metrics vs industry median ─────────────
        # (Replaces the deleted "Comparing to Industry" section from old Valuation Analysis)
        HEADER_METRICS = {
            "FCF Margin Adjusted": ("fcf_margin_adj",     "%",  "FCF margin after SBC adjustment"),
            "Gross Margin":        ("gross_margin",       "%",  "Gross profit / revenue"),
            "Revenue Growth YoY":  ("revenue_growth_yoy", "%",  "LTM vs prior LTM"),
            "Operating Leverage":  ("operating_leverage", "pp", "Op income growth − revenue growth"),
        }

        _conn_top = get_db_connection()
        _ticker_in_db = False
        if _conn_top is not None:
            _ticker_in_db = _conn_top.execute(
                "SELECT 1 FROM computed_metrics WHERE ticker=?", (ticker_symbol,)
            ).fetchone() is not None

        if not _ticker_in_db:
            st.info(
                f"ℹ️ **{ticker_symbol}** is not in the Valoura database yet. "
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
                with _cols_ui[_idx]:
                    if _cur is None:
                        st.metric(_label, "—", help=_tip)
                    else:
                        _value_str = f"{_cur:.1f}%" if _unit == "%" else f"{_cur:.1f}pp"
                        if _med is not None:
                            _delta = _cur - _med
                            _delta_str = f"{_delta:+.1f}{_unit} vs median ({_med:.1f}{_unit})"
                            # delta_color="normal" → green if positive, red if negative.
                            # All 4 metrics are "higher is better" so default is correct.
                            st.metric(_label, _value_str, delta=_delta_str, help=_tip)
                        else:
                            st.metric(_label, _value_str, help=_tip)

            st.markdown("---")

        st.markdown(f"<div class='fun-header'>🎯 Valoura Analysis: {ticker_symbol}</div>", unsafe_allow_html=True)

        # ── Constants ────────────────────────────────────────────────────────
        BM_CATEGORIES = ["hyperscale", "saas", "semi_hardware", "consumer_internet", "deep_tech"]
        CAT_DISPLAY = {
            "hyperscale":        "Hyperscale",
            "saas":              "SaaS",
            "semi_hardware":     "Semi / HW",
            "consumer_internet": "Consumer Internet",
            "deep_tech":         "Deep Tech",
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
        # Addition 2: formula source per metric key
        METRIC_SOURCES = {
            "peg_ratio":         "Formula: P/E ÷ EPS growth rate (trailing 2yr CAGR)",
            "capex_adj_ev_ebit": "Formula: (EV + CapEx) ÷ EBIT — income statement + cash flow statement",
            "ev_ebitda":         "Formula: Enterprise Value ÷ EBITDA — income statement + cash flow",
            "ev_fcf":            "Formula: Enterprise Value ÷ (OCF − CapEx − SBC) from cash flow statement",
            "fcf_yield":         "Formula: FCF ÷ Market cap × 100 — cash flow statement",
            "pe_ratio":          "Formula: Share price ÷ EPS — income statement",
            "ev_ntm_arr":        "Formula: EV ÷ (Current ARR × NRR) — ARR from SEC 8-K earnings release",
            "cycle_adj_pe":      "Formula: Price ÷ 3yr average mid-cycle EPS — income statement (normalised)",
            "ev_gross_profit":   "Formula: Enterprise Value ÷ Gross Profit — income statement",
            "ps_ratio":          "Formula: Market cap ÷ trailing 12M revenue — income statement",
            "rule_of_40":        "Formula: Revenue growth % + FCF margin % — income statement + cash flow",
            "arr":               "Source: ARR from SEC 8-K earnings release (management disclosed)",
        }
        METRIC_EXPLANATIONS = {
            "peg_ratio":         "A growth-adjusted P/E — below 1.0x suggests undervaluation relative to earnings growth; above 2.0x indicates the market is pricing in significant acceleration.",
            "capex_adj_ev_ebit": "Normalises EV/EBIT for CapEx intensity: hyperscalers investing heavily in infrastructure look cheap on raw EV/EBIT, so adding CapEx back enables like-for-like comparison.",
            "ev_ebitda":         "How many years of EBITDA to 'buy' the company — useful for mature businesses, but ignores CapEx intensity; compare within the same BM category only.",
            "ev_fcf":            "A purer cash multiple — FCF is after CapEx and SBC, reflecting true shareholder cash generation. Preferred over EV/EBITDA for CapEx-heavy businesses.",
            "fcf_yield":         "The inverse of EV/FCF as a percentage — higher yield means more cash generated per dollar of enterprise value. Stage 4 benchmark is 2–4%.",
            "pe_ratio":          "Classic earnings multiple — sensitive to accounting choices and one-time items. Less reliable for companies with high non-cash charges or elevated SBC.",
            "ev_ntm_arr":        "Forward recurring revenue multiple — the standard SaaS benchmark pre-profitability. NTM ARR is estimated as current ARR × net revenue retention.",
            "cycle_adj_pe":      "Normalises P/E across the semi cycle by averaging EPS over three years — avoids overpaying at a trough or appearing cheap at a cycle peak.",
            "ev_gross_profit":   "Used for hardware/deep tech with volatile EBIT — gross profit is a more stable anchor than operating income when R&D spend swings materially.",
            "ps_ratio":          "Price-to-Sales — meaningful only for pre-profit companies. Compresses quickly on the path to profitability; compare only within the same BM category.",
            "rule_of_40":        "Combined growth + FCF margin score. Above 40 is acceptable, above 60 is elite for mature SaaS. Below 20 at Stage 3+ is a quality warning.",
            "arr":               "Annual Recurring Revenue — the contractual forward revenue base. ARR growth rate and net revenue retention are the key quality signals, not the level.",
        }
        # Addition 3: all metrics with fair ranges per cell
        # Each tuple: (display_name, val_data_key, low, high, unit, kind)
        # kind: "multiple" (higher=expensive), "yield" (higher=cheaper), "score" (higher=better)
        FAIR_RANGES_FULL = {
            "hyperscale-4": [
                ("FCF Yield",    "fcf_yield",          1.5, 3.0, "%", "yield"),
                ("PEG",          "peg_ratio",          0.5, 1.5, "x", "multiple"),
                ("Rule of 40",   "rule_of_40",         30,  50,  "%", "score"),
            ],
            "hyperscale-3": [
                ("CapEx EV/EBIT","capex_adj_ev_ebit",  20,  35,  "x", "multiple"),
                ("PEG",          "peg_ratio",          0.5, 1.5, "x", "multiple"),
                ("Rule of 40",   "rule_of_40",         25,  45,  "%", "score"),
            ],
            "hyperscale-2": [
                ("CapEx EV/EBIT","capex_adj_ev_ebit",  40,  80,  "x", "multiple"),
                ("Rule of 40",   "rule_of_40",         15,  35,  "%", "score"),
            ],
            "hyperscale-1": [
                ("EV/NTM Rev",   "ev_ntm_arr",         6,   15,  "x", "multiple"),
                ("Rule of 40",   "rule_of_40",         0,   20,  "%", "score"),
            ],
            "saas-4": [
                ("FCF Yield",    "fcf_yield",          1.0, 2.5, "%", "yield"),
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
                ("FCF Yield",    "fcf_yield",          2.0, 4.0, "%", "yield"),
                ("PEG",          "peg_ratio",          0.5, 1.2, "x", "multiple"),
            ],
            "semi_hardware-3": [
                ("Cycle P/E",    "cycle_adj_pe",       20,  35,  "x", "multiple"),
                ("PEG",          "peg_ratio",          0.5, 1.5, "x", "multiple"),
                ("Rule of 40",   "rule_of_40",         25,  50,  "%", "score"),
            ],
            "semi_hardware-2": [
                ("EV/NTM Rev",   "ev_ntm_arr",         1.5, 4.0, "x", "multiple"),
            ],
            "semi_hardware-1": [
                ("EV/NTM Rev",   "ev_ntm_arr",         0.5, 2.0, "x", "multiple"),
            ],
            "consumer_internet-4": [
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
                ("FCF Yield",    "fcf_yield",          1.5, 3.0, "%", "yield"),
            ],
            "deep_tech-3": [
                ("EV/GP",        "ev_gross_profit",    15,  30,  "x", "multiple"),
                ("Rule of 40",   "rule_of_40",         10,  30,  "%", "score"),
            ],
            "deep_tech-2": [
                ("EV/NTM Rev",   "ev_ntm_arr",         5,   12,  "x", "multiple"),
            ],
            "deep_tech-1": [
                ("P/S",          "ps_ratio",           20,  60,  "x", "multiple"),
            ],
        }
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
                "fh_sbc_stage, fh_de_stage, fh_roic_stage, fh_weighted_score, fh_fcf_hard_cap_applied "
                "FROM classifications WHERE ticker=?",
                (ticker_symbol,)
            ).fetchone()

            if cls_row is None:
                st.info(f"ℹ️ **{ticker_symbol}** has not been classified by the Valoura engine yet. "
                        "Run the data pipeline to classify this ticker.")
            else:
                (bm_category, fh_stage, matrix_cell, bm_confidence, bm_method, bm_llm_rationale,
                 bm_decision_trace_str, bm_validators_str,
                 fh_fcf_stage, fh_gm_stage, fh_runway_stage, fh_oplev_stage,
                 fh_sbc_stage, fh_de_stage, fh_roic_stage, fh_weighted_score, fh_fcf_hard_cap) = cls_row

                stage_label = STAGE_LABELS.get(fh_stage, f"Stage {fh_stage}")

                # ── Row 1: Classification badges (unchanged) ──────────────────
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Business Model", bm_category.replace("_", " ").title())
                c2.metric("FH Stage", f"Stage {fh_stage}")
                c3.metric("Matrix Cell", matrix_cell)
                c4.metric("Confidence", bm_confidence or "—")

                st.caption(f"**{stage_label}**")

                # ── Addition 1: Matrix position visualiser ────────────────────
                grid_rows = []
                header_ths = (
                    '<th style="background:#0a0f1a;color:#475569;padding:7px 6px;'
                    'font-size:0.68em;border:1px solid #1e293b;min-width:60px;"></th>'
                )
                for cat in BM_CATEGORIES:
                    header_ths += (
                        f'<th style="background:#0a0f1a;color:#64748b;padding:7px 4px;'
                        f'font-size:0.7em;font-weight:600;border:1px solid #1e293b;'
                        f'text-align:center;min-width:108px;">{CAT_DISPLAY[cat]}</th>'
                    )
                grid_rows.append(f'<tr>{header_ths}</tr>')

                for s in [1, 2, 3, 4]:
                    stage_th = (
                        f'<td style="background:#0a0f1a;color:#475569;font-size:0.68em;'
                        f'font-weight:600;padding:7px 8px;border:1px solid #1e293b;'
                        f'white-space:nowrap;">Stage {s}</td>'
                    )
                    tds = stage_th
                    for cat in BM_CATEGORIES:
                        is_current = (cat == bm_category and s == fh_stage)
                        same_stage = (s == fh_stage and not is_current)
                        same_cat   = (cat == bm_category and not is_current)
                        label_line = (
                            f"{CAT_DISPLAY[cat]}<br>"
                            f"<span style='font-size:0.82em;opacity:0.6;'>S{s}</span>"
                        )
                        if is_current:
                            td_style  = ("background:#1b3a5c;border:2px solid #3b82f6;"
                                         "color:#93c5fd;font-weight:700;")
                            content   = (
                                f"{CAT_DISPLAY[cat]}<br>"
                                f"<span style='font-size:0.8em;opacity:0.75;'>S{s}</span><br>"
                                f"<span style='font-size:0.88em;color:#60a5fa;'>▶ {ticker_symbol}</span>"
                            )
                        elif same_stage:
                            td_style  = "background:#111827;border:1px solid #1e3a5f;color:#3d5278;"
                            content   = label_line
                        elif same_cat:
                            td_style  = "background:#0f1f18;border:1px solid #1a3828;color:#2e4d3a;"
                            content   = label_line
                        else:
                            td_style  = "background:#090d14;border:1px solid #131920;color:#1e2d3d;"
                            content   = label_line
                        tds += (
                            f'<td style="{td_style}padding:9px 4px;text-align:center;'
                            f'font-size:0.74em;width:20%;">{content}</td>'
                        )
                    grid_rows.append(f'<tr>{tds}</tr>')

                st.markdown(
                    '<div style="overflow-x:auto;margin:12px 0 4px 0;">'
                    '<table style="border-collapse:collapse;width:100%;table-layout:fixed;">'
                    + "".join(grid_rows)
                    + '</table></div>',
                    unsafe_allow_html=True,
                )
                st.caption("🔵 Current cell  ·  dim blue = same stage  ·  dim green = same category  ·  dark = other")

                # ── Addition 4: Classification method expander ────────────────
                with st.expander("🔎 How was this classified?"):
                    st.markdown("**Business Model**")
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
                        st.dataframe(pd.DataFrame(val_rows_display), use_container_width=True, hide_index=True)

                    st.markdown("---")
                    st.markdown("**Financial Health Stage Breakdown**")

                    fh_sub_scores = {
                        "FCF Margin (adj)": fh_fcf_stage,
                        "Gross Margin":     fh_gm_stage,
                        "Cash Runway":      fh_runway_stage,
                        "Op. Leverage":     fh_oplev_stage,
                        "SBC % Rev":        fh_sbc_stage,
                        "D/E Ratio":        fh_de_stage,
                        "ROIC":             fh_roic_stage,
                    }
                    weights_ui = FH_STAGE_WEIGHTS_UI.get(fh_stage, FH_STAGE_WEIGHTS_UI[3])
                    fh_table_rows = []
                    for m_name, sub_score in fh_sub_scores.items():
                        w = weights_ui.get(m_name, 0)
                        contrib = round(w * sub_score, 3) if w > 0 else None
                        fh_table_rows.append({
                            "Metric":       m_name,
                            "Sub-score":    sub_score,
                            "Weight":       f"{w:.0%}" if w > 0 else "—",
                            "Contribution": f"{contrib:.3f}" if contrib is not None else "—",
                        })
                    fh_table_rows.append({
                        "Metric":       "Weighted total",
                        "Sub-score":    "—",
                        "Weight":       "—",
                        "Contribution": f"{fh_weighted_score:.3f} → Stage {fh_stage}",
                    })
                    st.dataframe(pd.DataFrame(fh_table_rows), use_container_width=True, hide_index=True)

                    if fh_fcf_hard_cap:
                        st.warning("⚠️ FCF hard-cap applied: negative FCF margin capped the stage at Stage 2 regardless of other sub-scores.")
                    else:
                        st.success("✅ FCF hard-cap not applied.")

                    if bm_method == "llm_tiebreaker":
                        st.info("🤖 LLM was invoked to break a validator tie.")
                    elif bm_method == "override":
                        st.info("📌 LLM not used — manual override took precedence.")
                    else:
                        st.success("✅ LLM not needed — rule-based classification was unambiguous.")

                st.markdown("---")

                # ── Row 2: Valuation metrics ──────────────────────────────────
                val_row = conn_vb.execute(
                    "SELECT primary_method, valuation_json FROM valuations WHERE ticker=?",
                    (ticker_symbol,)
                ).fetchone()

                if val_row:
                    primary_method, val_json_str = val_row
                    val_data = json.loads(val_json_str) if val_json_str else {}

                    st.subheader(f"📊 Valuation — {primary_method or 'Context-specific'}")

                    # Only show non-None, non-RPO entries (layout unchanged)
                    display_items = [
                        (k, v) for k, v in val_data.items()
                        if v is not None and k in METRIC_LABELS
                    ]

                    if display_items:
                        metric_cols = st.columns(min(len(display_items), 4))
                        for idx, (key, value) in enumerate(display_items):
                            label, suffix = METRIC_LABELS[key]
                            col = metric_cols[idx % len(metric_cols)]
                            try:
                                if suffix == "%":
                                    col.metric(label, f"{float(value):.1f}%")
                                elif suffix == "$B":
                                    col.metric(label, f"${float(value)/1e9:.1f}B")
                                else:
                                    col.metric(label, f"{float(value):.2f}x")
                            except (TypeError, ValueError):
                                col.metric(label, str(value))
                            # Addition 2: source formula + plain-English explanation
                            src  = METRIC_SOURCES.get(key, "")
                            expl = METRIC_EXPLANATIONS.get(key, "See CLAUDE.md for metric definitions.")
                            if src:
                                col.caption(src)
                            col.caption(expl)

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

                    # Addition 3: Full fair-range table (replaces single caption line)
                    fair_rows = FAIR_RANGES_FULL.get(matrix_cell, [])
                    if fair_rows:
                        st.markdown("**📐 Fair-range reference**")
                        th_s = ("padding:6px 10px;text-align:left;color:#94a3b8;"
                                "font-size:0.78em;border-bottom:1px solid #334155;")
                        td_s = ("padding:5px 10px;font-size:0.8em;"
                                "border-bottom:1px solid #1e293b;color:#cbd5e1;")
                        rows_html = []
                        for (disp_name, val_key, fr_low, fr_high, fr_unit, kind) in fair_rows:
                            cur_raw = val_data.get(val_key)
                            if cur_raw is None:
                                cur_str      = "—"
                                verdict_html = f'<span style="color:#475569;">N/A</span>'
                            else:
                                try:
                                    cur_f   = float(cur_raw)
                                    cur_str = f"{cur_f:.1f}{fr_unit}"
                                    if kind == "yield":
                                        if fr_low <= cur_f <= fr_high:
                                            verdict_html = '<span style="color:#22c55e;">✅ Fair</span>'
                                        elif cur_f > fr_high:
                                            verdict_html = '<span style="color:#86efac;">💰 Cheap</span>'
                                        else:
                                            verdict_html = '<span style="color:#ef4444;">🔴 Rich</span>'
                                    elif kind == "score":
                                        if fr_low <= cur_f <= fr_high:
                                            verdict_html = '<span style="color:#22c55e;">✅ Good</span>'
                                        elif cur_f > fr_high:
                                            verdict_html = '<span style="color:#86efac;">💚 Strong</span>'
                                        else:
                                            verdict_html = '<span style="color:#f59e0b;">⚠️ Weak</span>'
                                    else:  # multiple
                                        if fr_low <= cur_f <= fr_high:
                                            verdict_html = '<span style="color:#22c55e;">✅ Fair</span>'
                                        elif cur_f < fr_low:
                                            verdict_html = '<span style="color:#86efac;">💰 Cheap</span>'
                                        else:
                                            verdict_html = '<span style="color:#ef4444;">🔴 Rich</span>'
                                except (TypeError, ValueError):
                                    cur_str      = str(cur_raw)
                                    verdict_html = '<span style="color:#475569;">—</span>'
                            rows_html.append(
                                f'<tr>'
                                f'<td style="{td_s}">{disp_name}</td>'
                                f'<td style="{td_s}text-align:center;">{fr_low}–{fr_high}{fr_unit}</td>'
                                f'<td style="{td_s}text-align:center;">{cur_str}</td>'
                                f'<td style="{td_s}text-align:center;">{verdict_html}</td>'
                                f'</tr>'
                            )
                        st.markdown(
                            f'<table style="border-collapse:collapse;width:100%;">'
                            f'<thead><tr>'
                            f'<th style="{th_s}">Metric</th>'
                            f'<th style="{th_s}text-align:center;">Fair Range</th>'
                            f'<th style="{th_s}text-align:center;">Current</th>'
                            f'<th style="{th_s}text-align:center;">Verdict</th>'
                            f'</tr></thead><tbody>'
                            + "".join(rows_html)
                            + '</tbody></table>',
                            unsafe_allow_html=True,
                        )

                    st.markdown("---")

                # ── Row 3: Matrix-cell peers (unchanged) ──────────────────────
                st.subheader("🔍 Matrix Cell Peers")
                peer_rows = conn_vb.execute(
                    "SELECT ticker FROM classifications WHERE matrix_cell=? AND ticker!=? ORDER BY ticker",
                    (matrix_cell, ticker_symbol)
                ).fetchall()
                peers = [r[0] for r in peer_rows]

                if peers:
                    st.write(f"Other tickers classified as **{matrix_cell}**: `{'` · `'.join(peers)}`")
                else:
                    st.write("No other classified tickers in this matrix cell yet.")

                st.markdown("---")

                # ── Row 4: Classification rationale (unchanged) ───────────────
                st.subheader("🧠 Classification Rationale")
                if bm_llm_rationale:
                    st.write(bm_llm_rationale)
                else:
                    st.write("Classification derived from quantitative signals (no LLM rationale recorded).")

# --- Helper Function: Mock AI Analysis ---
def generate_ai_verdict(info, news, history, ticker=None):
    verdict = []
    sentiment_score = 0 # Range roughly -3 to +3

    # --- Addition 2: Prepend Valoura classification context ---
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
                                    f"📊 **Valoura Valuation:** {primary_method} — "
                                    f"{fr_metric} = {metric_value:.1f}% ({verdict_label}) | Fair range: {fr_low}–{fr_high}%"
                                )
                            else:
                                verdict_label = "✅ in range" if fr_low <= metric_value <= fr_high else ("🔥 rich" if metric_value > fr_high else "💰 cheap")
                                valoura_line2 = (
                                    f"📊 **Valoura Valuation:** {primary_method} — "
                                    f"{fr_metric} = {metric_value:.2f}x ({verdict_label}) | Fair range: {fr_low}–{fr_high}x"
                                )
                        elif primary_method:
                            valoura_line2 = f"📊 **Valoura Valuation method:** {primary_method}"

                    bm_display = bm_category.replace("_", " ").title()
                    verdict.insert(0, f"🎯 **Valoura Classification:** {bm_display} — {stage_label} | Matrix cell: **{matrix_cell}**")
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
    if 'splash_complete' not in st.session_state:
        st.session_state.splash_complete = False

    if not st.session_state.splash_complete:
        splash_screen()
    else:
        main_dashboard()
