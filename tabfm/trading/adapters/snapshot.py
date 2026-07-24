"""SnapshotAdapter reads a pre-fetched market snapshot JSON file.

Used for MCP-driven nightly runs: an agent session with the Robinhood MCP
fetches real quotes, historicals, and option chains, writes them into a
snapshot file, and the pipeline consumes it through this adapter. Keeps the
Python pipeline free of any Robinhood auth.

Snapshot schema:
{
  "as_of": "YYYY-MM-DD",
  "vix": float,
  "tickers": {
    "<SYM>": {
      "sector": str,
      "underlying": {close, sma20, sma50, atr14, hv20, volume, volume_zscore,
                     momentum_5d, momentum_20d, rsi_14, macd_line, macd_signal,
                     macd_histogram},
      "chain": [{strike, expiry, option_type, bid, ask, mid, open_interest,
                 delta, iv, dte}, ...]
    }
  },
  "closes": {"<SYM>": [["YYYY-MM-DD", close], ...]}
}
"""
import json
from datetime import date
from pathlib import Path

import pandas as pd

from .base import DataAdapter


class SnapshotAdapter(DataAdapter):
  def __init__(self, path: str | Path) -> None:
    self._s = json.load(open(path))

  @property
  def symbols(self) -> list[str]:
    return list(self._s["tickers"].keys())

  def get_underlying(self, ticker: str, as_of: date) -> dict:
    return self._s["tickers"][ticker]["underlying"]

  def get_options_chain(self, ticker: str, as_of: date) -> pd.DataFrame:
    df = pd.DataFrame(self._s["tickers"][ticker]["chain"])
    if df.empty:
      return df
    df["expiry"] = pd.to_datetime(df["expiry"])
    # Long-leg selection in feature_engineer relies on ascending strike order
    return df.sort_values(["option_type", "expiry", "strike"]).reset_index(drop=True)

  def get_vix(self, as_of: date) -> float:
    return float(self._s["vix"])

  def get_close(self, ticker: str, as_of: date) -> float:
    closes = self._s["closes"].get(ticker)
    if not closes:
      raise ValueError(f"No closes for {ticker} in snapshot")
    valid = [c for d, c in closes if date.fromisoformat(d) <= as_of]
    if not valid:
      raise ValueError(f"No close for {ticker} on or before {as_of}")
    return float(valid[-1])

  def get_events(self, as_of: date) -> dict | None:
    return self._s.get("events")

  def get_vix_history(self, as_of: date, n: int = 6) -> list:
    hist = self._s.get("vix_history") or []
    valid = [
      [str(h[0]), float(h[1])]
      for h in hist
      if date.fromisoformat(str(h[0])) <= as_of
    ]
    return valid[-n:]
