from datetime import date, timedelta

import pandas as pd

from tabfm.trading.pipeline.accuracy_tracker import report
from tabfm.trading.pipeline.feature_engineer import engineer_features
from tabfm.trading.store.journal import init_db, insert_trade, close_trade

AS_OF = date(2026, 7, 24)


def _chain(with_pop: bool) -> pd.DataFrame:
  rows = []
  for i, strike in enumerate([95.0, 100.0]):
    r = {
      "strike": strike, "expiry": AS_OF + timedelta(days=14),
      "option_type": "put", "bid": 1.4 + i, "ask": 1.6 + i,
      "mid": 1.5 + i, "open_interest": 300, "delta": 0.20 + i * 0.1,
      "iv": 0.22, "dte": 14,
    }
    if with_pop:
      r["pop_market"] = 0.75 - i * 0.05
    rows.append(r)
  return pd.DataFrame(rows)


def _chain_data(with_pop: bool) -> dict:
  return {
    "ticker": "SPY", "sector": "index_etf", "vix": 18.5,
    "chain": _chain(with_pop),
    "underlying": {
      "close": 100.0, "sma20": 98.0, "sma50": 95.0, "atr14": 1.5,
      "hv20": 0.18, "volume": 5e7, "volume_zscore": 0.4,
      "momentum_5d": 0.01, "momentum_20d": 0.03,
      "rsi_14": 55.0, "macd_line": 0.5, "macd_signal": 0.3, "macd_histogram": 0.2,
    },
  }


def test_pop_market_copied_from_short_leg():
  rows = engineer_features(_chain_data(with_pop=True), AS_OF, iv_rank=55.0)
  assert rows, "fixture must yield a candidate"
  # short leg is the 100-strike (delta 0.30) -> pop_market 0.70
  assert rows[0]["pop_market"] == 0.70


def test_pop_market_none_when_chain_lacks_it():
  rows = engineer_features(_chain_data(with_pop=False), AS_OF, iv_rank=55.0)
  assert rows[0]["pop_market"] is None


def _closed_trade(pop_pred, pop_mkt, status):
  return dict(
    date_entered="2026-07-01", ticker="SPY", direction="put_spread",
    strike_short=700.0, strike_long=695.0, expiry="2026-07-18", dte=17,
    entry_credit=2.0, spread_width=5.0, contracts=1, max_loss=300.0,
    max_profit=200.0, pop_predicted=pop_pred, pop_raw=pop_pred,
    pop_market=pop_mkt, exp_return=0.2, regime="normal|sideways|fair",
  ), status


def test_brier_comparison_in_report(tmp_path):
  db = tmp_path / "j.db"
  init_db(db)
  for (trade, status), pnl in [
    (_closed_trade(0.8, 0.7, "won"), 200.0),
    (_closed_trade(0.6, 0.7, "lost"), -300.0),
    (_closed_trade(0.9, 0.8, "partial"), 100.0),
  ]:
    tid = insert_trade(trade, db)
    close_trade(tid, status, pnl, "2026-07-18", db)
  m = report(db_path=db, verbose=False)
  # outcomes: 1, 0, 1
  # tabfm: ((0.8-1)^2 + (0.6-0)^2 + (0.9-1)^2) / 3 = (0.04+0.36+0.01)/3
  assert m["brier_tabfm"] == round((0.04 + 0.36 + 0.01) / 3, 4)
  # market: ((0.7-1)^2 + (0.7-0)^2 + (0.8-1)^2) / 3 = (0.09+0.49+0.04)/3
  assert m["brier_market"] == round((0.09 + 0.49 + 0.04) / 3, 4)
  assert m["brier_n"] == 3


def test_brier_absent_without_pop_market(tmp_path):
  db = tmp_path / "j.db"
  init_db(db)
  trade, status = _closed_trade(0.8, None, "won")
  tid = insert_trade(trade, db)
  close_trade(tid, status, 200.0, "2026-07-18", db)
  m = report(db_path=db, verbose=False)
  assert "brier_tabfm" not in m
