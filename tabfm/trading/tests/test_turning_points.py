from pathlib import Path

from tabfm.trading.pipeline.turning_points import turning_point_report
from tabfm.trading.store.history_store import append_rows
from tabfm.trading.store.journal import init_db, insert_trade, close_trade


def _store_row(d, trend):
  return {"date": d, "ticker": "SPY", "trend_direction": trend,
          "vix_bucket": "normal", "iv_regime": "fair", "price_close": 740.0}


def _seed_store(path):
  rows = []
  for d in ["2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04", "2026-06-05"]:
    rows.append(_store_row(d, "uptrend"))
  for d in ["2026-06-08", "2026-06-09", "2026-06-10", "2026-06-11", "2026-06-12"]:
    rows.append(_store_row(d, "downtrend"))
  append_rows(rows, path)


def _trade(date_entered):
  return dict(
    date_entered=date_entered, ticker="SPY", direction="put_spread",
    strike_short=735.0, strike_long=730.0, expiry="2026-06-26", dte=21,
    entry_credit=2.0, spread_width=5.0, contracts=1, max_loss=300.0,
    max_profit=200.0, pop_predicted=0.7, pop_raw=0.7, exp_return=0.2,
    regime="normal|uptrend|fair",
  )


def test_detects_flip_and_trade_into_reversal(tmp_path):
  store = tmp_path / "store.parquet"
  db = tmp_path / "j.db"
  _seed_store(store)
  init_db(db)
  tid = insert_trade(_trade("2026-06-05"), db)   # entered day before the flip
  close_trade(tid, "lost", -300.0, "2026-06-26", db)

  m = turning_point_report(store, db, verbose=False)
  assert m["n_flips"] == 1
  flip = m["flips"][0]
  assert flip["from"] == "uptrend" and flip["to"] == "downtrend"
  assert flip["date"] == "2026-06-08"
  assert flip["trades_entered_before"] == 1
  assert flip["losers_into_reversal"] == 1
  assert m["trades_into_reversals"] == 1


def test_empty_when_single_trend(tmp_path):
  store = tmp_path / "store.parquet"
  db = tmp_path / "j.db"
  append_rows([_store_row("2026-06-01", "uptrend"),
               _store_row("2026-06-02", "uptrend")], store)
  init_db(db)
  m = turning_point_report(store, db, verbose=False)
  assert m["n_flips"] == 0
  assert m["flips"] == []
