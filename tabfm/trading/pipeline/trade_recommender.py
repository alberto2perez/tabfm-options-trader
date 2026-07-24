import math

from .bankroll import Bankroll, default_bankroll

_MAX_CONTRACTS = 10


def _passes_filters(row: dict) -> bool:
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



def _is_open_duplicate(candidate: dict, open_trades: list[dict]) -> bool:
  """True when an identical spread (ticker/direction/strikes/expiry) is already open."""
  for t in open_trades:
    if (
      candidate.get("ticker") == t.get("ticker")
      and candidate.get("direction") == t.get("direction")
      and candidate.get("strike_short") == t.get("strike_short")
      and candidate.get("strike_long") == t.get("strike_long")
      and str(candidate.get("expiry")) == str(t.get("expiry"))
    ):
      return True
  return False


def select_trade(
  scored_candidates: list[dict],
  open_trades: list[dict] | None = None,
  bankroll: Bankroll | None = None,
) -> dict | None:
  """Apply filter gauntlet and return the single highest expected-value trade.

  Sizing is bankroll-driven: each trade risks at most the per-trade slice,
  and total open max loss stays within the exposure limit. Candidates
  identical to an open position are skipped.
  """
  open_trades = open_trades or []
  if bankroll is None:
    bankroll = default_bankroll()

  open_risk = sum(float(t.get("max_loss") or 0) for t in open_trades)
  budget = min(bankroll.slice_limit, bankroll.exposure_limit - open_risk)
  if budget <= 0:
    return None

  survivors = [
    c for c in scored_candidates
    if _passes_filters(c) and not _is_open_duplicate(c, open_trades)
  ]
  if not survivors:
    return None

  sized = []
  for c in survivors:
    # True per-contract max loss; entry_credit may be absent in synthetic tests
    loss_per_contract = (c["spread_width_dollars"] - c.get("entry_credit", 0.0)) * 100
    if loss_per_contract <= 0:
      continue
    c["contracts"] = min(math.floor(budget / loss_per_contract), _MAX_CONTRACTS)
    if c["contracts"] < 1:
      continue  # doesn't fit the bankroll budget
    sized.append(c)
  survivors = sized
  if not survivors:
    return None

  for c in survivors:
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
