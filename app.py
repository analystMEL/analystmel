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
    try:
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
            
            try:
                pub_date = datetime.strptime(pub_date_str, '%a, %d %b %Y %H:%M:%S %Z')
                timestamp = pub_date.timestamp()
            except:
                timestamp = time.time()
            
            items.append({
                'title': title,
                'link': link,
                'publisher': source,
                'providerPublishTime': timestamp,
                'type': 'RSS'
            })
        return items
    except Exception as e:
        return []

# --- HELPER: COMPETITOR MAPPING ---
def get_competitors(ticker, info):
    industry = info.get('industry', 'Unknown Industry')
    sector = info.get('sector', 'Unknown Sector')
    
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
    
    comps = competitor_map.get(sector, [])
    if not comps:
        comps = ['SPY', 'QQQ', 'DIA', 'IWM', 'VTI']
        
    if ticker in comps:
        comps.remove(ticker)
        if sector == 'Technology': comps.append('ADBE')
        elif sector == 'Financial Services': comps.append('MS')
        else: comps.append('VOO')
        
    return industry, comps[:5]

# --- HELPER: FETCH COMPARISON DATA ---
def fetch_comparison_data(main_ticker, competitors):
    tickers = [main_ticker] + competitors
    data = []
    
    for t in tickers:
        try:
            stock = yf.Ticker(t)
            info = stock.info
            hist = stock.history(period="5y")
            
            pe = info.get('trailingPE')
            peg = info.get('pegRatio')
            roe = info.get('returnOnEquity')
            ev_ebitda = info.get('enterpriseToEbitda')
            ps = info.get('priceToSalesTrailing12Months')
            ev_rev = info.get('enterpriseToRevenue')
            rev_growth = info.get('revenueGrowth')
            
            roi_1y = None
            roi_5y = None
            
            if not hist.empty:
                curr = hist['Close'].iloc[-1]
                if len(hist) > 252:
                    price_1y = hist['Close'].iloc[-252]
                    roi_1y = (curr - price_1y) / price_1y
                if len(hist) > 1250:
                    price_5y = hist['Close'].iloc[0]
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
    st.markdown(f"<div style='color: white; font-weight: bold; margin-bottom: 5px; font-size: 1.1em;'>{label}</div>", unsafe_allow_html=True)
    if help_text:
        st.caption(help_text)
    
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
        enterprise_value = dcf_value + pv_terminal
        equity_value = enterprise_value - debt_input + cash_input
        
        if not shares: shares = 1
        return equity_value / shares
    except:
        return 0.0

# --- HELPER: ROBUST VALUATION FETCHER ---
def get_valuation_data(stock, info):
    data = {
        'pe': None,
        'pe_source': None,
        'eps': None,
        'eps_source': None,
        'peg': None,
        'peg_source': None,
        'yahoo_pe': info.get('trailingPE'),
        'yahoo_eps': info.get('trailingEps')
    }
    
    current_price = info.get('currentPrice')
    
    try:
        inc = stock.income_stmt
        eps_row = None
        if not inc.empty:
            if "Diluted EPS" in inc.index:
                eps_row = inc.loc["Diluted EPS"]
            elif "Basic EPS" in inc.index:
                eps_row = inc.loc["Basic EPS"]
        
        if eps_row is not None and not eps_row.empty:
            latest_annual_eps = float(eps_row.iloc[0])
            data['eps'] = latest_annual_eps
            data['eps_source'] = "Company Filings (Annual)"
            
            if current_price:
                data['pe'] = current_price / latest_annual_eps
                data['pe_source'] = "Calculated from Filings"
                
            valid_growths = []
            for i in range(min(5, len(eps_row) - 1)):
                try:
                    current_val = float(eps_row.iloc[i])
                    prev_val = float(eps_row.iloc[i+1])
                    if not np.isnan(current_val) and not np.isnan(prev_val) and prev_val != 0:
                        valid_growths.append((current_val / prev_val) - 1)
                except: pass
            
            if valid_growths:
                avg_growth = sum(valid_growths) / len(valid_growths)
                if avg_growth != 0 and data['pe']:
                    data['peg'] = data['pe'] / (avg_growth * 100)
                    data['peg_source'] = f"Calculated ({len(valid_growths)}yr Avg Growth)"

    except: pass

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

# --- HELPER: SMART DIAGNOSTIC ENGINE ---
def get_smart_diagnostic(stock, info):
    """
    Advanced diagnostic engine for Valuora.
    Categorizes tickers by Lifecycle, Velocity, and Solvency.
    """
    diag = {
        "category": "Standard",
        "is_burner": False,
        "is_distressed": False,
        "is_hyper_growth": False,
        "runway_months": None,
        "monthly_burn": 0
    }
    
    try:
        cf = stock.cashflow
        bs = stock.balance_sheet
        eps = info.get('trailingEps', 0)
        rev_growth = info.get('revenueGrowth', 0)
        debt_to_equity = info.get('debtToEquity', 0)
        current_ratio = info.get('currentRatio', 0)
        
        latest_fcf = cf.loc['Free Cash Flow'].iloc[0] if not cf.empty and 'Free Cash Flow' in cf.index else 0
        cash_on_hand = bs.loc['Cash And Cash Equivalents'].iloc[0] if not bs.empty and 'Cash And Cash Equivalents' in bs.index else 0
        
        if latest_fcf < 0:
            diag["is_burner"] = True
            diag["monthly_burn"] = abs(latest_fcf) / 12
            diag["runway_months"] = cash_on_hand / diag["monthly_burn"] if diag["monthly_burn"] > 0 else 999
            diag["category"] = "Hyper-Growth Burner" if rev_growth > 0.20 else "Early Stage / Speculative"
        elif eps > 0:
            diag["category"] = "Cash Cow / Mature" if rev_growth < 0.10 else "Profitable Compounder"
        
        if rev_growth and rev_growth > 0.25:
            diag["is_hyper_growth"] = True
            
        if current_ratio < 1.0 or (debt_to_equity and debt_to_equity > 200):
            diag["is_distressed"] = True
            
    except Exception as e:
        diag["category"] = "Unknown (Data Missing)"
        
    return diag

# --- MAIN DASHBOARD LOGIC ---
def main_dashboard():
    # --- CUSTOM CSS ---
    st.markdown("""
    <style>
        html, body, [class*="css"] { font-family: 'Georgia', 'Times New Roman', serif; }
        .stApp { background: linear-gradient(135deg, #0a1128 0%, #1c2541 50%, #3a506b 100%); color: #ffffff; }
        [data-testid="stSidebar"] { background-color: #f0f2f5; border-right: 2px solid #1c2541; }
        [data-testid="stSidebar"] .st-emotion-cache-6qob1r, [data-testid="stSidebar"] .st-emotion-cache-17l69k, [data-testid="stSidebar"] [data-testid="stWidgetLabel"] p { color: #000000 !important; font-weight: 700 !important; }
        [data-testid="stSidebar"] div[role="radiogroup"] label p { color: #000000 !important; font-size: 1.1em !important; }
        [data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3 { color: #000000 !important; }
        [data-testid="stMetricLabel"], [data-testid="stMetricValue"], .fun-header { color: #FFFFFF !important; }
        .stTextInput input { color: #FFFFFF !important; background-color: #1c2541 !important; -webkit-text-fill-color: #FFFFFF !important; }
        .stMarkdown p, .stMarkdown div { color: #FFFFFF !important; }
        button[data-baseweb="tab"] p { color: #cbd5e1 !important; }
        button[aria-selected="true"] p { color: #FFFFFF !important; }
        div[data-testid="stMetric"] { background: rgba(255, 255, 255, 0.05); backdrop-filter: blur(10px); border: 1px solid rgba(255, 255, 255, 0.1); padding: 15px; border-radius: 5px; box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1); transition: transform 0.2s; }
        div[data-testid="stMetric"]:hover { transform: translateY(-2px); border-color: #6fffe9; }
        .fun-header { font-size: 3em; font-weight: 800; text-shadow: 2px 2px 4px rgba(0,0,0,0.5); font-family: 'Georgia', serif; margin-bottom: 20px; border-bottom: 2px solid #3a506b; padding-bottom: 10px; }
        h1, h2, h3, h4, h5, h6 { color: #ffffff !important; font-family: 'Georgia', serif; font-weight: 600; }
        .main p, .main span, .main div { color: #e0e6ed; }
        .stSlider label, .stNumberInput label { color: #ffffff !important; }
        .stSlider [data-testid="stMarkdownContainer"] p, .stNumberInput [data-testid="stMarkdownContainer"] p { color: #ffffff !important; }
        .stTabs [data-baseweb="tab-list"] { gap: 8px; }
        .stTabs [data-baseweb="tab"] { height: 50px; background-color: rgba(11, 19, 43, 0.6); border: 1px solid #3a506b; border-radius: 4px; color: #cbd5e1; transition: all 0.3s; font-family: 'Helvetica Neue', sans-serif; }
        .stTabs [aria-selected="true"] { background: #3a506b !important; color: white !important; border: 1px solid #6fffe9; box-shadow: 0 0 10px rgba(111, 255, 233, 0.3); }
        .timeline-item { border-left: 3px solid #6fffe9; padding-left: 20px; margin-bottom: 25px; position: relative; }
        .timeline-dot { width: 12px; height: 12px; background-color: #6fffe9; border: 2px solid #0b132b; border-radius: 50%; position: absolute; left: -7px; top: 5px; box-shadow: 0 0 5px #6fffe9; }
        .timeline-date { font-size: 0.9em; color: #5bc0be; margin-bottom: 5px; font-weight: bold; font-family: 'Courier New', monospace; }
        .timeline-content { background: rgba(255, 255, 255, 0.03); padding: 15px; border-radius: 4px; border: 1px solid rgba(255, 255, 255, 0.05); }
    </style>
    
    <audio id="click-sound" src="data:audio/wav;base64,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"></audio>
    <script>
        var clickSound = document.getElementById("click-sound");
        function addSoundListeners() {
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
        setInterval(addSoundListeners, 1000);
    </script>
    """, unsafe_allow_html=True)

    if 'dcf_fcf' not in st.session_state: st.session_state.dcf_fcf = 0.0
    if 'dcf_growth' not in st.session_state: st.session_state.dcf_growth = 10.0
    if 'dcf_terminal' not in st.session_state: st.session_state.dcf_terminal = 2.5
    if 'dcf_wacc' not in st.session_state: st.session_state.dcf_wacc = 9.0
    if 'dcf_debt' not in st.session_state: st.session_state.dcf_debt = 0.0
    if 'dcf_cash' not in st.session_state: st.session_state.dcf_cash = 0.0

    with st.sidebar:
        st.markdown("# 🌊 Valuora")
        st.markdown("# 🧭 **Navigation**")
        page = st.radio("Select Mode:", ["Financial Analysis", "DCF Model", "Valuation Analysis", "Company Profile & Roadmap"])
        st.markdown("---")
        st.markdown("### ⚙️ **Configuration**")
        ticker_symbol = st.text_input("Stock Ticker", value="AAPL", help="Try: AAPL, MSFT, NVDA, GOOGL").upper()
        st.markdown("---")
        st.markdown("### ⭐ **Watchlist**")
        if 'watchlist' not in st.session_state:
            st.session_state.watchlist = ["AAPL", "TSLA", "NVDA"]
        watchlist_options = st.multiselect("Your Favorites:", options=list(set(st.session_state.watchlist + [ticker_symbol])), default=st.session_state.watchlist)
        st.session_state.watchlist = watchlist_options

    if 'last_ticker' not in st.session_state: st.session_state.last_ticker = ticker_symbol
    if st.session_state.last_ticker != ticker_symbol:
        st.session_state.dcf_fcf = 0.0
        st.session_state.dcf_debt = 0.0
        st.session_state.dcf_cash = 0.0
        st.session_state.dcf_growth = 10.0
        st.session_state.dcf_terminal = 2.5
        st.session_state.dcf_wacc = 9.0
        st.session_state.last_ticker = ticker_symbol
        for k in ['widget_dcf_fcf', 'widget_dcf_growth', 'widget_dcf_terminal', 'widget_dcf_wacc', 'widget_dcf_debt', 'widget_dcf_cash']:
            if k in st.session_state: del st.session_state[k]

    if ticker_symbol:
        try:
            clean_ticker = str(ticker_symbol).strip().upper()
            stock = yf.Ticker(clean_ticker)
            if stock.history(period="1d").empty:
                st.error(f"No data found for '{clean_ticker}'. Check the symbol on Yahoo Finance.")
                return
            info = stock.info
        except Exception as e:
             st.error(f"Could not fetch data for {ticker_symbol}. Check internet or ticker.")
             return
    else: return

    if page == "Financial Analysis":
        st.markdown(f'<div class="fun-header">Valuora: {ticker_symbol}</div>', unsafe_allow_html=True)
        st.markdown(f"**{info.get('longName', ticker_symbol)}** | Made by Om")
        with st.spinner("🤖 Reading the charts..."):
            hist = stock.history(period="max")
            chart_hist = hist.tail(504)
            news = stock.news
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Current Price", f"${info.get('currentPrice', 'N/A')}")
        m2.metric("Market Cap", f"${info.get('marketCap', 0)/1e9:.2f}B" if info.get('marketCap') else "N/A")
        m3.metric("Beta (Vol)", f"{info.get('beta', 'N/A')}")
        m4.metric("52W High", f"${info.get('fiftyTwoWeekHigh', 'N/A')}")
        st.markdown("---")
        tabs = st.tabs(["🧠 AI Verdict", "📊 Live Charts", "📑 The Books"])
        with tabs[0]:
            st.subheader("🤖 Valuora Verdict")
            verdict_points, score = generate_ai_verdict(info, news, hist)
            normalized_score = min(max((score + 3) / 6, 0.0), 1.0)
            if score >= 1: st.progress(normalized_score, text="Sentiment: BULLISH 🐂"); st.success("Strong positive signals!")
            elif score <= -1: st.progress(normalized_score, text="Sentiment: BEARISH 🐻"); st.error("Detected risks and negative trends.")
            else: st.progress(normalized_score, text="Sentiment: NEUTRAL 🦆"); st.warning("Mixed bag. Proceed with caution.")
            with st.expander("See Analysis Details", expanded=True):
                for point in verdict_points: st.markdown(point)
        with tabs[1]:
            st.subheader("📊 Price Action")
            if not chart_hist.empty:
                fig = go.Figure()
                fig.add_trace(go.Candlestick(x=chart_hist.index, open=chart_hist['Open'], high=chart_hist['High'], low=chart_hist['Low'], close=chart_hist['Close'], name='Price'))
                chart_hist['MA50'] = chart_hist['Close'].rolling(window=50).mean()
                fig.add_trace(go.Scatter(x=chart_hist.index, y=chart_hist['MA50'], line=dict(color='#60a5fa', width=2), name='50 MA'))
                fig.update_layout(template="plotly_dark", plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)', height=500, xaxis_rangeslider_visible=False)
                st.plotly_chart(fig, use_container_width=True)
            if not hist.empty:
                st.markdown("##### 📈 Historical Returns")
                current_price = hist['Close'].iloc[-1]
                def get_return(days_back):
                    try:
                        if len(hist) > days_back:
                            past = hist['Close'].iloc[-days_back-1]
                            return (current_price - past) / past * 100
                        return None
                    except: return None
                try:
                    ytd_start = hist[hist.index.year == hist.index[-1].year].iloc[0]['Open']
                    ytd_ret = (current_price - ytd_start) / ytd_start * 100
                except: ytd_ret = 0.0
                returns_data = [("1 Week", get_return(5)), ("1 Month", get_return(21)), ("1 Year", get_return(252)), ("YTD", ytd_ret), ("5 Years", get_return(1260)), ("All Time", (current_price - hist['Close'].iloc[0]) / hist['Close'].iloc[0] * 100)]
                cols = st.columns(6)
                for i, (label, val) in enumerate(returns_data):
                    with cols[i]:
                        if val is not None:
                            color = "#4ade80" if val >= 0 else "#f87171"
                            st.markdown(f"<div style='text-align: center;'><span style='color: white; font-weight: bold;'>{label}</span><br><span style='color: {color}; font-weight: bold;'>{'▲' if val >= 0 else '▼'} {abs(val):.2f}%</span></div>", unsafe_allow_html=True)
                        else: st.markdown(f"<div style='text-align: center;'><span style='color: white;'>{label}</span><br><span style='color: #94a3b8;'>N/A</span></div>", unsafe_allow_html=True)
        with tabs[2]:
            fin_tabs = st.tabs(["Detailed View", "Simplified View"])
            bs = stock.balance_sheet.copy()
            try:
                t_debt = bs.loc['Total Debt'] if 'Total Debt' in bs.index else (bs.loc['Long Term Debt'] + bs.loc['Current Debt'] if 'Long Term Debt' in bs.index and 'Current Debt' in bs.index else None)
                t_equity = bs.loc['Total Equity Gross Minority Interest'] if 'Total Equity Gross Minority Interest' in bs.index else (bs.loc['Stockholders Equity'] if 'Stockholders Equity' in bs.index else None)
                if t_debt is not None and t_equity is not None:
                    de_row = pd.DataFrame(t_debt / t_equity.replace(0, np.nan)).T
                    de_row.index = ["Debt to Equity Ratio"]
                    bs = pd.concat([de_row, bs])
            except: pass
            def highlight_de(s): return ['background-color: #facc15; color: black; font-weight: bold' if s.name == "Debt to Equity Ratio" else '' for _ in s]
            with fin_tabs[0]:
                try:
                    fmt = {"Debt to Equity Ratio": "{:.2f}"} if "Debt to Equity Ratio" in bs.index else {}
                    st.dataframe(bs.style.format(fmt).apply(highlight_de, axis=1))
                except: st.dataframe(bs)
            with fin_tabs[1]:
                def simplify(n):
                    try:
                        abs_n = abs(n)
                        if abs_n < 1000: return f"{n:.2f}"
                        if abs_n >= 1e9: return f"{n/1e9:.2f}B"
                        if abs_n >= 1e6: return f"{n/1e6:.2f}M"
                        return f"{n/1e3:.2f}K"
                    except: return n
                simple_df = bs.applymap(simplify)
                st.dataframe(simple_df.style.apply(highlight_de, axis=1))

    elif page == "DCF Model":
        st.markdown(f'<div class="fun-header">🔮 DCF Model: {ticker_symbol}</div>', unsafe_allow_html=True)
        if st.session_state.dcf_fcf == 0.0:
            try:
                cf_stmt = stock.cashflow
                if not cf_stmt.empty:
                    st.session_state.dcf_fcf = float(cf_stmt.loc['Free Cash Flow'].iloc[0] if 'Free Cash Flow' in cf_stmt.index else (cf_stmt.loc['Total Cash From Operating Activities'].iloc[0] + cf_stmt.loc['Capital Expenditures'].iloc[0] if 'Total Cash From Operating Activities' in cf_stmt.index and 'Capital Expenditures' in cf_stmt.index else 0.0))
            except: pass
        if st.session_state.dcf_debt == 0.0:
            try:
                bs_stmt = stock.balance_sheet
                if not bs_stmt.empty:
                    st.session_state.dcf_debt = float(bs_stmt.loc['Total Debt'].iloc[0] if 'Total Debt' in bs_stmt.index else (bs_stmt.loc['Long Term Debt'].iloc[0] if 'Long Term Debt' in bs_stmt.index else 0.0))
            except: pass
        if st.session_state.dcf_cash == 0.0:
            try:
                bs_stmt = stock.balance_sheet
                if not bs_stmt.empty:
                    st.session_state.dcf_cash = float(bs_stmt.loc['Cash And Cash Equivalents'].iloc[0] if 'Cash And Cash Equivalents' in bs_stmt.index else 0.0)
            except: pass
        def update_state(key, widget_key): st.session_state[key] = st.session_state[widget_key]
        c1, c2, c3 = st.columns(3)
        with c1: fcf_in = st.number_input("Latest FCF ($)", value=st.session_state.dcf_fcf, key="widget_dcf_fcf", on_change=update_state, args=('dcf_fcf', 'widget_dcf_fcf'))
        with c2: growth_in = st.slider("Growth % (5 Yr)", 0.0, 30.0, st.session_state.dcf_growth, key="widget_dcf_growth", on_change=update_state, args=('dcf_growth', 'widget_dcf_growth'))
        with c3: term_in = st.slider("Terminal %", 1.0, 5.0, st.session_state.dcf_terminal, key="widget_dcf_terminal", on_change=update_state, args=('dcf_terminal', 'widget_dcf_terminal'))
        wacc_in = st.slider("WACC %", 5.0, 15.0, st.session_state.dcf_wacc, key="widget_dcf_wacc", on_change=update_state, args=('dcf_wacc', 'widget_dcf_wacc'))
        c_d1, c_d2 = st.columns(2)
        with c_d1: d_in = st.number_input("Total Debt ($)", value=st.session_state.dcf_debt, key="widget_dcf_debt", on_change=update_state, args=('dcf_debt', 'widget_dcf_debt'))
        with c_d2: c_in = st.number_input("Cash ($)", value=st.session_state.dcf_cash, key="widget_dcf_cash", on_change=update_state, args=('dcf_cash', 'widget_dcf_cash'))
        if fcf_in > 0:
            iv = calculate_dcf_value(fcf_in, growth_in/100, term_in/100, wacc_in/100, d_in, c_in, info.get('sharesOutstanding', 1))
            curr = info.get('currentPrice', 0)
            st.subheader("🏷️ Result")
            res1, res2 = st.columns(2)
            with res1:
                color = "#4ade80" if iv > curr else "#f87171"
                st.markdown(f"<h2 style='color: {color};'>${iv:,.2f}</h2>", unsafe_allow_html=True)
                st.write("**UNDERVALUED**" if iv > curr else "**OVERVALUED**")
            with res2:
                st.metric("Market Price", f"${curr:,.2f}")
                if curr > 0: st.metric("Upside", f"{((iv-curr)/curr)*100:.2f}%")
        else: st.warning("Requires positive cash flow.")

    elif page == "Valuation Analysis":
        st.markdown(f'<div class="fun-header">⚖️ Smart Valuation: {ticker_symbol}</div>', unsafe_allow_html=True)
        
        val_data = get_valuation_data(stock, info)
        pe_ratio, peg_ratio, eps = val_data['pe'], val_data['peg'], val_data['eps']
        ps_ratio, ev_revenue, rev_growth = info.get('priceToSalesTrailing12Months'), info.get('enterpriseToRevenue'), info.get('revenueGrowth')
        div_yield, ev_ebitda = info.get('dividendYield'), info.get('enterpriseToEbitda')

        def render_growth_segment():
            st.markdown("### 🚀 Growth & Sales (Startup Focus)")
            c1, c2, c3 = st.columns(3)
            with c1: display_custom_metric("Price-to-Sales (P/S)", f"{ps_ratio:.2f}" if ps_ratio else "N/A")
            with c2: display_custom_metric("EV / Revenue", f"{ev_revenue:.2f}" if ev_revenue else "N/A")
            with c3: display_custom_metric("Revenue Growth", f"{rev_growth*100:.2f}%" if rev_growth else "N/A")
            st.markdown("---")

        def render_profit_segment():
            st.markdown("### 🏛️ Profitability & Value (Established Focus)")
            c1, c2 = st.columns(2)
            with c1:
                if pe_ratio: 
                    display_custom_metric("Price-to-Earnings (P/E)", f"{pe_ratio:.2f}")
                    st.caption(f"Source: {val_data['pe_source']}")
                else: st.warning("Negative Earnings")
                if ev_ebitda: display_custom_metric("EV / EBITDA", f"{ev_ebitda:.2f}")
                if div_yield: display_custom_metric("Dividend Yield", f"{div_yield*100:.2f}%")
                if eps: st.markdown(f"**EPS ($):** {eps:.2f} ({val_data['eps_source']})")
                dcf_fcf = st.session_state.get('dcf_fcf', 0.0)
                if dcf_fcf > 0:
                    iv = calculate_dcf_value(dcf_fcf, st.session_state.get('dcf_growth', 10.0)/100, st.session_state.get('dcf_terminal', 2.5)/100, st.session_state.get('dcf_wacc', 9.0)/100, st.session_state.get('dcf_debt', 0.0), st.session_state.get('dcf_cash', 0.0), info.get('sharesOutstanding', 1))
                    display_custom_metric("Intrinsic Value (DCF)", f"${iv:.2f}", color="green" if iv > info.get('currentPrice', 0) else "red")
            with c2:
                if peg_ratio is not None:
                    display_custom_metric("PEG Ratio", f"{peg_ratio:.2f}", help_text=f"Source: {val_data['peg_source']}")
                    if peg_ratio >= 0:
                        colors = ["#4ade80", "#facc15", "#fb923c", "#f87171"]
                        labels = ["Undervalued", "Fairly Valued", "High", "Overvalued"]
                        idx = min(int(peg_ratio), 3)
                        st.markdown(f"#### Status: <span style='color: {colors[idx]};'>{labels[idx]}</span>", unsafe_allow_html=True)
                        marker = (min(peg_ratio, 4.0) / 4.0) * 100
                        st.markdown(f'<div style="width:100%;height:20px;background:linear-gradient(to right, #4ade80, #facc15, #f87171);border-radius:10px;position:relative;"><div style="position:absolute;left:{marker}%;top:-5px;width:30px;height:30px;background:white;border:3px solid #3b82f6;border-radius:50%;transform:translateX(-50%);"></div></div>', unsafe_allow_html=True)
            st.markdown("---")

        # --- SMART DIAGNOSTIC INTERFACE ---
        diag = get_smart_diagnostic(stock, info)
        st.markdown(f"### 🧬 Smart Diagnostic: <span style='color: #fbbf24;'>{diag['category']}</span>", unsafe_allow_html=True)
        
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            if diag["is_burner"]: st.metric("Cash Runway", f"{diag['runway_months']:.1f} Mo", delta_color="inverse" if diag['runway_months'] < 12 else "normal")
            else: st.metric("P/E Ratio", f"{info.get('trailingPE', 'N/A')}")
        with c2:
            if diag["is_hyper_growth"]: st.metric("Revenue Growth", f"{info.get('revenueGrowth', 0)*100:.1f}%", delta="Hyper-Growth")
            else: st.metric("Div. Yield", f"{info.get('dividendYield', 0)*100:.2f}%")
        with c3:
            if diag["is_distressed"]: st.metric("Current Ratio", f"{info.get('currentRatio', 'N/A')}", delta="Liquidity Risk", delta_color="inverse")
            else: st.metric("Debt/Equity", f"{info.get('debtToEquity', 'N/A')}")
        with c4: st.metric("Profit Margin", f"{info.get('profitMargins', 0)*100:.1f}%")

        st.markdown("---")
        if diag["is_burner"]:
            st.info("💡 **Valuora Logic:** Growth phase detected. Prioritizing **Solvency (Runway)** and **Sales Multiples**.")
            render_growth_segment()
        elif diag["is_distressed"]:
            st.warning("⚠️ **Valuora Logic:** Financial risk detected. Prioritizing **Solvency** metrics.")
            render_growth_segment()
        else:
            st.success("✅ **Valuora Logic:** Cash stable. **DCF** and **Earnings Models** are active.")
            render_profit_segment()
        
        st.markdown("---")
        st.subheader("🏢 Comparing to Industry")
        ind, comps = get_competitors(ticker_symbol, info)
        if comps:
            with st.spinner("Comparing..."):
                cdf = fetch_comparison_data(ticker_symbol, comps)
                if not cdf.empty:
                    st.dataframe(cdf.style.format({"P/E": "{:.2f}", "PEG": "{:.2f}", "ROE": "{:.2f}", "1Y ROI": "{:.2%}", "5Y ROI": "{:.2%}"}).apply(lambda x: ['background-color: #facc15; color: black; font-weight: bold;' if x['Ticker'] == ticker_symbol else '' for i in x], axis=1))
                    st.markdown("#### 📊 Industry Averages")
                    cols = st.columns(3)
                    with cols[0]: display_custom_metric("Avg P/E", f"{cdf['P/E'].mean():.2f}")
                    with cols[1]: display_custom_metric("Avg PEG", f"{cdf['PEG'].mean():.2f}")
                    with cols[2]: display_custom_metric("Avg Rev Growth", f"{cdf['Rev Growth'].mean():.2%}")

    elif page == "Company Profile & Roadmap":
        st.markdown(f"<div class='fun-header'>🏢 Profile: {info.get('longName', ticker_symbol)}</div>", unsafe_allow_html=True)
        col1, col2 = st.columns([2, 1])
        with col1:
            st.subheader("Summary")
            st.write(info.get('longBusinessSummary', "N/A"))
            st.markdown(f"**📍 HQ:** {info.get('city', 'N/A')}, {info.get('country', 'N/A')} | **🌐 Web:** [{info.get('website', 'N/A')}]({info.get('website', '#')})")
        with col2:
            if info.get('logo_url'): st.image(info.get('logo_url'), width=150)
            st.metric("Sector", info.get('sector', 'N/A'))
            st.metric("Industry", info.get('industry', 'N/A'))
        st.markdown("---")
        st.subheader("🚀 News Timeline")
        all_news = []
        try:
            for n in stock.news + fetch_google_news_rss(ticker_symbol):
                all_news.append({'title': n.get('title'), 'link': n.get('link'), 'publisher': n.get('publisher', 'News'), 'time': n.get('providerPublishTime', 0)})
        except: pass
        all_news.sort(key=lambda x: x['time'], reverse=True)
        for item in all_news[:10]:
            t = datetime.fromtimestamp(item['time']).strftime('%Y-%m-%d %H:%M')
            st.markdown(f'<div class="timeline-item"><div class="timeline-dot"></div><div class="timeline-date">{t} • {item["publisher"]}</div><div class="timeline-content"><strong>{item["title"]}</strong><br><a href="{item["link"]}" target="_blank" style="color:#60a5fa;">Read More →</a></div></div>', unsafe_allow_html=True)

def generate_ai_verdict(info, news, history):
    v, s = [], 0
    pe = info.get('trailingPE')
    if pe:
        if pe < 15: v.append("🟢 **Value:** P/E suggests cheapness."); s += 1
        elif pe > 50: v.append("🔥 **Expensive:** P/E is very high."); s -= 1
    if not history.empty:
        curr, m50 = history['Close'].iloc[-1], history['Close'].tail(50).mean()
        if curr > m50: v.append("🚀 **Momentum:** Above 50-day average."); s += 1
        else: v.append("📉 **Downtrend:** Below 50-day average."); s -= 1
    v.append("\n**🗞️ News:**")
    pol = ['election', 'tariff', 'congress', 'policy', 'tax', 'lawsuit']
    found = False
    for n in news[:5]:
        if any(w in n.get('title','').lower() for w in pol):
            v.append(f"- ⚠️ **Political:** \"{n['title']}\""); found = True; s -= 0.5
    if not found: v.append("- 🛡️ **Clear Skies:** No political red flags."); s += 0.5
    return v, s

if __name__ == "__main__":
    if 'splash_complete' not in st.session_state: st.session_state.splash_complete = False
    if not st.session_state.splash_complete: splash_screen()
    else: main_dashboard()