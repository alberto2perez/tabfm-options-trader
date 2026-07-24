from pathlib import Path

import pytest

from tabfm.trading.pipeline.bankroll import Bankroll, get_bankroll, default_bankroll
from tabfm.trading.store.journal import init_db, insert_trade, close_trade


def _trade(pop=0.6):
  return dict(
    date_entered="2026-07-01", ticker="SPY", direction="put_spread",
    strike_short=700.0, strike_long=695.0, expiry="2026-07-18", dte=17,
    entry_credit=2.0, spread_width=5.0, contracts=1, max_loss=300.0,
    max_profit=200.0, pop_predicted=pop, pop_raw=pop, exp_return=0.2,
    regime="normal|sideways|fair",
  )


def _seed(db, pnls):
  init_db(db)
  for i, pnl in enumerate(pnls):
    tid = insert_trade(_trade(), db)
    close_trade(tid, "won" if pnl > 0 else "lost", pnl, f"2026-07-{10 + i:02d}", db)


def test_empty_journal_defaults(tmp_path):
  db = tmp_path / "j.db"
  init_db(db)
  bk = get_bankroll(db)
  assert bk.starting == 2000.0
  assert bk.realized == 0.0
  assert bk.equity == 2000.0
  assert bk.peak_equity == 2000.0
  assert bk.drawdown_pct == 0.0
  assert bk.recovery_mode is False
  assert bk.slice_limit == pytest.approx(360.0)
  assert bk.exposure_limit == pytest.approx(900.0)


def test_wins_raise_equity_and_peak(tmp_path):
  db = tmp_path / "j.db"
  _seed(db, [200.0, 200.0])
  bk = get_bankroll(db)
  assert bk.equity == 2400.0
  assert bk.peak_equity == 2400.0
  assert bk.slice_limit == pytest.approx(432.0)


def test_losses_shrink_slice(tmp_path):
  db = tmp_path / "j.db"
  _seed(db, [-300.0])
  bk = get_bankroll(db)
  assert bk.equity == 1700.0
  assert bk.peak_equity == 2000.0
  assert bk.drawdown_pct == pytest.approx(0.15)
  assert bk.recovery_mode is False
  assert bk.slice_limit == pytest.approx(306.0)


def test_drawdown_over_brake_triggers_recovery(tmp_path):
  db = tmp_path / "j.db"
  # Peak 2400 after wins, then losses to 1700: drawdown 700/2400 = 29.2% > 25%
  _seed(db, [200.0, 200.0, -300.0, -300.0, -100.0])
  bk = get_bankroll(db)
  assert bk.equity == 1700.0
  assert bk.peak_equity == 2400.0
  assert bk.drawdown_pct == pytest.approx(700 / 2400, abs=1e-4)
  assert bk.recovery_mode is True
  # Recovery halves the fraction: 1700 * 0.09
  assert bk.slice_limit == pytest.approx(153.0)


def test_recovery_exits_on_new_high(tmp_path):
  db = tmp_path / "j.db"
  # Deep drawdown then a run back above the old peak
  _seed(db, [200.0, 200.0, -300.0, -300.0, -100.0, 400.0, 400.0])
  bk = get_bankroll(db)
  assert bk.equity == 2500.0
  assert bk.peak_equity == 2500.0
  assert bk.recovery_mode is False
  assert bk.slice_limit == pytest.approx(450.0)


def test_equity_floor_zeroes_limits(tmp_path):
  db = tmp_path / "j.db"
  _seed(db, [-1500.0, -800.0])
  bk = get_bankroll(db)
  assert bk.equity == 0.0
  assert bk.slice_limit == 0.0
  assert bk.exposure_limit == 0.0


def test_env_overrides(tmp_path, monkeypatch):
  monkeypatch.setenv("TABFM_STARTING_CAPITAL", "5000")
  monkeypatch.setenv("TABFM_RISK_PER_TRADE", "0.10")
  monkeypatch.setenv("TABFM_MAX_EXPOSURE", "0.30")
  db = tmp_path / "j.db"
  init_db(db)
  bk = get_bankroll(db)
  assert bk.starting == 5000.0
  assert bk.slice_limit == pytest.approx(500.0)
  assert bk.exposure_limit == pytest.approx(1500.0)


def test_default_bankroll_matches_empty_journal():
  bk = default_bankroll()
  assert bk.equity == 2000.0
  assert bk.slice_limit == pytest.approx(360.0)
  assert bk.recovery_mode is False


def test_closed_trades_ordered_by_close_date(tmp_path):
  db = tmp_path / "j.db"
  init_db(db)
  # Insert out of order; walk must follow date_closed order for correct peak.
  t1 = insert_trade(_trade(), db)
  t2 = insert_trade(_trade(), db)
  close_trade(t2, "lost", -300.0, "2026-07-20", db)  # later
  close_trade(t1, "won", 400.0, "2026-07-11", db)    # earlier
  bk = get_bankroll(db)
  # Walk: 2000 -> 2400 (07-11) -> 2100 (07-20); peak 2400
  assert bk.peak_equity == 2400.0
  assert bk.equity == 2100.0


def test_brake_env_override(tmp_path, monkeypatch):
  monkeypatch.setenv("TABFM_DRAWDOWN_BRAKE", "0.50")
  db = tmp_path / "j.db"
  _seed(db, [200.0, 200.0, -300.0, -300.0, -100.0])  # 29.2% drawdown
  bk = get_bankroll(db)
  assert bk.recovery_mode is False  # 29.2% < overridden 50% brake
  assert bk.slice_limit == pytest.approx(306.0)  # full 18% of 1700
