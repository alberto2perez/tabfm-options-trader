from datetime import date

from tabfm.trading.adapters.historical import (
  _strike_grid, _iv_premium, _synthetic_chain,
)
from tabfm.trading.pipeline.feature_engineer import engineer_features
from tabfm.trading.pipeline.trade_recommender import _passes_filters

AS_OF = date(2026, 7, 24)


def test_strike_grid_large_underlying_5_spacing():
  grid = _strike_grid(740.0)
  steps = {round(grid[i + 1] - grid[i], 2) for i in range(len(grid) - 1)}
  assert steps == {5.0}
  assert min(grid) <= 740 * 0.86 and max(grid) >= 740 * 1.14


def test_strike_grid_small_underlying_1_spacing():
  grid = _strike_grid(29.0)
  steps = {round(grid[i + 1] - grid[i], 2) for i in range(len(grid) - 1)}
  assert steps == {1.0}


def test_iv_premium_default_and_override(monkeypatch):
  assert _iv_premium() == 1.25
  monkeypatch.setenv("TABFM_BACKTEST_IV_PREMIUM", "1.40")
  assert _iv_premium() == 1.40


def test_iv_premium_scales_written_iv(monkeypatch):
  low = _synthetic_chain(740.0, 0.15, AS_OF)
  monkeypatch.setenv("TABFM_BACKTEST_IV_PREMIUM", "2.0")
  high = _synthetic_chain(740.0, 0.15, AS_OF)
  assert high["iv"].iloc[0] > low["iv"].iloc[0]


def test_thirty_delta_5wide_spread_clears_credit_floor():
  chain = _synthetic_chain(740.0, 0.15, AS_OF)
  chain_data = {
    "ticker": "SPY", "sector": "index_etf", "chain": chain, "vix": 18.0,
    "underlying": {
      "close": 740.0, "sma20": 735.0, "sma50": 730.0, "atr14": 9.0,
      "hv20": 0.15, "volume": 8e7, "volume_zscore": 0.2,
      "momentum_5d": 0.005, "momentum_20d": 0.02, "rsi_14": 55.0,
      "macd_line": 1.0, "macd_signal": 0.8, "macd_histogram": 0.2,
    },
  }
  rows = engineer_features(chain_data, AS_OF, iv_rank=50.0)
  spreads = [r for r in rows if r["spread_width_dollars"] == 5.0]
  assert spreads, "expected $5-wide spreads on the $5 grid"
  ratios = [r["entry_credit"] / r["spread_width_dollars"] for r in spreads]
  assert max(ratios) >= 0.30, f"no $5 spread clears the 0.30 floor; max={max(ratios):.3f}"
  assert any(_passes_filters(r) for r in spreads), "no $5 spread passes the gauntlet"
