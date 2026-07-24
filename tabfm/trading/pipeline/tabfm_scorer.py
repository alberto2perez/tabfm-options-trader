import pandas as pd
from tabfm import TabFMClassifier, TabFMRegressor

# Wrapper defaults (n_estimators=32, batch_size=1) run 64 serial forward
# passes of the 1.6B model per scoring call — ~90s each on CPU. A 4-member
# ensemble forwarded in one batch is ~40x faster with near-identical output.
_N_ESTIMATORS = 4
_BATCH_SIZE = None  # None = all ensemble members in one forward pass

FEATURE_COLS = [
  "price_close", "momentum_5d", "momentum_20d", "atr_14", "volume_zscore",
  "price_vs_sma20", "vix_level", "vix_5d_change", "iv_rank", "hv20",
  "hv_iv_ratio", "rsi_14", "macd_line", "macd_signal", "macd_histogram",
  "days_to_next_megacap_earnings", "days_to_next_macro_event", "iv_spike_score",
  "dte", "short_delta", "strike_distance_pct",
  "spread_width_dollars", "bid_ask_pct",
  "vix_bucket", "trend_direction", "iv_regime", "earnings_flag",
  "direction", "expiry_type", "sector",
]


def score_candidates_batch(
  candidates: list[dict],
  context: pd.DataFrame,
  clf_model,
  reg_model,
) -> list[dict]:
  """Score all candidates sharing the same regime context in a single TabFM fit+predict.

  Fits the model once on the shared context, then predicts on all candidates
  together — O(1) model calls instead of O(N) per candidate.
  Falls back to neutral defaults (0.5, 0.0) when context is insufficient.
  """
  fallback = [
    {**c, "pop_predicted": 0.5, "exp_return": 0.0} for c in candidates
  ]

  if context.empty or "profitable" not in context.columns:
    return fallback

  y_clf = context["profitable"].values
  if len(y_clf) < 20 or len(set(y_clf)) < 2:
    return fallback

  y_reg = context["return_pct"].values
  X_train = context[FEATURE_COLS].copy()
  X_test = pd.DataFrame([{col: c.get(col) for col in FEATURE_COLS} for c in candidates])

  clf = TabFMClassifier(model=clf_model, n_estimators=_N_ESTIMATORS, batch_size=_BATCH_SIZE)
  clf.fit(X_train, y_clf)
  probas = clf.predict_proba(X_test)

  reg = TabFMRegressor(model=reg_model, n_estimators=_N_ESTIMATORS, batch_size=_BATCH_SIZE)
  reg.fit(X_train, y_reg)
  exp_returns = reg.predict(X_test)

  results = []
  for i, candidate in enumerate(candidates):
    proba = probas[i]
    pop = float(proba[1]) if len(proba) > 1 else float(proba[0])
    results.append({
      **candidate,
      "pop_predicted": round(pop, 4),
      "exp_return": round(float(exp_returns[i]), 4),
    })
  return results


def score_candidate(
  candidate: dict,
  context: pd.DataFrame,
  clf_model,
  reg_model,
) -> dict:
  """Single-candidate scoring — delegates to batch for consistency."""
  return score_candidates_batch([candidate], context, clf_model, reg_model)[0]
