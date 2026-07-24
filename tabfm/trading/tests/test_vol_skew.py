from datetime import date

from tabfm.trading.adapters.historical import _synthetic_chain, _skew_slope
from tabfm.trading.pipeline.feature_engineer import engineer_features
from tabfm.trading.pipeline.trade_recommender import _passes_filters

AS_OF = date(2026, 7, 24)


def test_skew_slope_default_and_override(monkeypatch):
  assert _skew_slope() == 2.5
  monkeypatch.setenv("TABFM_BACKTEST_SKEW", "1.0")
  assert _skew_slope() == 1.0


def test_iv_ordering_put_richer_than_call():
  chain = _synthetic_chain(740.0, 0.15, AS_OF)
  ivs = chain.groupby("strike")["iv"].first()
  lo = ivs.index[ivs.index <= 690][-1]      # OTM put strike
  atm = min(ivs.index, key=lambda k: abs(k - 740))
  hi = ivs.index[ivs.index >= 790][0]       # OTM call strike
  assert ivs[lo] > ivs[atm] > ivs[hi]


def test_skew_enriches_put_premium(monkeypatch):
  monkeypatch.setenv("TABFM_BACKTEST_SKEW", "0")
  flat = _synthetic_chain(740.0, 0.15, AS_OF)
  monkeypatch.setenv("TABFM_BACKTEST_SKEW", "2.5")
  skewed = _synthetic_chain(740.0, 0.15, AS_OF)

  def put_mid(chain, k):
    r = chain[(chain["strike"] == k) & (chain["option_type"] == "put") & (chain["dte"] == 30)]
    return float(r["mid"].iloc[0])

  strike = 700.0  # sub-ATM put
  assert put_mid(skewed, strike) > put_mid(flat, strike)


def test_skewed_chain_still_yields_passing_put_spread():
  chain = _synthetic_chain(740.0, 0.15, AS_OF)
  cd = {"ticker": "SPY", "sector": "index_etf", "chain": chain, "vix": 18.0,
        "underlying": {"close": 740.0, "sma20": 735.0, "sma50": 730.0, "atr14": 9.0,
                       "hv20": 0.15, "volume": 8e7, "volume_zscore": 0.2,
                       "momentum_5d": 0.005, "momentum_20d": 0.02, "rsi_14": 55.0,
                       "macd_line": 1.0, "macd_signal": 0.8, "macd_histogram": 0.2}}
  rows = engineer_features(cd, AS_OF, iv_rank=50.0)
  puts = [r for r in rows if r["direction"] == "put_spread" and r["spread_width_dollars"] == 5.0]
  assert any(_passes_filters(r) for r in puts)
