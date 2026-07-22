from datetime import date
from unittest.mock import MagicMock
import pandas as pd
from tabfm.trading.pipeline.chain_fetcher import fetch_chains

AS_OF = date(2025, 1, 10)

def _mock_adapter(fail_tickers=None):
  fail_tickers = fail_tickers or []
  adapter = MagicMock()
  adapter.get_vix.return_value = 18.5
  def get_underlying(ticker, as_of):
    if ticker in fail_tickers:
      raise ValueError("test error")
    return {"close": 100.0, "sma20": 98.0, "sma50": 95.0, "atr14": 2.0,
            "hv20": 0.18, "volume": 1e6, "volume_zscore": 0.3,
            "momentum_5d": 0.01, "momentum_20d": 0.03}
  def get_options_chain(ticker, as_of):
    if ticker in fail_tickers:
      raise ValueError("test error")
    return pd.DataFrame({"strike": [99.0], "expiry": [date(2025, 1, 17)],
                         "option_type": ["put"], "bid": [1.0], "ask": [1.1],
                         "mid": [1.05], "open_interest": [200], "delta": [0.25],
                         "iv": [0.20], "dte": [7]})
  adapter.get_underlying.side_effect = get_underlying
  adapter.get_options_chain.side_effect = get_options_chain
  return adapter


def test_fetch_chains_returns_all_tickers():
  adapter = _mock_adapter()
  results = fetch_chains(adapter, AS_OF)
  assert len(results) == 25


def test_fetch_chains_result_keys():
  adapter = _mock_adapter()
  results = fetch_chains(adapter, AS_OF)
  for r in results:
    assert "ticker" in r
    assert "sector" in r
    assert "underlying" in r
    assert "chain" in r
    assert "vix" in r


def test_fetch_chains_skips_erroring_tickers():
  adapter = _mock_adapter(fail_tickers=["TSLA", "NVDA"])
  results = fetch_chains(adapter, AS_OF)
  tickers = [r["ticker"] for r in results]
  assert "TSLA" not in tickers
  assert "NVDA" not in tickers
  assert len(results) == 23
