"""Turning-point analytics for backtests: did the system see direction
changes coming, and how much did each cost? Read-only over the history store
(per-day trend) and the journal (closed trades)."""
from datetime import date, timedelta
from pathlib import Path

from ..store.history_store import load_store, _DEFAULT_STORE
from ..store.journal import get_all_closed_trades, _DEFAULT_DB

_BEFORE_DAYS = 5     # calendar days before a flip to look for open positions
_AFTER_DAYS = 10     # window after a flip for realized P&L


def _trend_sequence(store, ticker: str) -> list[tuple[str, str]]:
  """One (date, trend) per date for the ticker, ordered — most common trend
  per date to collapse multiple candidate rows."""
  df = store[store["ticker"] == ticker]
  if df.empty or "trend_direction" not in df.columns:
    return []
  seq = []
  for d, sub in df.groupby("date"):
    trend = sub["trend_direction"].mode()
    seq.append((str(d), str(trend.iloc[0]) if len(trend) else "unknown"))
  return sorted(seq, key=lambda x: x[0])


def turning_point_report(
  store_path: Path = _DEFAULT_STORE,
  db_path: Path = _DEFAULT_DB,
  ticker: str = "SPY",
  verbose: bool = True,
) -> dict:
  store = load_store(store_path)
  seq = _trend_sequence(store, ticker) if not store.empty else []
  closed = get_all_closed_trades(db_path, strategy="model")

  flips = []
  prev_trend = None
  for d_str, trend in seq:
    if prev_trend is not None and trend != prev_trend:
      flip_date = date.fromisoformat(d_str)
      before_lo = flip_date - timedelta(days=_BEFORE_DAYS)
      after_hi = flip_date + timedelta(days=_AFTER_DAYS)
      entered_before, losers = 0, 0
      pnl_after = 0.0
      for t in closed:
        if t.get("ticker") != ticker:
          continue
        try:
          entered = date.fromisoformat(str(t["date_entered"]))
        except (ValueError, KeyError, TypeError):
          continue
        if before_lo <= entered < flip_date:
          entered_before += 1
          if t["status"] in ("lost", "stopped"):
            losers += 1
        closed_on = t.get("date_closed")
        if closed_on:
          try:
            cd = date.fromisoformat(str(closed_on))
            if flip_date <= cd <= after_hi:
              pnl_after += float(t.get("actual_pnl") or 0)
          except ValueError:
            pass
      flips.append({
        "date": d_str, "from": prev_trend, "to": trend,
        "trades_entered_before": entered_before,
        "losers_into_reversal": losers,
        "pnl_after": round(pnl_after, 2),
      })
    if trend != prev_trend:
      prev_trend = trend

  metrics = {
    "n_flips": len(flips),
    "flips": flips,
    "trades_into_reversals": sum(f["losers_into_reversal"] for f in flips),
  }

  if verbose and flips:
    print("\n╔══════════════════════════════════════╗")
    print(f"  TURNING POINTS ({ticker})")
    print("╠══════════════════════════════════════╣")
    for f in flips:
      print(f"  {f['date']}  {f['from']} → {f['to']}")
      print(f"    open into reversal: {f['trades_entered_before']} "
            f"({f['losers_into_reversal']} lost) · P&L after: ${f['pnl_after']:.2f}")
    print(f"  Trades caught into reversals: {metrics['trades_into_reversals']}")
    print("╚══════════════════════════════════════╝")
  return metrics
