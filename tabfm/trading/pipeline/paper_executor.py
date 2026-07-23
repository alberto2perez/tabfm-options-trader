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
  Entry Credit ${entry_credit} mid-price
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


def execute_paper_trade(trade: dict, as_of: date, path: Path = _DEFAULT_DB) -> int:
  init_db(path)
  record = {
    "date_entered": str(as_of),
    "ticker": trade["ticker"],
    "direction": trade["direction"],
    "strike_short": trade["strike_short"],
    "strike_long": trade["strike_long"],
    "expiry": trade["expiry"],
    "dte": trade["dte"],
    "entry_credit": trade["entry_credit"],
    "spread_width": trade["spread_width_dollars"],
    "contracts": trade["contracts"],
    "max_loss": trade["contracts"] * (trade["spread_width_dollars"] - trade["entry_credit"]) * 100,
    "max_profit": trade["contracts"] * trade["entry_credit"] * 100,
    "pop_predicted": trade["pop_predicted"],
    "pop_raw": trade.get("pop_raw", trade["pop_predicted"]),
    "exp_return": trade["exp_return"],
    "regime": f"{trade['vix_bucket']}|{trade['trend_direction']}|{trade['iv_regime']}",
  }
  return insert_trade(record, path)


def format_recommendation(trade: dict, trade_id: int, as_of: date) -> str:
  label = (
    "CALL SPREAD  (bullish)" if trade["direction"] == "call_spread"
    else "PUT SPREAD  (bearish)"
  )
  return _TEMPLATE.format(
    date=as_of,
    ticker=trade["ticker"],
    direction_label=label,
    strike_short=trade["strike_short"],
    strike_long=trade["strike_long"],
    expiry=trade["expiry"],
    dte=trade["dte"],
    spread_width_dollars=trade["spread_width_dollars"],
    entry_credit=trade["entry_credit"],
    max_profit_per=round(trade["entry_credit"], 2),
    max_loss_per=round(trade["spread_width_dollars"] - trade["entry_credit"], 2),
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
