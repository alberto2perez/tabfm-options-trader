import pytest
from datetime import date
from tabfm.trading.adapters.base import DataAdapter
from tabfm.trading.adapters.historical import HistAdapter

AS_OF = date(2025, 1, 10)


def test_hist_adapter_is_data_adapter():
  adapter = HistAdapter(as_of=AS_OF)
  assert isinstance(adapter, DataAdapter)


def test_get_underlying_returns_required_keys():
  adapter = HistAdapter(as_of=AS_OF)
  result = adapter.get_underlying("SPY", AS_OF)
  for key in ("close", "sma20", "sma50", "atr14", "hv20", "volume",
              "volume_zscore", "momentum_5d", "momentum_20d"):
    assert key in result, f"Missing key: {key}"
  assert result["close"] > 0


def test_get_underlying_no_lookahead():
  adapter = HistAdapter(as_of=AS_OF)
  future = date(2025, 6, 1)
  with pytest.raises(AssertionError, match="Lookahead"):
    adapter.get_underlying("SPY", future)


def test_get_options_chain_columns():
  adapter = HistAdapter(as_of=AS_OF)
  df = adapter.get_options_chain("SPY", AS_OF)
  for col in ("strike", "expiry", "option_type", "bid", "ask", "mid",
              "open_interest", "delta", "iv", "dte"):
    assert col in df.columns, f"Missing column: {col}"
  assert len(df) > 0


def test_get_options_chain_has_calls_and_puts():
  adapter = HistAdapter(as_of=AS_OF)
  df = adapter.get_options_chain("SPY", AS_OF)
  assert "call" in df["option_type"].values
  assert "put" in df["option_type"].values


def test_get_vix_returns_positive_float():
  adapter = HistAdapter(as_of=AS_OF)
  vix = adapter.get_vix(AS_OF)
  assert isinstance(vix, float)
  assert vix > 0
