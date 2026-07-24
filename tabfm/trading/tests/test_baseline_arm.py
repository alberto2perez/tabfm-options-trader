from datetime import date

import pandas as pd

from tabfm.trading.pipeline.accuracy_tracker import report
from tabfm.trading.pipeline.bankroll import get_bankroll
from tabfm.trading.pipeline.baseline import enter_baseline_trade
from tabfm.trading.store.journal import (
  init_db, insert_trade, close_trade, get_open_trades, get_all_closed_trades,
)

AS_OF = date(2026, 7, 24)


def _spy_chain_data():
  chain = pd.DataFrame([
    {"strike": 700.0, "expiry": pd.Timestamp("2026-08-21"), "option_type": "put",
     "bid": 3.40, "ask": 3.50, "mid": 3.45, "open_interest": 500,
     "delta": 0.31, "iv": 0.2, "dte": 28},
    {"strike": 695.0, "expiry": pd.Timestamp("2026-08-21"), "option_type": "put",
     "bid": 2.40, "ask": 2.50, "mid": 2.45, "open_interest": 500,
     "delta": 0.24, "iv": 0.2, "dte": 28},
    {"strike": 690.0, "expiry": pd.Timestamp("2026-08-21"), "option_type": "put",
     "bid": 1.70, "ask": 1.80, "mid": 1.75, "open_interest": 500,
     "delta": 0.18, "iv": 0.2, "dte": 28},
  ])
  return [{"ticker": "SPY", "sector": "index_etf", "chain": chain,
           "underlying": {"close": 738.0}, "vix": 18.5}]


def test_baseline_enters_one_contract(tmp_path):
  db = tmp_path / "j.db"
  init_db(db)
  tid = enter_baseline_trade(_spy_chain_data(), AS_OF, db)
  assert tid is not None
  rows = get_open_trades(db, strategy="baseline")
  assert len(rows) == 1
  r = rows[0]
  assert r["strategy"] == "baseline"
  assert r["contracts"] == 1
  # short = delta closest to 0.30 -> 700 strike; long = adjacent below -> 695
  assert r["strike_short"] == 700.0 and r["strike_long"] == 695.0


def test_baseline_stacks_across_nights(tmp_path):
  db = tmp_path / "j.db"
  init_db(db)
  enter_baseline_trade(_spy_chain_data(), AS_OF, db)
  enter_baseline_trade(_spy_chain_data(), date(2026, 7, 27), db)
  assert len(get_open_trades(db, strategy="baseline")) == 2


def test_baseline_skips_without_spy(tmp_path):
  db = tmp_path / "j.db"
  init_db(db)
  assert enter_baseline_trade([], AS_OF, db) is None


def test_baseline_invisible_to_model_helpers_and_bankroll(tmp_path):
  db = tmp_path / "j.db"
  init_db(db)
  enter_baseline_trade(_spy_chain_data(), AS_OF, db)
  assert get_open_trades(db) == []                 # default filters to model
  assert get_all_closed_trades(db) == []
  bk = get_bankroll(db)
  assert bk.equity == 2000.0                       # untouched by baseline
  # close the baseline trade at a loss; bankroll must still ignore it
  tid = get_open_trades(db, strategy=None)[0]["trade_id"]
  close_trade(tid, "lost", -300.0, "2026-08-21", db)
  assert get_bankroll(db).equity == 2000.0


def test_tracker_reports_both_arms(tmp_path):
  db = tmp_path / "j.db"
  init_db(db)
  base = dict(
    date_entered="2026-07-01", ticker="SPY", direction="put_spread",
    strike_short=700.0, strike_long=695.0, expiry="2026-07-18", dte=17,
    entry_credit=2.0, spread_width=5.0, contracts=1, max_loss=300.0,
    max_profit=200.0, pop_predicted=0.7, pop_raw=0.7, exp_return=0.2,
    regime="normal|sideways|fair",
  )
  t1 = insert_trade(base, db)                       # model (default)
  close_trade(t1, "won", 200.0, "2026-07-18", db)
  t2 = insert_trade({**base, "strategy": "baseline"}, db)
  close_trade(t2, "lost", -300.0, "2026-07-18", db)
  m = report(db_path=db, verbose=False)
  assert m["total_trades"] == 1                     # model-only headline
  assert m["baseline_trades"] == 1
  assert m["baseline_pnl"] == -300.0
  assert m["model_vs_baseline_pnl"] == 500.0
