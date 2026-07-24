from tabfm.trading.pipeline.calibrator import fit_return_calibration, calibrate_return
from tabfm.trading.store.journal import init_db, insert_trade, close_trade

import pytest


def _trade(exp_return):
  return dict(
    date_entered="2026-07-01", ticker="SPY", direction="put_spread",
    strike_short=700.0, strike_long=695.0, expiry="2026-07-18", dte=17,
    entry_credit=2.0, spread_width=5.0, contracts=1, max_loss=300.0,
    max_profit=200.0, pop_predicted=0.7, pop_raw=0.7,
    exp_return=exp_return, exp_return_raw=exp_return,
    regime="normal|sideways|fair",
  )


def _seed_biased(db, n):
  """Model predicts 2x reality: realized fraction = 0.5 * predicted."""
  init_db(db)
  for i in range(n):
    pred = 0.10 + (i % 10) * 0.02          # 0.10 .. 0.28, has variance
    realized_frac = 0.5 * pred
    tid = insert_trade(_trade(pred), db)
    close_trade(tid, "won", realized_frac * 300.0, f"2026-07-{(i % 27) + 1:02d}", db)


def test_identity_below_min_trades(tmp_path):
  db = tmp_path / "j.db"
  _seed_biased(db, 10)
  assert fit_return_calibration(db) is None


def test_recovers_linear_bias(tmp_path):
  db = tmp_path / "j.db"
  _seed_biased(db, 30)
  params = fit_return_calibration(db)
  assert params is not None
  a, b = params
  assert a == pytest.approx(0.5, abs=0.02)
  assert b == pytest.approx(0.0, abs=0.01)
  assert calibrate_return(0.20, params) == pytest.approx(0.10, abs=0.01)


def test_no_variance_returns_none(tmp_path):
  db = tmp_path / "j.db"
  init_db(db)
  for i in range(30):
    tid = insert_trade(_trade(0.20), db)   # constant prediction
    close_trade(tid, "won", 30.0, f"2026-07-{(i % 27) + 1:02d}", db)
  assert fit_return_calibration(db) is None


def test_inverted_fit_returns_none(tmp_path):
  db = tmp_path / "j.db"
  init_db(db)
  # Anti-correlated: higher prediction -> worse outcome
  for i in range(30):
    pred = 0.10 + (i % 10) * 0.02
    tid = insert_trade(_trade(pred), db)
    close_trade(tid, "won", (0.30 - pred) * 300.0, f"2026-07-{(i % 27) + 1:02d}", db)
  assert fit_return_calibration(db) is None
