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
    st.markdown("""
    <style>
        [data-testid="stSidebar"], [data-testid="stHeader"], [data-testid="stToolbar"] { display: none !important; }
        .splash-container {
            position: fixed; top: 0; left: 0; width: 100vw; height: 100vh;
            background: linear-gradient(135deg, #0f172a 0%, #1e3a8a 50%, #00b4db 100%);
            z-index: 999999; display: flex; flex-direction: column; justify-content: center; align-items: center; overflow: hidden;
        }
        .typewriter h1 {
            color: #fff; font-family: 'Courier New', Courier, monospace; overflow: hidden; 
            border-right: .15em solid #fbbf24; white-space: nowrap; margin: 0 auto; letter-spacing: .15em;
            animation: typing 3.5s steps(30, end), blink-caret .75s step-end infinite;
            font-size: 4vw; font-weight: bold; text-shadow: 0 0 15px rgba(0,0,0,0.5); z-index: 1000000;
        }
        @keyframes typing { from { width: 0 } to { width: 100% } }
        @keyframes blink-caret { from, to { border-color: transparent } 50% { border-color: #fbbf24 } }
        .ocean { height: 200px; width: 100%; position: absolute; bottom: 0; left: 0; overflow: hidden; }
        .wave {
            background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 1440 320'%3E%3Cpath fill='%23ffffff' fill-opacity='0.2' d='M0,192L48,197.3C96,203,192,213,288,229.3C384,245,480,267,576,250.7C672,235,768,181,864,160C960,139,1056,149,1152,165.3C1248,181,1344,203,1392,213.3L1440,224L1440,320L1392,320C1344,320,1248,320,1152,320C1056,320,960,320,864,320C768,320,672,320,576,320C480,320,384,320,288,320C192,320,96,320,48,320L0,320Z'%3E%3C/path%3E%3C/svg%3E");
            background-size: 1440px 200px; position: absolute; bottom: 0; width: 200%; height: 100%;
            animation: wave 15s cubic-bezier( 0.36, 0.45, 0.63, 0.53) infinite; transform: translate3d(0, 0, 0);
        }
        @keyframes wave { 0% { transform: translateX(0); } 100% { transform: translateX(-50%); } }
    </style>
    <div class="splash-container">
        <div class="typewriter">
            <h1>Valuora</h1>
            <p style="color: #fbbf24; font-family: 'Courier New', monospace; font-size: 1.5vw; margin-top: 10px; opacity: 0.8;">Made by Om</p>
        </div>
        <div class="ocean"><div class="wave"></div></div>
    </div>
    """, unsafe_allow_html=True)
    time.sleep(4.5)
    st.session_state.splash_complete = True
    st.rerun()

# --- HELPER: GOOGLE NEWS RSS FETCHER ---
def fetch_google_news_rss(ticker):
    try:
        url = f"https://news.google.com/rss/search?q={ticker}+stock&hl=en-US&gl=US&ceid=US:en"
        response = requests.get(url, timeout=5)
        root = ET.fromstring(response.content)
        items = []
        for item in root.findall('.//item'):
            title = item.find('title').text
            link = item.find('link').text
            pub_date_str = item.find('pubDate').text
            source = item.find('source').text
            try:
                timestamp = datetime.strptime(pub_date_str, '%a, %d %b %Y %H:%M:%S %Z').timestamp()
            except: timestamp = time.time()
            items.append({'title': title, 'link': link, 'publisher': source, 'providerPublishTime': timestamp})
        return items
    except: return []

# --- HELPER: SMART DIAGNOSTIC ENGINE ---
def get_smart_diagnostic(stock, info):
    diag = {"category": "Standard", "is_burner": False, "is_distressed": False, "is_hyper_growth": False, "runway_months": None, "monthly_burn": 0, "logic_explanation": ""}
    try:
        cf, bs = stock.cashflow, stock.balance_sheet
        eps, rev_growth = info.get('trailingEps', 0), info.get('revenueGrowth', 0)
        debt_to_equity, current_ratio = info.get('debtToEquity', 0), info.get('currentRatio', 0)
        latest_fcf = cf.loc['Free Cash Flow'].iloc[0] if not cf.empty and 'Free Cash Flow' in cf.index else 0
        cash_on_hand = bs.loc['Cash And Cash Equivalents'].iloc[0] if not bs.empty and 'Cash And Cash Equivalents' in bs.index else 0
        if latest_fcf < 0:
            diag["is_burner"] = True
            diag["monthly_burn"] = abs(latest_fcf) / 12
            diag["runway_months"] = cash_on_hand / diag["monthly_burn"] if diag["monthly_burn"] > 0 else 999
            diag["category"] = "Hyper-Growth Burner" if rev_growth > 0.20 else "Early Stage / Speculative"
            diag["logic_explanation"] = "This company is spending more cash than it earns to scale. Traditional P/E is useless; we focus on 'Runway' to see how long they can survive without more funding."
        elif eps > 0:
            diag["category"] = "Cash Cow / Mature" if rev_growth < 0.10 else "Profitable Compounder"
            diag["logic_explanation"] = "Company is profitable. We prioritize Earnings (P/E) and Intrinsic Value (DCF) to see if you are overpaying for their profit."
        if rev_growth and rev_growth > 0.25: diag["is_hyper_growth"] = True
        if current_ratio < 1.0 or (debt_to_equity and debt_to_equity > 200):
            diag["is_distressed"] = True
            diag["logic_explanation"] = "⚠️ Warning: Liquidity risk detected. They may struggle to pay short-term bills."
    except: diag["category"] = "Data Limited"
    return diag

# --- HELPER: CUSTOM METRIC DISPLAY ---
def display_custom_metric(label, value, prefix="", suffix="", help_text=None, color=None):
    st.markdown(f"<div style='color: white; font-weight: bold; margin-bottom: 5px; font-size: 1.1em;'>{label}</div>", unsafe_allow_html=True)
    if help_text: st.caption(help_text)
    text_color = "white"
    if color == "green": text_color = "#4ade80"
    elif color == "red": text_color = "#f87171"
    st.markdown(f'<div style="background: rgba(255, 255, 255, 0.05); backdrop-filter: blur(10px); border: 1px solid rgba(255, 255, 255, 0.1); padding: 15px; border-radius: 5px; color: {text_color}; font-family: \'Courier New\', monospace; font-size: 1.5em; font-weight: bold;">{prefix}{value}{suffix}</div>', unsafe_allow_html=True)

# --- VALUATION HELPERS ---
def get_valuation_data(stock, info):
    data = {'pe': None, 'pe_source': None, 'eps': None, 'eps_source': None, 'peg': None, 'peg_source': None}
    curr_p = info.get('currentPrice')
    try:
        inc = stock.income_stmt
        eps_row = inc.loc["Diluted EPS"] if "Diluted EPS" in inc.index else inc.loc["Basic EPS"] if "Basic EPS" in inc.index else None
        if eps_row is not None:
            data['eps'] = float(eps_row.iloc[0])
            data['eps_source'] = "Company Filings"
            if curr_p: data['pe'] = curr_p / data['eps']; data['pe_source'] = "Calculated"
            valid_g = []
            for i in range(min(5, len(eps_row) - 1)):
                if eps_row.iloc[i+1] != 0: valid_g.append((eps_row.iloc[i]/eps_row.iloc[i+1])-1)
            if valid_g and data['pe']:
                avg_g = sum(valid_g)/len(valid_g)
                data['peg'] = data['pe']/(avg_g*100); data['peg_source'] = f"Calc ({len(valid_g)}yr Growth)"
    except: pass
    if not data['pe']: data['pe'] = info.get('trailingPE'); data['pe_source'] = "Yahoo Finance"
    if not data['peg']: data['peg'] = info.get('pegRatio'); data['peg_source'] = "Yahoo Finance"
    return data

def main_dashboard():
    # --- CSS ---
    st.markdown("""<style>
        .stApp { background: linear-gradient(135deg, #0a1128 0%, #1c2541 50%, #3a506b 100%); color: white; }
        [data-testid="stSidebar"] { background-color: #f0f2f5; color: black; }
        [data-testid="stSidebar"] * { color: black !important; }
        .fun-header { font-size: 3em; font-weight: 800; border-bottom: 2px solid #3a506b; margin-bottom: 20px; }
        div[data-testid="stExpanderDetails"] { background-color: #f0f2f6 !important; color: #0b132b !important; border-radius: 5px; }
        div[data-testid="stExpanderDetails"] * { color: #0b132b !important; }
    </style>""", unsafe_allow_html=True)

    with st.sidebar:
        st.markdown("# 🌊 Valuora")
        page = st.radio("Navigation", ["Financial Analysis", "DCF Model", "Valuation Analysis", "Profile"])
        ticker = st.text_input("Ticker", value="AAPL").upper()

    stock = yf.Ticker(ticker)
    info = stock.info

    if page == "Financial Analysis":
        st.markdown(f'<div class="fun-header">Valuora: {ticker}</div>', unsafe_allow_html=True)
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Price", f"${info.get('currentPrice', 'N/A')}")
        m2.metric("Market Cap", f"${info.get('marketCap', 0)/1e9:.2f}B")
        m3.metric("Beta", f"{info.get('beta', 'N/A')}")
        m4.metric("52W High", f"${info.get('fiftyTwoWeekHigh', 'N/A')}")

    elif page == "DCF Model":
        st.markdown(f'<div class="fun-header">🔮 DCF Model: {ticker}</div>', unsafe_allow_html=True)
        with st.expander("📚 Beginner's Guide: What is DCF?"):
            st.write("Discounted Cash Flow (DCF) values a company by estimating how much cash it will generate in the future and 'discounting' it back to today's value. If the DCF price is higher than the market price, the stock might be a bargain.")
        
        c1, c2 = st.columns(2)
        fcf = c1.number_input("Latest Free Cash Flow ($)", value=0.0)
        growth = c2.slider("Growth % (Next 5 Years)", 0, 30, 10)
        wacc = st.slider("Discount Rate (WACC) %", 5, 15, 9)
        
        with st.expander("🔎 How to pick a Growth Rate?"):
            st.write("Check the 'Financials' tab. Look at the Revenue Growth over the last 3 years. For mature companies like Apple, 5-10% is standard. For hyper-growth, it can be 20%+. Be conservative!")

    elif page == "Valuation Analysis":
        st.markdown(f'<div class="fun-header">⚖️ Smart Valuation: {ticker}</div>', unsafe_allow_html=True)
        diag = get_smart_diagnostic(stock, info)
        st.info(f"🧬 **Diagnostic:** {diag['category']}\n\n{diag['logic_explanation']}")
        
        v_data = get_valuation_data(stock, info)
        
        if diag['is_burner']:
            st.subheader("🚀 Growth Metrics (The 'Startup' View)")
            c1, c2 = st.columns(2)
            display_custom_metric("Cash Runway", f"{diag['runway_months']:.1f} Months", help_text="Time until they run out of cash at current burn rate.")
            display_custom_metric("Price-to-Sales (P/S)", f"{info.get('priceToSalesTrailing12Months', 'N/A'):.2f}", help_text="Common for companies with no profit. Measures value against revenue.")
        else:
            st.subheader("🏛️ Value Metrics (The 'Profit' View)")
            c1, c2 = st.columns(2)
            with c1:
                display_custom_metric("P/E Ratio", f"{v_data['pe']:.2f}" if v_data['pe'] else "N/A")
                st.caption(f"Source: {v_data['pe_source']}")
            with c2:
                if v_data['peg']:
                    display_custom_metric("PEG Ratio", f"{v_data['peg']:.2f}")
                    with st.expander("🧮 See Calculation Details (Typewriter Mode)"):
                        calc_text = f"1. P/E Ratio: {v_data['pe']:.2f}\n2. Source: {v_data['pe_source']}\n3. Method: P/E divided by Avg Growth Rate.\n4. Verdict: " + ("Undervalued" if v_data['peg'] < 1 else "Fair" if v_data['peg'] < 2 else "Overvalued")
                        placeholder = st.empty()
                        out = ""
                        for char in calc_text:
                            out += char; placeholder.code(out); time.sleep(0.01)

    elif page == "Profile":
        st.subheader(info.get('longName'))
        st.write(info.get('longBusinessSummary'))

if __name__ == "__main__":
    if 'splash_complete' not in st.session_state: st.session_state.splash_complete = False
    if not st.session_state.splash_complete: splash_screen()
    else: main_dashboard()