import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import datetime
import time
import requests
import xml.etree.ElementTree as ET

# --- Page Configuration ---
st.set_page_config(page_title="Valuora", page_icon="🌊", layout="wide")

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
    Returns the industry and a list of 5 competitor tickers based on the sector/industry.
    Fallback to generic market indices if sector is unknown.
    """
    industry = info.get('industry', 'Unknown Industry')
    sector = info.get('sector', 'Unknown Sector')
    
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
        # Generic mixed bag if unknown
        comps = ['SPY', 'QQQ', 'DIA', 'IWM', 'VTI']
    
    # Ensure the main ticker isn't in the competitor list (replace if found)
    if ticker in comps:
        comps.remove(ticker)
        # Add a filler if we removed one
        if sector == 'Technology': comps.append('ADBE')
        elif sector == 'Financial Services': comps.append('MS')
        else: comps.append('VOO') # Generic ETF filler
        
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

# --- MAIN DASHBOARD LOGIC (Original Code Wrapped) ---
def main_dashboard():
    # --- CUSTOM CSS: Ocean Blue Theme & Fun Graphics ---
    # --- SURGICAL CSS FOR SIDEBAR NAVIGATION ---
    st.markdown("""
    <style>
        /* --- Formal Font Stack --- */
        html, body, [class*="css"] {
            font-family: 'Georgia', 'Times New Roman', serif;
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
            font-family: 'Georgia', serif;
            margin-bottom: 20px;
            border-bottom: 2px solid #3a506b;
            padding-bottom: 10px;
        }
        
        /* --- Headers --- */
        h1, h2, h3, h4, h5, h6 {
            color: #ffffff !important;
            font-family: 'Georgia', serif;
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
    </style>
    
    <!-- Sound Effect Script -->
    <audio id="click-sound" src="data:audio/wav;base64,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"></audio>
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

    # --- Sidebar Navigation ---
    with st.sidebar:
        st.markdown("# 🌊 Valuora")
    st.sidebar.markdown("# 🧭 **Navigation**")
    page = st.sidebar.radio("Select Mode:", ["Financial Analysis", "DCF Model", "Valuation Analysis", "Company Profile & Roadmap"])
    
    st.sidebar.markdown("---")
    st.sidebar.markdown("### ⚙️ **Configuration**")
    ticker_symbol = st.sidebar.text_input("Stock Ticker", value="AAPL", help="Try: AAPL, MSFT, NVDA, GOOGL").upper()

    st.sidebar.markdown("---")
    st.sidebar.markdown("### ⭐ **Watchlist**")
    
    # Initialize watchlist in session state
    if 'watchlist' not in st.session_state:
        st.session_state.watchlist = ["AAPL", "TSLA", "NVDA"]

    # Multiselect for managing the watchlist
    watchlist_options = st.sidebar.multiselect(
        "Your Favorites:", 
        options=list(set(st.session_state.watchlist + [ticker_symbol])), 
        default=st.session_state.watchlist
    )
    st.session_state.watchlist = watchlist_options
    
    if page == "Financial Analysis":
        pass # Removed DCF Settings from here
    
    st.sidebar.info("💡 **Tip:** Try TSLA, NVDA, or MSFT for interesting results!")

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
    if ticker_symbol:
        try:
            # 1. Clean the ticker input
            clean_ticker = str(ticker_symbol).strip().upper()
            if not clean_ticker:
                st.error("Ticker symbol is empty. Please enter a valid symbol.")
                return

            stock = yf.Ticker(clean_ticker)
            
            # Check if we actually got data (history is a good test)
            hist_check = stock.history(period="1d")
            if hist_check.empty:
                st.error(f"No data found for '{clean_ticker}'. Check the symbol on Yahoo Finance.")
                return

            info = stock.info
            # Basic validation
            if 'symbol' not in info and 'currentPrice' not in info:
                st.error("Invalid Ticker Data. Please try again.")
                return
        except Exception as e:
             print(f"DEBUG: yfinance error for {ticker_symbol}: {e}")
             st.error(f"Could not fetch data for {ticker_symbol}. Check internet or ticker.")
             return
    else:
        st.info("Please enter a ticker symbol.")
        return

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
            verdict_points, score = generate_ai_verdict(info, news, hist)
            
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
    
    # --- PAGE 3: Valuation Analysis ---
    elif page == "Valuation Analysis":
        st.markdown(f'<div class="fun-header">⚖️ Smart Valuation: {ticker_symbol}</div>', unsafe_allow_html=True)
        # st.subheader("Key Valuation Ratios") # Removed subheader to fit new design

        val_data = get_valuation_data(stock, info)
        
        # Extended Metrics
        pe_ratio = val_data['pe']
        peg_ratio = val_data['peg']
        eps = val_data['eps']
        
        # Growth / Startup Metrics
        ps_ratio = info.get('priceToSalesTrailing12Months')
        ev_revenue = info.get('enterpriseToRevenue')
        rev_growth = info.get('revenueGrowth')
        
        # Established Metrics
        div_yield = info.get('dividendYield')
        ev_ebitda = info.get('enterpriseToEbitda')

        # Define Segments (Before calling them)
        def render_growth_segment():
            st.markdown("### 🚀 Segment 1: Growth & Sales (Startup Focus)")
            st.markdown("Metrics used for companies focusing on market expansion over immediate profit.")
            
            c1, c2, c3 = st.columns(3)
            with c1:
                if ps_ratio:
                    display_custom_metric("Price-to-Sales (P/S)", f"{ps_ratio:.2f}")
                else:
                    display_custom_metric("Price-to-Sales", "N/A")
            with c2:
                if ev_revenue:
                    display_custom_metric("EV / Revenue", f"{ev_revenue:.2f}")
                else:
                    display_custom_metric("EV / Revenue", "N/A")
            with c3:
                if rev_growth:
                    display_custom_metric("Revenue Growth (YoY)", f"{rev_growth*100:.2f}%")
                else:
                    display_custom_metric("Revenue Growth", "N/A")
            
            st.markdown("""
            *   **P/S & EV/Sales**: Key for valuing unprofitable companies. Shows how much investors pay for each dollar of sales.
            *   **Revenue Growth**: The lifeblood of a startup. High growth justifies higher P/S multiples.
            """)
            st.markdown("---")

        def render_profit_segment():
            st.markdown("### 🏛️ Segment 2: Profitability & Value (Established Focus)")
            st.markdown("Metrics used for companies with consistent earnings and cash return.")
            
            c1, c2 = st.columns(2)
            
            with c1:
                st.markdown("#### Earnings Valuation")
                if pe_ratio:
                    display_custom_metric("Price-to-Earnings (P/E)", f"{pe_ratio:.2f}")
                    st.caption(f"Source: {val_data['pe_source']}")
                    # Crosscheck
                    if val_data['pe_source'] != "Yahoo Finance" and val_data['yahoo_pe'] and abs(val_data['yahoo_pe'] - pe_ratio) > 0.5:
                         st.info(f"Crosscheck: Yahoo Finance P/E: {val_data['yahoo_pe']:.2f}")
                else:
                    st.warning("P/E Ratio N/A (Negative Earnings)")
                
                if ev_ebitda:
                    display_custom_metric("EV / EBITDA", f"{ev_ebitda:.2f}")
                    with st.expander("ℹ️ Why EV/EBITDA?"):
                         st.markdown("""
                         **Enterprise Value to EBITDA** allows for comparison between companies with different capital structures (debt vs equity) and tax rates. 
                         It focuses on operating profitability before financial and government influence, providing a cleaner view of core business performance.
                         """)
                
                if div_yield:
                    display_custom_metric("Dividend Yield", f"{div_yield*100:.2f}%")
                
                if eps:
                     st.markdown(f"**EPS ($):** {eps:.2f} ({val_data['eps_source']})")

                # --- DCF Snippet ---
                st.markdown("#### 🔮 DCF Snapshot")
                # Try to get DCF inputs from session state or defaults
                # Use defaults if session state is 0.0 (uninitialized for new ticker logic above)
                dcf_fcf = st.session_state.get('dcf_fcf', 0.0)
                if dcf_fcf == 0.0:
                     # Try auto-fetch again if not set (simple version)
                     try:
                        cf_stmt = stock.cashflow
                        if not cf_stmt.empty and 'Free Cash Flow' in cf_stmt.index:
                            dcf_fcf = float(cf_stmt.loc['Free Cash Flow'].iloc[0])
                     except: pass
                
                if dcf_fcf > 0:
                    dcf_growth = st.session_state.get('dcf_growth', 10.0) / 100.0
                    dcf_term = st.session_state.get('dcf_terminal', 2.5) / 100.0
                    dcf_wacc = st.session_state.get('dcf_wacc', 9.0) / 100.0
                    dcf_debt = st.session_state.get('dcf_debt', 0.0)
                    dcf_cash = st.session_state.get('dcf_cash', 0.0)
                    
                    # If debt/cash are 0, try fetching if not done
                    if dcf_debt == 0.0:
                         try: 
                             bs_stmt = stock.balance_sheet
                             if not bs_stmt.empty and 'Total Debt' in bs_stmt.index: dcf_debt = float(bs_stmt.loc['Total Debt'].iloc[0])
                         except: pass
                    
                    shares_out = info.get('sharesOutstanding', 1)
                    
                    intrinsic_val = calculate_dcf_value(dcf_fcf, dcf_growth, dcf_term, dcf_wacc, dcf_debt, dcf_cash, shares_out)
                    
                    curr_p = info.get('currentPrice', 0)
                    delta_color = "green" if intrinsic_val > curr_p else "red"
                    
                    display_custom_metric("Intrinsic Value (DCF)", f"${intrinsic_val:.2f}", color=delta_color)
                    st.caption(f"Based on {dcf_growth*100:.0f}% growth (5yr) & {dcf_wacc*100:.0f}% WACC.")
                else:
                    st.info("DCF Model requires positive Free Cash Flow.")

            with c2:
                st.markdown("#### PEG Analysis")
                if peg_ratio is not None:
                    # Determine if PEG is estimated based on the source text
                    is_estimated_peg = "Calculated" in (val_data['peg_source'] or "")
                    
                    if is_estimated_peg:
                        display_custom_metric("PEG Ratio (Est.)", f"{peg_ratio:.2f}", help_text="Estimated using avg annual EPS growth.")
                        st.markdown("**Formula:** `P/E / (Avg EPS Growth * 100)`")
                    else:
                        display_custom_metric("PEG Ratio", f"{peg_ratio:.2f}", help_text=f"Source: {val_data['peg_source']}")
                    
                    # Negative PEG Check
                    if peg_ratio < 0:
                        st.warning("⚠️ Warning: Negative PEG detected due to shrinking earnings. Standard valuation models may not apply. Switch to Price-to-Sales (P/S) for a clearer picture of market sentiment.")
                    else:
                        # Determine Label and Explanation
                        if peg_ratio < 1:
                            status_label = "Undervalued (< 1.0)"
                            status_color = "#4ade80"
                            explanation = "The stock may be undervalued relative to its growth potential. Investors might be underestimating future earnings."
                        elif 1 <= peg_ratio < 2:
                            status_label = "Fairly Valued (1.0 - 2.0)"
                            status_color = "#facc15"
                            explanation = "The stock price is roughly in line with its expected growth rates. It's priced efficiently."
                        elif 2 <= peg_ratio < 3:
                            status_label = "Quite High (2.0 - 3.0)"
                            status_color = "#fb923c"
                            explanation = "The stock is trading at a premium. Investors have high expectations for future growth."
                        else:
                            status_label = "Overvalued (> 3.0)"
                            status_color = "#f87171"
                            explanation = "The stock appears expensive relative to its growth. The market may be overly optimistic or a correction could be due."
                        
                        st.markdown(f"#### 🏷️ Status: <span style='color: {status_color};'>{status_label}</span>", unsafe_allow_html=True)
                        st.caption(explanation)

                        # Gradient Bar Logic
                        peg_clamped = min(max(peg_ratio, 0.0), 4.0)
                        marker_pos = (peg_clamped / 4.0) * 100
                        
                        st.markdown(f"""
                        <div style="margin-top: 10px; margin-bottom: 20px;">
                            <div style="
                                width: 100%;
                                height: 20px;
                                background: linear-gradient(to right, #4ade80, #facc15, #f87171);
                                border-radius: 10px;
                                position: relative;
                                box-shadow: inset 0 1px 3px rgba(0,0,0,0.5);
                            ">
                                <div style="
                                    position: absolute;
                                    left: {marker_pos}%;
                                    top: -5px;
                                    width: 30px;
                                    height: 30px;
                                    background-color: white;
                                    border: 3px solid #3b82f6;
                                    border-radius: 50%;
                                    transform: translateX(-50%);
                                    box-shadow: 0 2px 5px rgba(0,0,0,0.5);
                                "></div>
                            </div>
                        </div>
                        """, unsafe_allow_html=True)
                    
                    # --- Typewriter Animation for "Showing Work" ---
                    with st.expander("🧮 See Calculation Details"):
                        calc_text = ""
                        if pe_ratio and eps:
                            calc_text += f"1. EPS (Latest Annual): ${eps:.2f}\n"
                            calc_text += f"2. P/E Ratio: {pe_ratio:.2f}\n"
                            try:
                                inc = stock.income_stmt
                                if not inc.empty:
                                    eps_row = inc.loc["Diluted EPS"] if "Diluted EPS" in inc.index else inc.loc["Basic EPS"]
                                    
                                    # Recalculate growths for display (mirroring the logic in get_valuation_data)
                                    valid_growths_disp = []
                                    growth_steps = []
                                    for i in range(min(5, len(eps_row) - 1)):
                                        try:
                                            c_val = float(eps_row.iloc[i])
                                            p_val = float(eps_row.iloc[i+1])
                                            
                                            # Check for NaNs
                                            if np.isnan(c_val) or np.isnan(p_val):
                                                growth_steps.append(f"Growth (Y{i} vs Y{i+1}): N/A (Skipped)")
                                                continue

                                            if p_val != 0:
                                                g = (c_val / p_val) - 1
                                                valid_growths_disp.append(g)
                                                growth_steps.append(f"Growth (Y{i} vs Y{i+1}): {g*100:.2f}%")
                                            else:
                                                growth_steps.append(f"Growth (Y{i} vs Y{i+1}): Undefined (Skipped)")
                                        except: 
                                            growth_steps.append(f"Growth (Y{i} vs Y{i+1}): Error (Skipped)")
                                    
                                    if valid_growths_disp:
                                        for step in growth_steps:
                                            calc_text += f"3. {step}\n"
                                        avg_g = sum(valid_growths_disp) / len(valid_growths_disp)
                                        calc_text += f"4. Avg Growth Rate: {avg_g*100:.2f}%\n"
                                        calc_text += f"5. PEG = {pe_ratio:.2f} / {(avg_g*100):.2f} = {pe_ratio/(avg_g*100):.2f}"
                                    else:
                                        calc_text += "Insufficient history for growth calc."
                            except:
                                calc_text += "Detailed growth data not available."
                        else:
                            calc_text = "Insufficient data to show calculation."

                        typewriter_placeholder = st.empty()
                        displayed_text = ""
                        for char in calc_text:
                            displayed_text += char
                            typewriter_placeholder.markdown(f"```text\n{displayed_text}\n```")
                            time.sleep(0.005) 
                else:
                     st.info("PEG Ratio data not available (requires positive earnings/growth).")
            st.markdown("---")

        # --- PHASE 1: DIAGNOSTIC ---
        status, runway, monthly_burn = classify_cash_position(stock)
        is_unprofitable = (status == "Cash Burning")
        
        if status == "Cash Burning":
            st.warning(f"🧪 **Diagnostic: Cash Burning (Growth Phase)**")
            st.write(f"This company is currently spending more than it earns to scale operations. Traditional P/E models are not applicable.")
            
            # Show Runway Card
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Monthly Burn", f"-${monthly_burn/1e6:.1f}M")
            with col2:
                # Color code runway: Green > 24m, Yellow > 12m, Red < 12m
                r_color = "normal" if runway > 18 else "inverse"
                st.metric("Cash Runway", f"{runway:.1f} Months", delta_color=r_color)
            with col3:
                st.metric("Classification", "High Growth")

            st.markdown("---")
            # Trigger Segment 1 Analysis (Sales & Growth)
            render_growth_segment() # Your existing P/S and EV/Rev code
            
        else:
            st.success(f"🏛️ **Diagnostic: Cash Stable (Mature Phase)**")
            st.write(f"This company is generating positive cash flow. We will use Earnings-based and Intrinsic Value models.")
            
            col1, col2 = st.columns(2)
            with col1:
                st.metric("Annual FCF", f"Positive ✅")
            with col2:
                st.metric("Classification", "Value / Established")

            st.markdown("---")
            # Trigger Segment 2 Analysis (DCF & P/E)
            render_profit_segment() # Your existing DCF and P/E code
        
        # --- Comparison Segment ---
        st.markdown("---")
        st.subheader("🏢 Comparing to Industry")
        
        # Get Competitors
        industry_name, competitor_list = get_competitors(ticker_symbol, info)
        st.markdown(f"**Industry:** {industry_name}")
        
        if competitor_list:
            with st.spinner(f"Comparing with {', '.join(competitor_list)}..."):
                comp_df = fetch_comparison_data(ticker_symbol, competitor_list)
                
                if not comp_df.empty:
                    # Styling DataFrame
                    # Format columns
                    # 1Y ROI, 5Y ROI -> Percentage
                    # Highlight main ticker row? Streamlit dataframe styling is limited but we can try basic formatting
                    
                    st.dataframe(
                        comp_df.style.format({
                            "P/E": "{:.2f}",
                            "PEG": "{:.2f}",
                            "ROE": "{:.2f}",
                            "1Y ROI": "{:.2%}",
                            "5Y ROI": "{:.2%}"
                        }).apply(lambda x: ['background-color: #facc15; color: black; font-weight: bold;' if x['Ticker'] == ticker_symbol else '' for i in x], axis=1)
                    )
                    
                    if is_unprofitable:
                         st.markdown("#### 🚀 Startup/Growth Comparison Table")
                         growth_cols = ['Ticker', 'P/S', 'EV/Revenue', 'Rev Growth']
                         growth_df = comp_df[growth_cols].copy()
                         st.dataframe(
                            growth_df.style.format({
                                "P/S": "{:.2f}",
                                "EV/Revenue": "{:.2f}",
                                "Rev Growth": "{:.2%}"
                            }).apply(lambda x: ['background-color: #facc15; color: black; font-weight: bold;' if x['Ticker'] == ticker_symbol else '' for i in x], axis=1)
                        )
                    else:
                         # --- EV/EBITDA Table ---
                         st.subheader("🏭 Industry EV/EBITDA Comparison")
                         ev_df = comp_df[['Ticker', 'EV/EBITDA']].copy()
                         st.dataframe(
                             ev_df.style.format({"EV/EBITDA": "{:.2f}"})
                             .apply(lambda x: ['background-color: #facc15; color: black; font-weight: bold;' if x['Ticker'] == ticker_symbol else '' for i in x], axis=1)
                         )

                    st.markdown("---")

                    # --- Industry Averages Section ---
                    st.markdown("#### 📊 Industry Averages")
                    
                    if is_unprofitable:
                        avg_ps = comp_df['P/S'].mean()
                        avg_ev_rev = comp_df['EV/Revenue'].mean()
                        avg_rev_g = comp_df['Rev Growth'].mean()
                        
                        c_avg1, c_avg2, c_avg3 = st.columns(3)
                        with c_avg1:
                            display_custom_metric("Avg P/S", f"{avg_ps:.2f}" if not pd.isna(avg_ps) else "N/A")
                        with c_avg2:
                             display_custom_metric("Avg EV/Rev", f"{avg_ev_rev:.2f}" if not pd.isna(avg_ev_rev) else "N/A")
                        with c_avg3:
                             display_custom_metric("Avg Rev Growth", f"{avg_rev_g:.2%}" if not pd.isna(avg_rev_g) else "N/A")
                    else:
                        # Calculate Averages
                        avg_pe = comp_df['P/E'].mean()
                        avg_peg = comp_df['PEG'].mean()
                        avg_roe = comp_df['ROE'].mean()
                        avg_1y = comp_df['1Y ROI'].mean()
                        avg_5y = comp_df['5Y ROI'].mean()
                        
                        # Display Averages
                        c_avg1, c_avg2, c_avg3, c_avg4, c_avg5 = st.columns(5)
                        with c_avg1:
                            display_custom_metric("Avg P/E", f"{avg_pe:.2f}" if not pd.isna(avg_pe) else "N/A")
                        with c_avg2:
                            display_custom_metric("Avg PEG", f"{avg_peg:.2f}" if not pd.isna(avg_peg) else "N/A")
                        with c_avg3:
                            display_custom_metric("Avg ROE", f"{avg_roe:.2f}" if not pd.isna(avg_roe) else "N/A")
                        with c_avg4:
                            display_custom_metric("Avg 1Y ROI", f"{avg_1y:.2%}" if not pd.isna(avg_1y) else "N/A")
                        with c_avg5:
                            display_custom_metric("Avg 5Y ROI", f"{avg_5y:.2%}" if not pd.isna(avg_5y) else "N/A")
                    
                    st.markdown("---")

                    # --- Automated Analysis / Verdict (Refactored UI) ---
                    st.subheader("🖋️ Analyst Verdict & Summary")
                    
                    # Get Main Ticker Values
                    main_row = comp_df[comp_df['Ticker'] == ticker_symbol]
                    if not main_row.empty:
                        
                        if is_unprofitable:
                            main_ps = main_row.iloc[0]['P/S']
                            main_rev_g = main_row.iloc[0]['Rev Growth']
                            avg_ps = comp_df['P/S'].mean()
                            avg_rev_g = comp_df['Rev Growth'].mean()
                            
                            if not pd.isna(main_ps) and not pd.isna(avg_ps):
                                with st.container():
                                    col1, col2 = st.columns(2)
                                    ps_diff = ((main_ps - avg_ps) / avg_ps) * 100
                                    
                                    with col1:
                                        st.metric(
                                            label="Valuation (P/S)",
                                            value=f"{main_ps:.2f}",
                                            delta=f"{ps_diff:.1f}% vs Industry",
                                            delta_color="inverse"
                                        )
                                        st.caption(f"Industry Average: {avg_ps:.2f}")
                                    
                                    with col2:
                                        if not pd.isna(main_rev_g) and not pd.isna(avg_rev_g):
                                            rev_diff = (main_rev_g - avg_rev_g)
                                            st.metric(
                                                label="Growth (Revenue)",
                                                value=f"{main_rev_g:.2%}",
                                                delta=f"{rev_diff*100:.1f}% vs Avg",
                                                delta_color="normal"
                                            )
                                            st.caption(f"Industry Average: {avg_rev_g:.2%}")
                                
                                st.markdown("### 💡 Interpretation")
                                st.info("For growth companies, a lower P/S ratio combined with higher revenue growth suggests a potential opportunity. Compare EV/Revenue to validate.")
                            else:
                                st.info("Insufficient data for growth verdict.")

                        else:
                            main_pe = main_row.iloc[0]['P/E']
                            main_roe = main_row.iloc[0]['ROE']
                            
                            if not pd.isna(main_pe) and not pd.isna(avg_pe) and not pd.isna(main_roe) and not pd.isna(avg_roe):
                                # Create a container for the key metrics
                                with st.container():
                                    col1, col2 = st.columns(2)
                                    
                                    # P/E Metric Card
                                    pe_diff = ((main_pe - avg_pe) / avg_pe) * 100
                                    with col1:
                                        st.metric(
                                            label="Valuation (P/E)", 
                                            value=f"{main_pe:.2f}", 
                                            delta=f"{pe_diff:.1f}% vs Industry",
                                            delta_color="inverse" # Red if higher (expensive), Green if lower (cheap)
                                        )
                                        st.caption(f"Industry Average: {avg_pe:.2f}")

                                    # ROE Metric Card
                                    with col2:
                                        st.metric(
                                            label="Efficiency (ROE)", 
                                            value=f"{main_roe:.2f}", 
                                            delta=f"{(main_roe - avg_roe):.2f} vs Avg",
                                            delta_color="normal" # Green if higher (good)
                                        )
                                        st.caption(f"Industry Average: {avg_roe:.2f}")

                                # Interpretation Section
                                st.markdown("### 💡 Interpretation")
                                
                                pe_status = "undervalued" if main_pe < avg_pe else "overvalued"
                                st.info(
                                    f"**Growth/Value Signal:** A P/E of {main_pe:.2f} suggests the market expects higher future growth "
                                    f"or the stock is currently **{pe_status}** compared to the industry average of {avg_pe:.2f}."
                                )
                                
                                with st.expander("View Management Quality Breakdown"):
                                    if main_roe > avg_roe:
                                        st.write(f"The ROE of {main_roe:.2f} indicates superior management and capital allocation compared to the industry average.")
                                    else:
                                        st.write(f"The ROE of {main_roe:.2f} indicates management efficiency is lagging behind the industry average.")
                            else:
                                st.info("Insufficient data for full automated verdict.")
                    else:
                        st.info("Ticker data not found in comparison.")

                else:
                    st.warning("Could not fetch competitor data.")
        else:
            st.info("No specific competitors mapped for this sector.")

        st.markdown("---")
        st.subheader("📚 Understanding the PEG Ratio")
        st.markdown("""
        The **PEG ratio** (Price/Earnings-to-Growth) enhances the P/E ratio by adding expected earnings growth into the calculation. 
        It is considered a better indicator of a stock's true value than the P/E ratio alone.
        
        *   **< 1.0 (Undervalued):** The stock price is considered low relative to its expected growth. This is often seen as a potential "buy" signal for value investors.
        *   **1.0 - 2.0 (Fairly Valued):** The stock price accurately reflects its expected growth. A PEG of 1.0 is theoretically "perfectly" valued.
        *   **2.0 - 3.0 (Quite High):** The stock is trading at a premium. Investors are paying a high price for future growth, which increases risk.
        *   **> 3.0 (Overvalued):** The stock price is significantly higher than its growth rate would justify. The company needs to significantly exceed expected earnings to justify this valuation, or the price is likely a bubble.
        """)

    # --- PAGE 4: Company Profile ---
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

# --- Helper Function: Mock AI Analysis ---
def generate_ai_verdict(info, news, history):
    verdict = []
    sentiment_score = 0 # Range roughly -3 to +3
    
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
