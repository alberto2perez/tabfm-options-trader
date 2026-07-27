import math
import os
from collections import defaultdict

from .bankroll import Bankroll, default_bankroll

_MAX_CONTRACTS = 10
# Correlated-risk buckets: SPY/QQQ/IWM are ~0.9 correlated, so short puts
# across them are ONE levered short-vol bet, not diversification.
_INDEX_ETFS = {"SPY", "QQQ", "IWM"}


def _bucket_bias(ticker, direction) -> tuple:
  """Group a position by correlated underlying + directional bias, so
  concentration is capped per correlated bet, not per ticker."""
  bucket = "index" if ticker in _INDEX_ETFS else ticker
  bias = "bullish" if direction == "put_spread" else "bearish"
  return (bucket, bias)


def _passes_filters(row: dict) -> bool:
  if row["bid_ask_pct"] > 0.15:
    return False
  if row["open_interest"] < 100:
    return False
  # Entries must have room to live before DTE management (auditor closes at
  # TABFM_MANAGE_DTE): floor = manage_dte + 7 so fresh trades aren't
  # force-closed next session at breakeven, polluting win-rate/calibration.
  manage_dte = int(os.environ.get("TABFM_MANAGE_DTE", "21"))
  if not (manage_dte + 7 <= row["dte"] <= 45):
    return False
  if not (0.15 <= row["short_delta"] <= 0.40):
    return False
  if row["earnings_flag"] == "earnings_week":
    return False
  # Entry quality: a real expected-value gate (replaces the miscalibrated
  # credit/width ratio floor, which rejected all real SPY spreads). Losses are
  # capped near loss_mult × credit by the 2×-mark stop, so structural
  # EV = P(win)·credit − P(loss)·(loss_mult·credit) with P(win) ≈ 1 − short_delta.
  # An ABSOLUTE credit floor also excludes trades too thin to beat fees/slippage
  # even when +EV (high IV *rank* with cheap *absolute* premium slips past a
  # ratio floor but not this).
  credit = row.get("entry_credit")
  if credit is not None:
    if credit < float(os.environ.get("TABFM_MIN_CREDIT_ABS", "0.25")):
      return False
    sd = float(row.get("short_delta", 0.30))
    loss_mult = float(os.environ.get("TABFM_EV_LOSS_MULT", "2.0"))
    ev = (1.0 - sd) * credit - sd * loss_mult * credit
    if ev <= 0:
      return False
  min_iv_rank = float(os.environ.get("TABFM_MIN_IV_RANK", "30.0"))
  if float(row.get("iv_rank", 50.0)) < min_iv_rank:
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
  # Per-correlated-bucket open risk, to cap concentration in one directional bet
  bucket_open: dict = defaultdict(float)
  for t in open_trades:
    bucket_open[_bucket_bias(t.get("ticker"), t.get("direction"))] += float(t.get("max_loss") or 0)
  max_bucket_risk = bankroll.equity * float(os.environ.get("TABFM_MAX_BUCKET_RISK", "0.25"))

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
    # Tightest of: per-trade slice, total exposure headroom, AND the
    # per-correlated-bucket concentration cap (prevents piling into one bet).
    bb = _bucket_bias(c.get("ticker"), c.get("direction"))
    budget_c = min(budget, max_bucket_risk - bucket_open[bb])
    c["contracts"] = min(math.floor(budget_c / loss_per_contract), _MAX_CONTRACTS)
    if c["contracts"] < 1:
      continue  # doesn't fit the bankroll budget
    sized.append(c)
  survivors = sized
  if not survivors:
    return None

  for c in survivors:
    c["total_risk"] = round(
      c["contracts"] * (c["spread_width_dollars"] - c.get("entry_credit", 0.0)) * 100, 2
    )
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
