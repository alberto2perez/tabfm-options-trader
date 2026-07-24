from datetime import date, timedelta
import numpy as np
import pandas as pd
import yfinance as yf
from scipy.stats import norm
from .base import DataAdapter

_RISK_FREE_RATE = 0.045
_DTE_WINDOWS = [7, 14, 21, 30, 45]
# 1% steps — keeps spread width ≤ 1% of spot price per contract (~$7 for SPY at $744)
_STRIKE_RANGE = np.arange(0.85, 1.16, 0.01)


def _rsi(closes: pd.Series, period: int = 14) -> float:
  delta = closes.diff()
  gain = delta.clip(lower=0).ewm(com=period - 1, adjust=False).mean()
  loss = (-delta.clip(upper=0)).ewm(com=period - 1, adjust=False).mean()
  rs = gain.iloc[-1] / loss.iloc[-1] if loss.iloc[-1] != 0 else float("inf")
  return float(100 - 100 / (1 + rs))


def _macd(closes: pd.Series) -> tuple[float, float, float]:
  ema12 = closes.ewm(span=12, adjust=False).mean()
  ema26 = closes.ewm(span=26, adjust=False).mean()
  line = ema12 - ema26
  signal = line.ewm(span=9, adjust=False).mean()
  return float(line.iloc[-1]), float(signal.iloc[-1]), float(line.iloc[-1] - signal.iloc[-1])


def _bs_delta(S: float, K: float, T: float, sigma: float, opt: str) -> float:
  if T <= 0 or sigma <= 0:
    return 0.0
  d1 = (np.log(S / K) + (0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
  return float(norm.cdf(d1) if opt == "call" else norm.cdf(d1) - 1)


def _bs_price(S: float, K: float, T: float, sigma: float, opt: str) -> float:
  if T <= 0:
    return max(0.0, (S - K) if opt == "call" else (K - S))
  d1 = (np.log(S / K) + (0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
  d2 = d1 - sigma * np.sqrt(T)
  if opt == "call":
    return float(S * norm.cdf(d1) - K * np.exp(-_RISK_FREE_RATE * T) * norm.cdf(d2))
  return float(K * np.exp(-_RISK_FREE_RATE * T) * norm.cdf(-d2) - S * norm.cdf(-d1))


class HistAdapter(DataAdapter):
  def __init__(self, as_of: date) -> None:
    self._as_of = as_of
    self._cache: dict = {}

  def _assert_no_lookahead(self, requested: date) -> None:
    assert requested <= self._as_of, (
      f"Lookahead violation: requested {requested} but as_of is {self._as_of}"
    )

  def _history(self, ticker: str, lookback: int = 300) -> pd.DataFrame:
    key = (ticker, lookback)
    if key not in self._cache:
      start = self._as_of - timedelta(days=lookback)
      df = yf.download(
        ticker, start=str(start), end=str(self._as_of), progress=False, auto_adjust=True
      )
      # Newer yfinance returns MultiIndex columns (col, ticker) — flatten to single level
      if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
      self._cache[key] = df
    return self._cache[key]

  def get_underlying(self, ticker: str, as_of: date) -> dict:
    self._assert_no_lookahead(as_of)
    df = self._history(ticker)
    # Filter to dates on or before the requested date so callers asking for
    # a historical settlement price (e.g. expiry date < self._as_of) get the
    # correct price, not the adapter's own most-recent close.
    df = df[df.index <= pd.Timestamp(as_of)]
    if df.empty or len(df) < 50:
      raise ValueError(f"Insufficient history for {ticker}")

    close = float(df["Close"].iloc[-1])
    sma20 = float(df["Close"].rolling(20).mean().iloc[-1])
    sma50 = float(df["Close"].rolling(50).mean().iloc[-1])
    atr14 = float((df["High"] - df["Low"]).rolling(14).mean().iloc[-1])
    hv20 = float(df["Close"].pct_change().dropna().iloc[-20:].std() * np.sqrt(252))
    volume = float(df["Volume"].iloc[-1])
    vol_series = df["Volume"].rolling(20)
    vol_std = float(vol_series.std().iloc[-1])
    vol_mean = float(vol_series.mean().iloc[-1])
    vol_z = (volume - vol_mean) / vol_std if vol_std > 0 else 0.0

    macd_line, macd_signal, macd_hist = _macd(df["Close"])
    return {
      "close": close,
      "sma20": sma20,
      "sma50": sma50,
      "atr14": atr14,
      "hv20": hv20,
      "volume": volume,
      "volume_zscore": float(vol_z),
      "momentum_5d": float(df["Close"].pct_change(5).iloc[-1]),
      "momentum_20d": float(df["Close"].pct_change(20).iloc[-1]),
      "rsi_14": _rsi(df["Close"]),
      "macd_line": macd_line,
      "macd_signal": macd_signal,
      "macd_histogram": macd_hist,
    }

  def get_close(self, ticker: str, as_of: date) -> float:
    """Lightweight price-only lookup — skips indicator computation."""
    self._assert_no_lookahead(as_of)
    df = self._history(ticker)
    df = df[df.index <= pd.Timestamp(as_of)]
    if df.empty:
      raise ValueError(f"No data for {ticker} on {as_of}")
    return float(df["Close"].iloc[-1])

  def get_options_chain(self, ticker: str, as_of: date) -> pd.DataFrame:
    self._assert_no_lookahead(as_of)
    u = self.get_underlying(ticker, as_of)
    S, sigma = u["close"], max(u["hv20"], 0.05)
    rows = []
    for dte in _DTE_WINDOWS:
      expiry = as_of + timedelta(days=dte)
      T = dte / 365.0
      for pct in _STRIKE_RANGE:
        K = round(S * pct, 0)
        for opt in ("call", "put"):
          price = _bs_price(S, K, T, sigma, opt)
          delta = abs(_bs_delta(S, K, T, sigma, opt))
          rows.append({
            "strike": K,
            "expiry": expiry,
            "option_type": opt,
            "bid": round(price * 0.99, 2),
            "ask": round(price * 1.01, 2),
            "mid": round(price, 2),
            "open_interest": 500,
            "delta": round(delta, 4),
            "iv": round(sigma, 4),
            "dte": dte,
          })
    return pd.DataFrame(rows)

  def get_vix(self, as_of: date) -> float:
    self._assert_no_lookahead(as_of)
    df = self._history("^VIX", lookback=30)
    if df.empty:
      return 20.0
    return float(df["Close"].iloc[-1])

  def get_events(self, as_of: date) -> dict | None:
    # No historical earnings calendar in v1: layer A inactive, not degraded.
    return {"earnings": []}

  def get_vix_history(self, as_of: date, n: int = 6) -> list:
    self._assert_no_lookahead(as_of)
    df = self._history("^VIX", lookback=60)
    df = df[df.index <= pd.Timestamp(as_of)]
    tail = df["Close"].tail(n)
    return [[str(idx.date()), float(v)] for idx, v in tail.items()]
