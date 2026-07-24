"""Dumb-baseline shadow book: always sell the ~30-delta SPY put spread.

One virtual contract every run — no gates, no model, no bankroll. Exists
purely to measure what the full stack adds over the simplest premium-selling
rule. Never touches sizing or learning (journal helpers filter by strategy).
"""
import os
from datetime import date
from pathlib import Path

from .paper_executor import execute_paper_trade
from ..store.journal import _DEFAULT_DB


def enter_baseline_trade(
  chain_data_list: list, as_of: date, db_path: Path = _DEFAULT_DB
) -> int | None:
  spy = next((c for c in chain_data_list if c["ticker"] == "SPY"), None)
  if spy is None or not len(spy["chain"]):
    return None
  chain = spy["chain"]
  puts = chain[chain["option_type"] == "put"]
  puts = puts[(puts["delta"] >= 0.15) & (puts["delta"] <= 0.40)]
  if puts.empty:
    return None
  manage_dte = int(os.environ.get("TABFM_MANAGE_DTE", "21"))
  puts = puts[(puts["dte"] >= manage_dte + 7) & (puts["dte"] <= 45)]
  if puts.empty:
    return None
  short = puts.loc[(puts["delta"] - 0.30).abs().idxmin()]
  longs = chain[
    (chain["option_type"] == "put")
    & (chain["expiry"] == short["expiry"])
    & (chain["strike"] < short["strike"])
  ].sort_values("strike")
  if longs.empty:
    return None
  long = longs.iloc[-1]
  credit = float(short["bid"]) - float(long["ask"])
  width = float(short["strike"]) - float(long["strike"])
  if credit <= 0 or width <= 0:
    return None
  ba = (float(short["ask"]) - float(short["bid"])) + (float(long["ask"]) - float(long["bid"]))
  expiry = short["expiry"]
  expiry_date = expiry.date() if hasattr(expiry, "date") else expiry
  trade = {
    "ticker": "SPY", "direction": "put_spread",
    "strike_short": float(short["strike"]), "strike_long": float(long["strike"]),
    "expiry": str(expiry_date), "dte": int(short["dte"]),
    "entry_credit": round(credit, 2), "spread_width_dollars": round(width, 2),
    "bid_ask_pct": round(ba / credit, 4), "contracts": 1,
    "pop_predicted": 0.0, "exp_return": 0.0,
    "vix_bucket": "na", "trend_direction": "na", "iv_regime": "na",
  }
  tid = execute_paper_trade(trade, as_of, db_path, strategy="baseline")
  print(f"[Baseline] sold SPY {trade['strike_short']:g}/{trade['strike_long']:g} "
        f"exp {trade['expiry']} (~${trade['entry_credit']} credit, trade {tid})")
  return tid
