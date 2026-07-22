from datetime import date, timedelta
import pandas as pd
from tabfm.trading.pipeline.feature_engineer import (
  engineer_features, _vix_bucket, _trend_direction, _iv_regime,
)

AS_OF = date(2025, 1, 10)


def _make_chain(as_of: date, n_strikes: int = 5) -> pd.DataFrame:
  S = 100.0
  rows = []
  for i in range(n_strikes):
    k = S * (0.85 + i * 0.05)
    strike = round(k, 0)
    for opt in ("call", "put"):
      delta = 0.35 - i * 0.05
      # Realistic strike-dependent pricing so credit spreads can be positive:
      # puts: higher strike = higher premium; calls: lower strike = higher premium
      if opt == "put":
        mid = 1.0 + i * 0.5
      else:
        mid = 3.0 - i * 0.5
      bid = round(mid - 0.10, 2)
      ask = round(mid + 0.10, 2)
      rows.append({
        "strike": strike,
        "expiry": as_of + timedelta(days=14),
        "option_type": opt,
        "bid": bid, "ask": ask, "mid": mid,
        "open_interest": 300, "delta": delta, "iv": 0.22, "dte": 14,
      })
  return pd.DataFrame(rows)


def _make_chain_data(as_of: date) -> dict:
  return {
    "ticker": "SPY",
    "sector": "index_etf",
    "vix": 18.5,
    "chain": _make_chain(as_of),
    "underlying": {
      "close": 100.0, "sma20": 98.0, "sma50": 95.0, "atr14": 1.5,
      "hv20": 0.18, "volume": 5e7, "volume_zscore": 0.4,
      "momentum_5d": 0.01, "momentum_20d": 0.03,
      "rsi_14": 55.0, "macd_line": 0.5, "macd_signal": 0.3, "macd_histogram": 0.2,
    },
  }


def test_vix_bucket():
  assert _vix_bucket(12.0) == "low"
  assert _vix_bucket(20.0) == "normal"
  assert _vix_bucket(30.0) == "elevated"
  assert _vix_bucket(40.0) == "spike"


def test_trend_direction():
  assert _trend_direction(100.0, 98.0, 95.0) == "uptrend"
  assert _trend_direction(90.0, 93.0, 96.0) == "downtrend"
  assert _trend_direction(97.0, 98.0, 95.0) == "sideways"


def test_iv_regime():
  assert _iv_regime(10.0) == "cheap"
  assert _iv_regime(50.0) == "fair"
  assert _iv_regime(85.0) == "expensive"


def test_engineer_features_returns_list_of_dicts():
  rows = engineer_features(_make_chain_data(AS_OF), AS_OF, iv_rank=55.0)
  assert isinstance(rows, list)
  assert len(rows) > 0
  assert isinstance(rows[0], dict)


def test_engineer_features_required_columns():
  rows = engineer_features(_make_chain_data(AS_OF), AS_OF, iv_rank=55.0)
  required = [
    "date", "ticker", "sector", "direction", "strike_short", "strike_long",
    "expiry", "dte", "entry_credit", "spread_width_dollars", "max_profit",
    "max_loss", "short_delta", "bid_ask_pct", "open_interest",
    "price_close", "momentum_5d", "momentum_20d", "atr_14", "volume_zscore",
    "price_vs_sma20", "vix_level", "iv_rank", "hv20", "hv_iv_ratio",
    "rsi_14", "macd_line", "macd_signal", "macd_histogram",
    "vix_bucket", "trend_direction", "iv_regime", "earnings_flag",
    "expiry_type", "direction",
  ]
  for col in required:
    assert col in rows[0], f"Missing column: {col}"


def test_engineer_features_direction_values():
  rows = engineer_features(_make_chain_data(AS_OF), AS_OF, iv_rank=55.0)
  directions = {r["direction"] for r in rows}
  assert directions.issubset({"call_spread", "put_spread"})


def test_engineer_features_filters_zero_credit():
  rows = engineer_features(_make_chain_data(AS_OF), AS_OF, iv_rank=55.0)
  assert all(r["entry_credit"] > 0 for r in rows)
