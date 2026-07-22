"""LiveAdapter wraps the Robinhood API via robin-stocks.

Authentication: call `robin_stocks.robinhood.login(username, password)` once
before using this adapter. Credentials can be stored in a local `.env` file
and loaded with python-dotenv:

  from dotenv import load_dotenv
  import os, robin_stocks.robinhood as rh
  load_dotenv()
  rh.login(os.environ["RH_USER"], os.environ["RH_PASS"])
"""
from datetime import date, datetime
import numpy as np
import pandas as pd
import robin_stocks.robinhood as rh
from .base import DataAdapter


class LiveAdapter(DataAdapter):
  def get_underlying(self, ticker: str, as_of: date) -> dict:
    quote = rh.get_stock_quote_by_symbol(ticker)
    price = float(quote["last_trade_price"])
    historicals = rh.get_stock_historicals(
      ticker, interval="day", span="3month", bounds="regular"
    )
    closes = pd.Series([float(h["close_price"]) for h in historicals])
    volumes = pd.Series([float(h["volume"]) for h in historicals])
    sma20 = float(closes.rolling(20).mean().iloc[-1])
    sma50 = float(closes.rolling(50).mean().iloc[-1]) if len(closes) >= 50 else sma20
    highs = pd.Series([float(h["high_price"]) for h in historicals])
    lows = pd.Series([float(h["low_price"]) for h in historicals])
    atr14 = float((highs - lows).rolling(14).mean().iloc[-1])
    hv20 = float(closes.pct_change().dropna().iloc[-20:].std() * np.sqrt(252))
    vol_mean = float(volumes.rolling(20).mean().iloc[-1])
    vol_std = float(volumes.rolling(20).std().iloc[-1])
    vol_z = (volumes.iloc[-1] - vol_mean) / vol_std if vol_std > 0 else 0.0
    return {
      "close": price,
      "sma20": sma20,
      "sma50": sma50,
      "atr14": atr14,
      "hv20": hv20,
      "volume": float(volumes.iloc[-1]),
      "volume_zscore": float(vol_z),
      "momentum_5d": float(closes.pct_change(5).iloc[-1]),
      "momentum_20d": float(closes.pct_change(20).iloc[-1]),
    }

  def get_options_chain(self, ticker: str, as_of: date) -> pd.DataFrame:
    expirations = rh.get_chains(ticker)["expiration_dates"]
    rows = []
    for exp_str in expirations[:5]:  # up to 5 nearest expiries
      exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
      dte = (exp_date - as_of).days
      if not (5 <= dte <= 50):
        continue
      for opt_type in ("call", "put"):
        options = rh.find_options_by_expiration(ticker, exp_str, optionType=opt_type)
        for o in (options or []):
          try:
            rows.append({
              "strike": float(o["strike_price"]),
              "expiry": exp_date,
              "option_type": opt_type,
              "bid": float(o["bid_price"] or 0),
              "ask": float(o["ask_price"] or 0),
              "mid": (float(o["bid_price"] or 0) + float(o["ask_price"] or 0)) / 2,
              "open_interest": int(o["open_interest"] or 0),
              "delta": abs(float(o["delta"] or 0)),
              "iv": float(o["implied_volatility"] or 0),
              "dte": dte,
            })
          except (TypeError, ValueError):
            continue
    return pd.DataFrame(rows)

  def get_vix(self, as_of: date) -> float:
    quote = rh.get_stock_quote_by_symbol("VIXY")  # VIX proxy ETF available on Robinhood
    return float(quote["last_trade_price"]) * 10  # rough VIX approximation
