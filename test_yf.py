import yfinance as yf
import requests

session = requests.Session()
stock = yf.Ticker("AAPL", session=session)
hist = stock.history(period="1d")
print(hist)
