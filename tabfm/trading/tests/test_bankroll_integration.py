from datetime import date

from tabfm.trading.pipeline.bankroll import get_bankroll
from tabfm.trading.pipeline.portfolio import portfolio_summary
from tabfm.trading.store.journal import init_db, insert_trade, close_trade
from tabfm.trading.pipeline.trade_recommender import select_trade


def _trade():
  return dict(
    date_entered="2026-07-01", ticker="SPY", direction="put_spread",
    strike_short=700.0, strike_long=695.0, expiry="2026-07-18", dte=17,
    entry_credit=2.0, spread_width=5.0, contracts=1, max_loss=300.0,
    max_profit=200.0, pop_predicted=0.6, pop_raw=0.6, exp_return=0.2,
    regime="normal|sideways|fair",
  )


def test_summary_contains_bankroll_block(tmp_path):
  db = tmp_path / "j.db"
  init_db(db)
  tid = insert_trade(_trade(), db)
  close_trade(tid, "lost", -300.0, "2026-07-20", db)
  out = portfolio_summary(db, as_of=date(2026, 7, 24))
  assert "BANKROLL" in out
  assert "1,700" in out          # equity after the loss
  assert "NORMAL" in out         # 15% drawdown < 25% brake
  assert "Slice $255.00" in out  # 1700 * 0.15


def test_summary_shows_recovery_mode(tmp_path):
  db = tmp_path / "j.db"
  init_db(db)
  for pnl, day in [(200.0, "10"), (200.0, "11"), (-300.0, "12"),
                   (-300.0, "13"), (-100.0, "14")]:
    tid = insert_trade(_trade(), db)
    close_trade(tid, "won" if pnl > 0 else "lost", pnl, f"2026-07-{day}", db)
  out = portfolio_summary(db, as_of=date(2026, 7, 24))
  assert "RECOVERY" in out
  assert get_bankroll(db).recovery_mode is True


def test_sizing_shrinks_after_losses(tmp_path):
  candidate = {
    "ticker": "SPY", "direction": "put_spread",
    "spread_width_dollars": 5.0, "entry_credit": 2.25,
    "strike_short": 480.0, "strike_long": 475.0, "expiry": "2026-08-21",
    "bid_ask_pct": 0.10, "open_interest": 200, "dte": 30, "short_delta": 0.25,  # above the manage_dte+7 entry floor
    "earnings_flag": "no_earnings", "pop_predicted": 0.72, "exp_return": 0.20,
  }

  db = tmp_path / "j.db"
  init_db(db)
  # Fresh journal: $2k equity -> $300 slice -> 1 contract ($275 loss each)
  best = select_trade([dict(candidate)], bankroll=get_bankroll(db))
  assert best["contracts"] == 1

  # Win big: equity 6000 -> slice 900 -> 3 contracts
  tid = insert_trade(_trade(), db)
  close_trade(tid, "won", 4000.0, "2026-07-20", db)
  best = select_trade([dict(candidate)], bankroll=get_bankroll(db))
  assert best["contracts"] == 3

  # Crash below the brake: equity 2000, peak 6000 -> 66% drawdown -> recovery
  tid = insert_trade(_trade(), db)
  close_trade(tid, "lost", -4000.0, "2026-07-21", db)
  bk = get_bankroll(db)
  assert bk.recovery_mode is True
  # slice 2000 * 0.075 = 150 < 275 -> no trade fits
  assert select_trade([dict(candidate)], bankroll=bk) is None
