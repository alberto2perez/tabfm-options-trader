from abc import ABC, abstractmethod
from datetime import date
import pandas as pd


class DataAdapter(ABC):
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
