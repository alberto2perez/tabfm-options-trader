from abc import ABC, abstractmethod
from datetime import date
import pandas as pd


class DataAdapter(ABC):
  persists_market_history = True

  @abstractmethod
  def get_underlying(self, ticker: str, as_of: date) -> dict:
    """Return dict with keys: close, sma20, sma50, atr14, hv20, volume,
    volume_zscore, momentum_5d, momentum_20d."""
    ...

  @abstractmethod
  def get_options_chain(self, ticker: str, as_of: date) -> pd.DataFrame:
    """Return DataFrame with columns: strike, expiry, option_type, bid, ask,
    mid, open_interest, delta, iv, dte."""
    ...

  @abstractmethod
  def get_vix(self, as_of: date) -> float:
    ...

  def get_close(self, ticker: str, as_of: date) -> float:
    """Return closing price for ticker on as_of. Default delegates to get_underlying."""
    return self.get_underlying(ticker, as_of)["close"]

  def get_events(self, as_of: date) -> dict | None:
    """Upcoming market events ({"earnings": [...]}) or None when unavailable."""
    return None

  def get_vix_history(self, as_of: date, n: int = 6) -> list:
    """Recent [date_str, vix] pairs on/before as_of, oldest first."""
    return []
