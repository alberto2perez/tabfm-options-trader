import os
from datetime import date
from pathlib import Path
from ..store.journal import insert_trade, init_db, _DEFAULT_DB

_TEMPLATE = """
══════════════════════════════════════════════
  NIGHTLY RECOMMENDATION  ·  {date}
══════════════════════════════════════════════
  Ticker       {ticker}
  Direction    {direction_label}
  Strikes      ${strike_short} / ${strike_long}
  Expiry       {expiry}  ({dte} DTE)
  Spread Width ${spread_width_dollars}
  Entry Credit ${entry_credit} est. fill (mid ${entry_credit_mid})
  Max Profit   ${max_profit_per} / contract
  Max Loss     ${max_loss_per} / contract
  Contracts    {contracts}  →  max exposure ${total_risk:.0f}
  ─────────────────────────────────────────────
  POP%         {pop_pct:.1f}%
  Exp. Return  ${exp_return_dollars:.0f} expected paper P&L
  IV Rank      {iv_rank:.1f}  ({iv_regime} IV)
  Regime       {vix_bucket} VIX · {trend_direction} · {iv_regime} IV
══════════════════════════════════════════════
  [PAPER LOGGED]  trade_id: {trade_id}
"""


def _apply_friction(mid_credit: float, bid_ask_pct: float) -> float:
  """Round-trip fill friction applied at entry: half the combined bid/ask
  spread plus regulatory fees. Keeps every downstream number (P&L, bankroll,
  calibration) compounding on realistic fills."""
  slip_frac = float(os.environ.get("TABFM_SLIPPAGE_FRAC", "0.50"))
  fees_rt = float(os.environ.get("TABFM_FEES_RT", "0.20"))
  combined_spread = (bid_ask_pct or 0.0) * mid_credit
  return round(max(mid_credit - slip_frac * combined_spread - fees_rt / 100.0, 0.01), 2)


def execute_paper_trade(trade: dict, as_of: date, path: Path = _DEFAULT_DB) -> int:
  init_db(path)
  mid_credit = trade["entry_credit"]
  fill_credit = _apply_friction(mid_credit, float(trade.get("bid_ask_pct") or 0.0))
  record = {
    "date_entered": str(as_of),
    "ticker": trade["ticker"],
    "direction": trade["direction"],
    "strike_short": trade["strike_short"],
    "strike_long": trade["strike_long"],
    "expiry": trade["expiry"],
    "dte": trade["dte"],
    "entry_credit": fill_credit,
    "entry_credit_mid": mid_credit,
    "spread_width": trade["spread_width_dollars"],
    "contracts": trade["contracts"],
    "max_loss": round(trade["contracts"] * (trade["spread_width_dollars"] - fill_credit) * 100, 2),
    "max_profit": round(trade["contracts"] * fill_credit * 100, 2),
    "pop_predicted": trade["pop_predicted"],
    "pop_raw": trade.get("pop_raw", trade["pop_predicted"]),
    "pop_market": trade.get("pop_market"),
    "exp_return": trade["exp_return"],
    "regime": f"{trade['vix_bucket']}|{trade['trend_direction']}|{trade['iv_regime']}",
  }
  return insert_trade(record, path)


def format_recommendation(trade: dict, trade_id: int, as_of: date) -> str:
  # These are CREDIT spreads: short call spread profits when price stays below
  # the short strike (bearish); short put spread profits above it (bullish).
  label = (
    "CALL CREDIT SPREAD  (bearish/neutral)" if trade["direction"] == "call_spread"
    else "PUT CREDIT SPREAD  (bullish/neutral)"
  )
  mid_credit = trade["entry_credit"]
  fill_credit = _apply_friction(mid_credit, float(trade.get("bid_ask_pct") or 0.0))
  return _TEMPLATE.format(
    date=as_of,
    ticker=trade["ticker"],
    direction_label=label,
    strike_short=trade["strike_short"],
    strike_long=trade["strike_long"],
    expiry=trade["expiry"],
    dte=trade["dte"],
    spread_width_dollars=trade["spread_width_dollars"],
    entry_credit=fill_credit,
    entry_credit_mid=mid_credit,
    max_profit_per=round(fill_credit, 2),
    max_loss_per=round(trade["spread_width_dollars"] - fill_credit, 2),
    contracts=trade["contracts"],
    total_risk=trade["total_risk"],
    pop_pct=trade["pop_predicted"] * 100,
    exp_return_dollars=trade["exp_return"] * trade["total_risk"],
    iv_rank=trade["iv_rank"],
    iv_regime=trade["iv_regime"],
    vix_bucket=trade["vix_bucket"],
    trend_direction=trade["trend_direction"],
    trade_id=trade_id,
  )
