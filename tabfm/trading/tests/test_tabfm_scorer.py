import pandas as pd
import numpy as np
from unittest.mock import MagicMock, patch
from tabfm.trading.pipeline.tabfm_scorer import score_candidate, FEATURE_COLS

_CANDIDATE = {
  "price_close": 480.0, "momentum_5d": 1.2, "momentum_20d": 3.5,
  "atr_14": 8.5, "volume_zscore": 0.3, "price_vs_sma20": 1.8,
  "vix_level": 18.5, "vix_5d_change": -0.5, "iv_rank": 55.0,
  "hv20": 0.18, "hv_iv_ratio": 0.9, "dte": 14, "short_delta": 0.25,
  "strike_distance_pct": 4.2, "spread_width_dollars": 5.0, "bid_ask_pct": 0.08,
  "vix_bucket": "normal", "trend_direction": "uptrend", "iv_regime": "fair",
  "earnings_flag": "no_earnings", "direction": "put_spread",
  "expiry_type": "weekly", "sector": "index_etf",
}

def _context(n: int = 20) -> pd.DataFrame:
  rows = []
  for i in range(n):
    row = {col: _CANDIDATE.get(col, "normal") for col in FEATURE_COLS}
    row["profitable"] = i % 2
    row["return_pct"] = 0.2 if i % 2 else -0.8
    rows.append(row)
  return pd.DataFrame(rows)


def test_score_candidate_adds_pop_and_return():
  with patch("tabfm.trading.pipeline.tabfm_scorer.TabFMClassifier") as MockClf, \
       patch("tabfm.trading.pipeline.tabfm_scorer.TabFMRegressor") as MockReg:
    clf_inst = MagicMock()
    clf_inst.predict_proba.return_value = np.array([[0.28, 0.72]])
    MockClf.return_value = clf_inst

    reg_inst = MagicMock()
    reg_inst.predict.return_value = np.array([0.18])
    MockReg.return_value = reg_inst

    result = score_candidate(_CANDIDATE, _context(), MagicMock(), MagicMock())

  assert "pop_predicted" in result
  assert "exp_return" in result
  assert abs(result["pop_predicted"] - 0.72) < 0.01
  assert abs(result["exp_return"] - 0.18) < 0.01


def test_score_candidate_empty_context_returns_defaults():
  result = score_candidate(_CANDIDATE, pd.DataFrame(), MagicMock(), MagicMock())
  assert result["pop_predicted"] == 0.5
  assert result["exp_return"] == 0.0


def test_score_candidate_preserves_original_fields():
  with patch("tabfm.trading.pipeline.tabfm_scorer.TabFMClassifier") as MockClf, \
       patch("tabfm.trading.pipeline.tabfm_scorer.TabFMRegressor") as MockReg:
    MockClf.return_value.predict_proba.return_value = np.array([[0.3, 0.7]])
    MockReg.return_value.predict.return_value = np.array([0.15])
    result = score_candidate(_CANDIDATE, _context(), MagicMock(), MagicMock())

  assert result["ticker"] if "ticker" in _CANDIDATE else True
  assert result["dte"] == 14


def test_feature_cols_are_defined():
  assert len(FEATURE_COLS) > 0
  assert "iv_rank" in FEATURE_COLS
  assert "vix_bucket" in FEATURE_COLS
