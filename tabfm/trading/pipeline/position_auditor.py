from datetime import date
from pathlib import Path
from ..store.journal import get_open_trades, close_trade, _DEFAULT_DB
from ..adapters.base import DataAdapter

_EARLY_CLOSE_THRESHOLD = 0.50


def _is_winner(trade: dict, underlying_price: float) -> bool:
  if trade["direction"] == "put_spread":
    return underlying_price > trade["strike_short"]
  return underlying_price < trade["strike_short"]


def _estimate_current_value(trade: dict, underlying_price: float) -> float:
  short = trade["strike_short"]
  width = trade["spread_width"]
  if trade["direction"] == "put_spread":
    intrinsic = max(0.0, short - underlying_price)
  else:
    intrinsic = max(0.0, underlying_price - short)
  return min(intrinsic, width)


def audit_positions(
  adapter: DataAdapter, as_of: date, db_path: Path = _DEFAULT_DB
) -> list[dict]:
  """Close expired or 50%-profit positions and record actual P&L."""
  open_trades = get_open_trades(db_path)
  closed = []

  for trade in open_trades:
    expiry = date.fromisoformat(str(trade["expiry"]))
    settle_date = expiry if as_of >= expiry else as_of
    try:
      underlying = adapter.get_underlying(trade["ticker"], settle_date)
    except Exception:
      continue

    S = underlying["close"]
    credit = trade["entry_credit"]
    width = trade["spread_width"]
    contracts = trade["contracts"]

    current_val = _estimate_current_value(trade, S)
    unrealized = (credit - current_val) * contracts * 100
    max_profit = credit * contracts * 100

    if as_of < expiry and unrealized >= max_profit * _EARLY_CLOSE_THRESHOLD:
      close_trade(trade["trade_id"], "partial", round(unrealized, 2), str(as_of), db_path)
      closed.append({**trade, "status": "partial", "actual_pnl": round(unrealized, 2)})
      continue

    if as_of >= expiry:
      if _is_winner(trade, S):
        pnl = round(credit * contracts * 100, 2)
        status = "won"
      else:
        pnl = round(-(width - credit) * contracts * 100, 2)
        status = "lost"
      close_trade(trade["trade_id"], status, pnl, str(as_of), db_path)
      closed.append({**trade, "status": status, "actual_pnl": pnl})

  return closed
