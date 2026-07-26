from datetime import date
from pathlib import Path

import pandas as pd

from tabfm.trading.run_nightly import run_audit_only
from tabfm.trading.store.journal import init_db, insert_trade


class _DowntrendAdapter:
  def get_underlying(self, ticker, as_of):
    return {"close": 690.0, "sma20": 700.0, "sma50": 710.0}   # downtrend
  def get_options_chain(self, ticker, as_of):
    return pd.DataFrame([
      {"strike": 680.0, "expiry": pd.Timestamp("2026-08-21"), "option_type": "put",
       "mid": 2.8, "bid": 2.78, "ask": 2.82, "open_interest": 500,
       "delta": 0.3, "iv": 0.2, "dte": 28},
      {"strike": 675.0, "expiry": pd.Timestamp("2026-08-21"), "option_type": "put",
       "mid": 0.2, "bid": 0.18, "ask": 0.22, "open_interest": 500,
       "delta": 0.2, "iv": 0.2, "dte": 28},
    ])
  def get_vix(self, as_of):
    return 18.0


def _open_put(db):
  return insert_trade(dict(
    date_entered="2026-07-20", ticker="SPY", direction="put_spread",
    strike_short=680.0, strike_long=675.0, expiry="2026-08-21", dte=28,
    entry_credit=2.0, spread_width=5.0, contracts=1, max_loss=300.0,
    max_profit=200.0, pop_predicted=0.7, pop_raw=0.7, exp_return=0.2,
    regime="normal|downtrend|fair",
  ), db)


def test_midday_audit_emits_trend_alert(tmp_path, capsys):
  db = tmp_path / "j.db"
  init_db(db)
  _open_put(db)
  # mark 2.8-0.2=2.6 → unrealized -60 (losing), downtrend adverse → alert.
  # Set the stop high enough that the auditor does NOT close it first
  # (2.6 < 2x credit=4.0), so it survives to the trend-guard step.
  run_audit_only(_DowntrendAdapter(), date(2026, 7, 24), db_path=db,
                 store_path=tmp_path / "s.parquet")
  out = capsys.readouterr().out
  assert "[TrendGuard]" in out
  assert "CONSIDER CLOSING" in out or "CLOSE NOW" in out
  md = (tmp_path / "RECOMMENDATIONS.md").read_text()
  assert "TREND ALERT" in md
