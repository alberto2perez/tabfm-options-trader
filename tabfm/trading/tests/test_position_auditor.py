from datetime import date
from unittest.mock import MagicMock

from tabfm.trading.pipeline.position_auditor import _is_winner, _estimate_current_value, audit_positions
from tabfm.trading.store.journal import init_db, insert_trade, get_all_closed_trades


def test_put_spread_winner_when_above_short_strike():
  trade = {"direction": "put_spread", "strike_short": 480.0, "spread_width": 5.0}
  assert _is_winner(trade, underlying_price=485.0) is True
  assert _is_winner(trade, underlying_price=478.0) is False


def test_call_spread_winner_when_below_short_strike():
  trade = {"direction": "call_spread", "strike_short": 500.0, "spread_width": 5.0}
  assert _is_winner(trade, underlying_price=495.0) is True
  assert _is_winner(trade, underlying_price=505.0) is False


def test_estimate_current_value_put_spread_itm():
  trade = {"direction": "put_spread", "strike_short": 480.0, "spread_width": 5.0}
  val = _estimate_current_value(trade, underlying_price=477.0)
  assert val == 3.0  # intrinsic = 480 - 477 = 3, within spread


def test_estimate_current_value_capped_at_spread():
  trade = {"direction": "put_spread", "strike_short": 480.0, "spread_width": 5.0}
  val = _estimate_current_value(trade, underlying_price=470.0)
  assert val == 5.0  # capped at spread width


def _make_trade(**overrides):
  base = dict(
    date_entered="2025-01-03",
    ticker="SPY", direction="put_spread", strike_short=490.0,
    strike_long=485.0, expiry="2025-01-10", dte=7,
    entry_credit=1.50, spread_width=5.0, contracts=2,
    max_loss=700.0, max_profit=300.0, pop_predicted=0.70,
    exp_return=0.05, regime="normal-uptrend-fair",
  )
  base.update(overrides)
  return base


def test_audit_positions_expire_win(tmp_path):
  db = tmp_path / "j.db"
  init_db(db)
  insert_trade(_make_trade(ticker="SPY", strike_short=490.0), db)

  adapter = MagicMock()
  # SPY at 495 > 490 short strike → put_spread wins (price above short strike)
  adapter.get_underlying.return_value = {"close": 495.0}

  # Run audit on expiry day
  closed = audit_positions(adapter, date(2025, 1, 10), db)
  assert len(closed) == 1
  assert closed[0]["status"] == "won"


def test_audit_positions_expire_loss(tmp_path):
  db = tmp_path / "j.db"
  init_db(db)
  insert_trade(_make_trade(ticker="SPY", strike_short=490.0), db)

  adapter = MagicMock()
  # SPY at 480 < 490 short strike → put_spread loses
  adapter.get_underlying.return_value = {"close": 480.0}

  closed = audit_positions(adapter, date(2025, 1, 10), db)
  assert len(closed) == 1
  assert closed[0]["status"] == "lost"


def test_audit_positions_early_close_at_50pct(tmp_path):
  db = tmp_path / "j.db"
  init_db(db)
  # max_profit=300, so 50% threshold = 150; credit keeps 150 → early close
  trade = _make_trade(ticker="SPY", entry_credit=1.50, max_profit=300.0, expiry="2025-02-01")
  insert_trade(trade, db)

  adapter = MagicMock()
  # Price well above short strike → current spread value ≈ 0 → unrealized ≈ max_profit
  adapter.get_underlying.return_value = {"close": 510.0}

  # Run before expiry
  closed = audit_positions(adapter, date(2025, 1, 15), db)
  assert len(closed) == 1
  assert closed[0]["status"] == "partial"
