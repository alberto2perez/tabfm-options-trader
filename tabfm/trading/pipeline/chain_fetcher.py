from datetime import date
from ..adapters.base import DataAdapter
from ..watchlist import WATCHLIST


def fetch_chains(adapter: DataAdapter, as_of: date) -> list[dict]:
  """Fetch options chain and underlying data for all watchlist tickers.

  Skips any ticker that raises an exception (network error, insufficient history).
  """
  results = []
  vix = adapter.get_vix(as_of)
  for ticker_info in WATCHLIST:
    try:
      underlying = adapter.get_underlying(ticker_info.symbol, as_of)
      chain = adapter.get_options_chain(ticker_info.symbol, as_of)
      results.append({
        "ticker": ticker_info.symbol,
        "sector": ticker_info.sector,
        "underlying": underlying,
        "chain": chain,
        "vix": vix,
      })
    except Exception as e:
      print(f"[ChainFetcher] Skipping {ticker_info.symbol}: {e}")
  return results
