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

  positive_ev = [c for c in survivors if c["score"] > 0]
  if not positive_ev:
    return None

  return max(positive_ev, key=lambda c: c["score"])
