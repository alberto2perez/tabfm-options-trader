"""ReplayAdapter: serves REAL historical option marks from a cached parquet,
with real SPY/VIX underlying inherited from HistAdapter. Lets the identical
pipeline replay over real prices — trustworthy stats without waiting weeks."""
from datetime import date

import pandas as pd

from .historical import HistAdapter


class ReplayAdapter(HistAdapter):
  def __init__(self, cache_path, as_of: date) -> None:
    super().__init__(as_of=as_of)
    self._chains = pd.read_parquet(cache_path)

  def get_options_chain(self, ticker: str, as_of: date) -> pd.DataFrame:
    self._assert_no_lookahead(as_of)
    df = self._chains[
      (self._chains["ticker"] == ticker) & (self._chains["date"] == str(as_of))
    ].copy()
    if df.empty:
      return df
    df["dte"] = df["expiry"].map(lambda e: (pd.Timestamp(e).date() - as_of).days)
    df = df[(df["dte"] >= 28) & (df["dte"] <= 45)]
    if df.empty:
      return df
    df["expiry"] = pd.to_datetime(df["expiry"])
    return df.reset_index(drop=True)
