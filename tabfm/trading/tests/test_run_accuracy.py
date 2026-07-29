from pathlib import Path

from tabfm.trading.run_accuracy import main
from tabfm.trading.store.journal import init_db, insert_trade, close_trade

_BASE_TRADE = {
  "date_entered": "2025-01-01", "ticker": "SPY", "direction": "put_spread",
  "strike_short": 480.0, "strike_long": 475.0, "expiry": "2025-01-17",
  "dte": 7, "entry_credit": 1.20, "spread_width": 5.0, "contracts": 1,
  "max_loss": 380.0, "max_profit": 120.0, "pop_predicted": 0.70,
  "exp_return": 0.20, "regime": "normal|uptrend|fair",
}


def test_main_empty_journal(tmp_path: Path):
  db = tmp_path / "empty.db"
  init_db(db)
  metrics = main(["--db", str(db)])
  assert metrics == {}


def test_main_reports_win_rate(tmp_path: Path):
  db = tmp_path / "journal.db"
  init_db(db)
  for i, (status, pnl) in enumerate([("won", 120.0), ("won", 120.0), ("lost", -380.0)]):
    tid = insert_trade({**_BASE_TRADE, "date_entered": f"2025-01-{i+1:02d}"}, db)
    close_trade(tid, status, pnl, "2025-01-17", db)
  metrics = main(["--db", str(db)])
  assert metrics["total_trades"] == 3
  assert metrics["wins"] == 2
  assert abs(metrics["win_rate"] - 2 / 3) < 0.01
