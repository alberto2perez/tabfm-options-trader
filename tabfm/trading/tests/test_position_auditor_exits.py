# tabfm/trading/tests/test_position_auditor_exits.py
import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd

from tabfm.trading.pipeline.position_auditor import audit_positions
from tabfm.trading.store.journal import init_db, insert_trade, get_open_trades

AS_OF = date(2026, 7, 24)


class _MarkAdapter:
  """Adapter stub returning a fixed spread mark via its options chain."""

  def __init__(
    self,
    short_mid: float,
    long_mid: float,
    underlying: float = 700.0,
    expiry: str = "2026-08-21",
  ):
    self.short_mid = short_mid
    self.long_mid = long_mid
    self.underlying = underlying
    self.expiry = expiry

  def get_underlying(self, ticker, as_of):
    return {"close": self.underlying}

  def get_options_chain(self, ticker, as_of):
    return pd.DataFrame([
      {"strike": 680.0, "expiry": pd.Timestamp(self.expiry), "option_type": "put",
       "mid": self.short_mid, "bid": self.short_mid - 0.02, "ask": self.short_mid + 0.02,
       "open_interest": 500, "delta": 0.3, "iv": 0.2, "dte": 28},
      {"strike": 675.0, "expiry": pd.Timestamp(self.expiry), "option_type": "put",
       "mid": self.long_mid, "bid": self.long_mid - 0.02, "ask": self.long_mid + 0.02,
       "open_interest": 500, "delta": 0.25, "iv": 0.2, "dte": 28},
    ])

  def get_vix(self, as_of):
    return 18.0


class _NoChainAdapter(_MarkAdapter):
  def __init__(self, underlying: float = 700.0):
    super().__init__(0.0, 0.0, underlying)

  def get_options_chain(self, ticker, as_of):
    return pd.DataFrame()


def _open_trade(db, credit=2.0, expiry="2026-08-21"):
  init_db(db)
  return insert_trade(dict(
    date_entered="2026-07-20", ticker="SPY", direction="put_spread",
    strike_short=680.0, strike_long=675.0, expiry=expiry, dte=28,
    entry_credit=credit, spread_width=5.0, contracts=1, max_loss=300.0,
    max_profit=200.0, pop_predicted=0.7, pop_raw=0.7, exp_return=0.2,
    regime="normal|sideways|fair",
  ), db)


def _status(db, tid):
  conn = sqlite3.connect(db)
  conn.row_factory = sqlite3.Row
  return dict(conn.execute(
    "SELECT * FROM paper_trades WHERE trade_id=?", (tid,)).fetchone())


def test_stop_loss_fires_at_double_credit(tmp_path):
  db = tmp_path / "j.db"
  tid = _open_trade(db, credit=2.0)
  # spread mark = 4.5 - 0.4 = 4.1 >= 2.0 * 2.0 -> stop
  closed = audit_positions(_MarkAdapter(4.5, 0.4), AS_OF, db)
  assert len(closed) == 1
  row = _status(db, tid)
  assert row["status"] == "stopped"
  assert row["actual_pnl"] == -210.0  # (2.0 - 4.1) * 1 * 100


def test_stop_does_not_fire_below_multiple(tmp_path):
  db = tmp_path / "j.db"
  tid = _open_trade(db, credit=2.0)
  # mark = 3.0 - 0.2 = 2.8 < 4.0 -> stays open (and profit target not hit)
  audit_positions(_MarkAdapter(3.0, 0.2), AS_OF, db)
  assert _status(db, tid)["status"] == "open"


def test_stop_never_fires_on_intrinsic_fallback(tmp_path):
  db = tmp_path / "j.db"
  tid = _open_trade(db, credit=2.0)
  # No chain: deep-ITM intrinsic would look like a huge loss, but the rule
  # requires a real mark. Underlying 650 -> intrinsic 5.0 (max loss zone).
  audit_positions(_NoChainAdapter(underlying=650.0), AS_OF, db)
  assert _status(db, tid)["status"] == "open"


def test_dte_management_closes_profitable_as_partial(tmp_path):
  db = tmp_path / "j.db"
  tid = _open_trade(db, credit=2.0, expiry="2026-08-07")  # 14 DTE from AS_OF
  # mark = 1.2 - 0.2 = 1.0 -> unrealized +100 (50% of 200 -> profit target
  # would also fire; set mark so it's below target): 1.6 - 0.3 = 1.3 -> +70
  closed = audit_positions(_MarkAdapter(1.6, 0.3, expiry="2026-08-07"), AS_OF, db)
  row = _status(db, tid)
  assert row["status"] == "partial"
  assert row["actual_pnl"] == 70.0


def test_dte_management_closes_loser_as_stopped(tmp_path):
  db = tmp_path / "j.db"
  tid = _open_trade(db, credit=2.0, expiry="2026-08-07")
  # mark = 2.8 - 0.3 = 2.5 -> unrealized -50, below stop multiple, 14 DTE
  audit_positions(_MarkAdapter(2.8, 0.3, expiry="2026-08-07"), AS_OF, db)
  row = _status(db, tid)
  assert row["status"] == "stopped"
  assert row["actual_pnl"] == -50.0


def test_dte_rule_respects_env_override(tmp_path, monkeypatch):
  monkeypatch.setenv("TABFM_MANAGE_DTE", "5")
  db = tmp_path / "j.db"
  tid = _open_trade(db, credit=2.0, expiry="2026-08-07")  # 14 DTE > 5
  audit_positions(_MarkAdapter(1.6, 0.3, expiry="2026-08-07"), AS_OF, db)
  assert _status(db, tid)["status"] == "open"


def test_excursions_tracked_and_widen_only(tmp_path):
  db = tmp_path / "j.db"
  tid = _open_trade(db, credit=2.0)
  audit_positions(_MarkAdapter(2.4, 0.2), AS_OF, db)   # mark 2.2 -> -20
  row = _status(db, tid)
  assert row["mae"] == -20.0 and row["mfe"] == -20.0
  audit_positions(_MarkAdapter(1.4, 0.2), AS_OF, db)   # mark 1.2 -> +80
  row = _status(db, tid)
  assert row["mfe"] == 80.0 and row["mae"] == -20.0    # mae must not shrink


def test_stopped_counts_as_loss_in_summary(tmp_path):
  from tabfm.trading.pipeline.portfolio import portfolio_summary
  db = tmp_path / "j.db"
  _open_trade(db, credit=2.0)
  audit_positions(_MarkAdapter(4.5, 0.4), AS_OF, db)  # -> stopped
  out = portfolio_summary(db, as_of=AS_OF)
  assert "(0W / 1L)" in out
