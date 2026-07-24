"""Journal-backed Platt calibration for TabFM POP% predictions.

Every closed paper trade is a (predicted POP, realized outcome) pair. Platt
scaling fits calibrated = sigmoid(a * logit(raw) + b) on those pairs so the
pipeline's probabilities track realized win rates — and keeps re-fitting
nightly as new trades close, correcting systematic bias in whatever data
regime (synthetic, paper, live) the journal reflects.
"""
import math
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression

from ..store.journal import get_all_closed_trades, _DEFAULT_DB

_MIN_TRADES = 25
_EPS = 0.01  # clip raw probabilities away from 0/1 before logit


def _logit(p: float) -> float:
  p = min(max(p, _EPS), 1 - _EPS)
  return math.log(p / (1 - p))


def fit_calibration(
  db_path: Path = _DEFAULT_DB, min_trades: int = _MIN_TRADES
) -> tuple[float, float] | None:
  """Fit Platt scaling on closed trades. Returns (a, b) or None if too few.

  Outcome definition matches the accuracy tracker: won/partial = 1, lost = 0.
  Uses pop_raw (the uncalibrated model output) when recorded, falling back to
  pop_predicted for rows written before the pop_raw column existed.
  """
  trades = get_all_closed_trades(db_path, strategy="model")
  if len(trades) < min_trades:
    return None

  raw = [t.get("pop_raw") or t["pop_predicted"] for t in trades]
  y = [1 if t["status"] in ("won", "partial") else 0 for t in trades]
  if len(set(y)) < 2:
    return None  # need both outcomes to fit a slope

  X = np.array([[_logit(p)] for p in raw])
  lr = LogisticRegression()
  lr.fit(X, np.array(y))
  return float(lr.coef_[0][0]), float(lr.intercept_[0])


def calibrate_pop(pop: float, params: tuple[float, float]) -> float:
  """Apply fitted Platt parameters to a raw POP prediction."""
  a, b = params
  z = a * _logit(pop) + b
  return float(1 / (1 + math.exp(-z)))


def fit_return_calibration(
  db_path: Path = _DEFAULT_DB, min_trades: int = 25
) -> tuple[float, float] | None:
  """Linear map from predicted exp_return to realized return fraction.

  Realized fraction = actual_pnl / max_loss for closed model trades. Returns
  (slope, intercept) or None below min_trades / without prediction variance.
  """
  trades = get_all_closed_trades(db_path, strategy="model")
  xs, ys = [], []
  for t in trades:
    max_loss = float(t.get("max_loss") or 0)
    if max_loss <= 0 or t.get("actual_pnl") is None:
      continue
    pred = t.get("exp_return_raw")
    if pred is None:
      pred = t.get("exp_return")
    if pred is None:
      continue
    xs.append(float(pred))
    ys.append(float(t["actual_pnl"]) / max_loss)
  if len(xs) < min_trades:
    return None
  x_arr, y_arr = np.array(xs), np.array(ys)
  if float(x_arr.std()) < 1e-9:
    return None
  slope, intercept = np.polyfit(x_arr, y_arr, 1)
  if slope <= 0:
    return None  # inverted/degenerate fit — identity is safer than flipping EV ranking
  return float(slope), float(intercept)


def calibrate_return(exp_return: float, params: tuple[float, float]) -> float:
  slope, intercept = params
  return float(slope * exp_return + intercept)
