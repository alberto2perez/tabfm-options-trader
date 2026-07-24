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


def _spread_mark(adapter: DataAdapter, trade: dict, as_of: date) -> float | None:
  """Cost to close the spread from real option marks, when the adapter has them.

  Intrinsic-only valuation ignores time value, so an OTM spread looks like
  instant max profit the day after entry — real marks prevent phantom early
  closes. Returns None when the chain/strikes aren't available.
  """
  try:
    import pandas as pd
    chain = adapter.get_options_chain(trade["ticker"], as_of)
    if chain is None or chain.empty:
      return None
    opt = "call" if trade["direction"] == "call_spread" else "put"
    sub = chain[chain["option_type"] == opt].copy()
    expiries = pd.to_datetime(sub["expiry"]).dt.strftime("%Y-%m-%d")
    sub = sub[expiries == str(trade["expiry"])]
    short_mid = float(sub[sub["strike"] == trade["strike_short"]]["mid"].iloc[0])
    long_mid = float(sub[sub["strike"] == trade["strike_long"]]["mid"].iloc[0])
    return max(0.0, short_mid - long_mid)
  except Exception:
    return None


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

    current_val = _spread_mark(adapter, trade, as_of)
    if current_val is None:
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
