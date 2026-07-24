"""Tracked-equity bankroll: fixed-fractional sizing limits from the journal.

Equity = starting capital + realized P&L of closed trades. Open positions are
never marked to market — sizing reacts only to realized outcomes. Recovery
mode (drawdown from peak beyond the brake) halves the per-trade slice until
equity sets a new all-time high.
"""
import os
from dataclasses import dataclass
from pathlib import Path

from ..store.journal import get_all_closed_trades, _DEFAULT_DB


@dataclass
class Bankroll:
  starting: float
  realized: float
  equity: float
  peak_equity: float
  drawdown_pct: float
  recovery_mode: bool
  slice_limit: float
  exposure_limit: float


def _config() -> tuple[float, float, float, float]:
  return (
    float(os.environ.get("TABFM_STARTING_CAPITAL", "2000")),
    float(os.environ.get("TABFM_RISK_PER_TRADE", "0.15")),
    float(os.environ.get("TABFM_MAX_EXPOSURE", "0.45")),
    float(os.environ.get("TABFM_DRAWDOWN_BRAKE", "0.25")),
  )


def _build(starting: float, risk_frac: float, max_exposure: float,
           brake: float, closed: list[dict]) -> Bankroll:
  ordered = sorted(
    closed,
    key=lambda t: (str(t.get("date_closed") or ""), t.get("trade_id") or 0),
  )
  equity = peak = starting
  realized = 0.0
  for t in ordered:
    pnl = float(t.get("actual_pnl") or 0)
    realized += pnl
    equity += pnl
    peak = max(peak, equity)
  # Floor AFTER the walk: peak tracking must see the true (possibly negative)
  # path; only the exposed equity is clamped to zero.
  equity = max(equity, 0.0)
  drawdown = (peak - equity) / peak if peak > 0 else 0.0
  recovery = drawdown > brake
  slice_frac = risk_frac * (0.5 if recovery else 1.0)
  return Bankroll(
    starting=starting,
    realized=round(realized, 2),
    equity=round(equity, 2),
    peak_equity=round(peak, 2),
    drawdown_pct=round(drawdown, 4),
    recovery_mode=recovery,
    slice_limit=round(equity * slice_frac, 2) if equity > 0 else 0.0,
    exposure_limit=round(equity * max_exposure, 2) if equity > 0 else 0.0,
  )


def get_bankroll(db_path: Path = _DEFAULT_DB) -> Bankroll:
  starting, risk_frac, max_exposure, brake = _config()
  if not Path(db_path).exists():
    closed = []  # journal not created yet → fresh bankroll
  else:
    closed = get_all_closed_trades(db_path)
  return _build(starting, risk_frac, max_exposure, brake, closed)


def default_bankroll() -> Bankroll:
  """Bankroll as if the journal were empty — for callers without a db path."""
  starting, risk_frac, max_exposure, brake = _config()
  return _build(starting, risk_frac, max_exposure, brake, [])
