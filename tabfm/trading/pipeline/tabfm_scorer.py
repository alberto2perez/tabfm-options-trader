import pandas as pd
from tabfm import TabFMClassifier, TabFMRegressor

FEATURE_COLS = [
  "price_close", "momentum_5d", "momentum_20d", "atr_14", "volume_zscore",
  "price_vs_sma20", "vix_level", "vix_5d_change", "iv_rank", "hv20",
  "hv_iv_ratio", "rsi_14", "macd_line", "macd_signal", "macd_histogram",
  "dte", "short_delta", "strike_distance_pct",
  "spread_width_dollars", "bid_ask_pct",
  "vix_bucket", "trend_direction", "iv_regime", "earnings_flag",
  "direction", "expiry_type", "sector",
]


def score_candidate(
  candidate: dict,
  context: pd.DataFrame,
  clf_model,
  reg_model,
) -> dict:
  """Score a candidate spread with TabFMClassifier (POP%) and TabFMRegressor.

  Returns the candidate dict with pop_predicted and exp_return added.
  Falls back to neutral defaults (0.5, 0.0) when context is empty.
  """
  if context.empty or "profitable" not in context.columns:
    return {**candidate, "pop_predicted": 0.5, "exp_return": 0.0}

  # Need both classes in training set for meaningful POP%; fall back otherwise.
  y_clf = context["profitable"].values
  if len(set(y_clf)) < 2:
    return {**candidate, "pop_predicted": 0.5, "exp_return": 0.0}

  y_reg = context["return_pct"].values
  X_train = context[FEATURE_COLS].copy()
  X_test = pd.DataFrame([{col: candidate.get(col) for col in FEATURE_COLS}])

  clf = TabFMClassifier(model=clf_model)
  clf.fit(X_train, y_clf)
  proba = clf.predict_proba(X_test)[0]
  pop = float(proba[1]) if len(proba) > 1 else float(proba[0])

  reg = TabFMRegressor(model=reg_model)
  reg.fit(X_train, y_reg)
  exp_return = float(reg.predict(X_test)[0])

  return {**candidate, "pop_predicted": round(pop, 4), "exp_return": round(exp_return, 4)}
