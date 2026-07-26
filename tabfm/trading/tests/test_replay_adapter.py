from datetime import date

import pandas as pd

from tabfm.trading.adapters.historical import _implied_vol, _bs_price
from tabfm.trading.adapters.replay import ReplayAdapter


def test_implied_vol_round_trips():
  S, K, T, opt = 740.0, 720.0, 30 / 365.0, "put"
  price = _bs_price(S, K, T, 0.22, opt)
  iv = _implied_vol(price, S, K, T, opt)
  assert iv is not None
  assert abs(iv - 0.22) < 0.01


def test_implied_vol_none_below_intrinsic():
  # A put worth less than intrinsic (K-S) has no positive-vol solution
  S, K, T, opt = 700.0, 740.0, 30 / 365.0, "put"
  assert _implied_vol(1.0, S, K, T, opt) is None


def _seed_cache(tmp_path):
  rows = [
    # as_of 2026-03-02, expiry 2026-04-17 → 46 DTE (OUT of 28-45)
    {"date": "2026-03-02", "ticker": "SPY", "strike": 720.0, "expiry": "2026-04-17",
     "option_type": "put", "bid": 3.0, "ask": 3.1, "mid": 3.05, "delta": 0.30,
     "iv": 0.22, "open_interest": 500, "dte": 46},
    # as_of 2026-03-20, expiry 2026-04-17 → 28 DTE (IN range)
    {"date": "2026-03-20", "ticker": "SPY", "strike": 700.0, "expiry": "2026-04-17",
     "option_type": "put", "bid": 2.0, "ask": 2.1, "mid": 2.05, "delta": 0.28,
     "iv": 0.24, "open_interest": 500, "dte": 28},
    {"date": "2026-03-20", "ticker": "SPY", "strike": 695.0, "expiry": "2026-04-17",
     "option_type": "put", "bid": 1.5, "ask": 1.6, "mid": 1.55, "delta": 0.22,
     "iv": 0.24, "open_interest": 500, "dte": 28},
  ]
  p = tmp_path / "cache.parquet"
  pd.DataFrame(rows).to_parquet(p, index=False)
  return p


def test_chain_filters_to_date_and_dte_window(tmp_path):
  cache = _seed_cache(tmp_path)
  adapter = ReplayAdapter(cache, as_of=date(2026, 4, 17))
  # 2026-03-20 has a 28-DTE expiry → returned
  chain = adapter.get_options_chain("SPY", date(2026, 3, 20))
  assert len(chain) == 2
  assert set(chain["strike"]) == {700.0, 695.0}
  assert str(chain["expiry"].iloc[0].date()) == "2026-04-17"
  # 2026-03-02's only expiry is 46 DTE → filtered out → empty
  assert adapter.get_options_chain("SPY", date(2026, 3, 2)).empty


def test_no_lookahead(tmp_path):
  cache = _seed_cache(tmp_path)
  adapter = ReplayAdapter(cache, as_of=date(2026, 3, 20))
  import pytest
  with pytest.raises(AssertionError):
    adapter.get_options_chain("SPY", date(2026, 4, 1))  # after as_of
