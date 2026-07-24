"""Trend guard: advise on open positions the trend has turned against.

Advisory only — never closes or modifies a position. A credit spread is only
"challenged" when the adverse trend is CONFIRMED by an actual unrealized loss,
so a position comfortably OTM through a trend wiggle is not flagged (avoids
noise-trading). Its real value is live on real marks; in backtests the
synthetic marks under-price panic costs, so it exercises the logic only."""
import os
from datetime import date

from .feature_engineer import _trend_direction
from .position_auditor import _spread_mark


def assess_trend_risk(open_trades: list, adapter, as_of: date) -> list:
  if os.environ.get("TABFM_TREND_GUARD", "on").lower() == "off":
    return []
  challenged = []
  for t in open_trades:
    try:
      u = adapter.get_underlying(t["ticker"], as_of)
    except Exception:
      continue
    trend = _trend_direction(u["close"], u["sma20"], u["sma50"])
    adverse = (
      (t["direction"] == "put_spread" and trend == "downtrend")
      or (t["direction"] == "call_spread" and trend == "uptrend")
    )
    if not adverse:
      continue
    mark = _spread_mark(adapter, t, as_of)
    if mark is None:
      continue
    credit = float(t["entry_credit"])
    contracts = int(t["contracts"])
    unrealized = (credit - mark) * contracts * 100
    if unrealized >= 0:
      continue  # adverse trend but position still profitable — not an emergency
    max_loss = float(t.get("max_loss") or 0) or (
      (float(t["spread_width"]) - credit) * contracts * 100)
    loss_fraction = (-unrealized / max_loss) if max_loss > 0 else 0.0
    dte_left = (date.fromisoformat(str(t["expiry"])) - as_of).days
    stop_amount = credit * contracts * 100  # loss realized if the 2x stop fires
    legs = f"{t['strike_short']:g}/{t['strike_long']:g}"
    dirn = t["direction"].replace("_", " ")
    if loss_fraction >= 0.5:
      action = "CLOSE NOW"
      message = (f"#{t['trade_id']} {t['ticker']} {dirn} {legs}: adverse {trend}, "
                 f"at {loss_fraction:.0%} of max loss ({dte_left}d left) — exit now "
                 f"rather than wait for the 2x stop (~-${stop_amount:.0f}).")
    else:
      action = "CONSIDER CLOSING"
      message = (f"#{t['trade_id']} {t['ticker']} {dirn} {legs}: adverse {trend}, "
                 f"losing ${abs(unrealized):.0f} ({dte_left}d left, stop "
                 f"~-${stop_amount:.0f}) — close early or roll the tested side "
                 f"if the trend persists.")
    challenged.append({
      "trade_id": t["trade_id"], "ticker": t["ticker"], "direction": t["direction"],
      "trend": trend, "unrealized": round(unrealized, 2),
      "loss_fraction": round(loss_fraction, 4), "dte_left": dte_left,
      "stop_level": round(stop_amount, 2), "action": action, "message": message,
    })
  return challenged
