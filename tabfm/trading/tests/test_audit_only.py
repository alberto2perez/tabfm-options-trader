import sqlite3
from datetime import date

import pandas as pd

from tabfm.trading.run_nightly import run_audit_only
from tabfm.trading.store.journal import init_db, insert_trade


class _MarkAdapter:
  """Marks the open spread at >= 2x credit so the stop fires."""
  def get_underlying(self, ticker, as_of):
    return {"close": 700.0}
  def get_options_chain(self, ticker, as_of):
    return pd.DataFrame([
      {"strike": 680.0, "expiry": pd.Timestamp("2026-08-21"), "option_type": "put",
       "mid": 4.50, "bid": 4.48, "ask": 4.52, "open_interest": 500,
       "delta": 0.3, "iv": 0.2, "dte": 28},
      {"strike": 675.0, "expiry": pd.Timestamp("2026-08-21"), "option_type": "put",
       "mid": 0.40, "bid": 0.38, "ask": 0.42, "open_interest": 500,
       "delta": 0.2, "iv": 0.2, "dte": 28},
    ])
  def get_vix(self, as_of):
    return 18.0


def _open(db):
  return insert_trade(dict(
    date_entered="2026-07-20", ticker="SPY", direction="put_spread",
    strike_short=680.0, strike_long=675.0, expiry="2026-08-21", dte=28,
    entry_credit=2.0, spread_width=5.0, contracts=1, max_loss=300.0,
    max_profit=200.0, pop_predicted=0.7, pop_raw=0.7, exp_return=0.2,
    regime="normal|sideways|fair",
  ), db)


def test_audit_only_stops_and_summarizes(tmp_path, capsys):
  db = tmp_path / "j.db"
  init_db(db)
  tid = _open(db)
  before = sqlite3.connect(db).execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0]

  closed = run_audit_only(_MarkAdapter(), date(2026, 7, 24), db_path=db,
                          store_path=tmp_path / "store.parquet")

  after = sqlite3.connect(db).execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0]
  assert after == before                    # no new trades placed
  assert len(closed) == 1                    # the open position was managed
  conn = sqlite3.connect(db); conn.row_factory = sqlite3.Row
  assert dict(conn.execute("SELECT * FROM paper_trades WHERE trade_id=?",
                           (tid,)).fetchone())["status"] == "stopped"
  out = capsys.readouterr().out
  assert "PORTFOLIO SUMMARY" in out
