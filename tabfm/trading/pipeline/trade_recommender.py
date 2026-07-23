import math

_MAX_RISK = 1000.0
_MAX_CONTRACTS = 10


def _passes_filters(row: dict) -> bool:
  if row["spread_width_dollars"] * 100 > _MAX_RISK:
    return False
  if row["bid_ask_pct"] > 0.15:
    return False
  if row["open_interest"] < 100:
    return False
  if not (7 <= row["dte"] <= 45):
    return False
  if not (0.15 <= row["short_delta"] <= 0.40):
    return False
  if row["earnings_flag"] == "earnings_week":
    return False
  return True


def _contracts(spread_width: float) -> int:
  n = math.floor(_MAX_RISK / (spread_width * 100))
  return max(1, min(n, _MAX_CONTRACTS))


def select_trade(scored_candidates: list[dict]) -> dict | None:
  """Apply filter gauntlet and return the single highest expected-value trade."""
  survivors = [c for c in scored_candidates if _passes_filters(c)]
  if not survivors:
    return None

  for c in survivors:
    c["contracts"] = _contracts(c["spread_width_dollars"])
    c["total_risk"] = c["contracts"] * c["spread_width_dollars"] * 100
    c["score"] = c["pop_predicted"] * c["exp_return"]

  # Candidates where TabFM gave a real (non-fallback) prediction
  tabfm_scored = [
    c for c in survivors
    if not (c["pop_predicted"] == 0.5 and c["exp_return"] == 0.0)
  ]
  if tabfm_scored:
    positive_ev = [c for c in tabfm_scored if c["score"] > 0]
    if not positive_ev:
      return None  # model has predictions and all are negative EV → skip
    return max(positive_ev, key=lambda c: c["score"])

  # Cold-start: no TabFM context yet → rank structurally by credit yield
  best = max(survivors, key=lambda c: c["entry_credit"] / c["spread_width_dollars"])
  best["score"] = round(best["entry_credit"] / best["spread_width_dollars"], 4)
  return best
