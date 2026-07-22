from pathlib import Path
from tabfm.trading.pipeline.accuracy_tracker import report
from tabfm.trading.store.journal import init_db, insert_trade, close_trade

_BASE_TRADE = {
  "date_entered": "2025-01-01", "ticker": "SPY", "direction": "put_spread",
  "strike_short": 480.0, "strike_long": 475.0, "expiry": "2025-01-17",
  "dte": 7, "entry_credit": 1.20, "spread_width": 5.0, "contracts": 1,
  "max_loss": 380.0, "max_profit": 120.0, "pop_predicted": 0.70,
  "exp_return": 0.20, "regime": "normal|uptrend|fair",
}


def _setup_db(tmp_path: Path, records: list[tuple]) -> Path:
  db = tmp_path / "test.db"
  init_db(db)
  for i, (status, pnl, pop) in enumerate(records):
    t = {**_BASE_TRADE, "date_entered": f"2025-01-{i+1:02d}", "pop_predicted": pop}
    tid = insert_trade(t, db)
    close_trade(tid, status, pnl, "2025-01-17", db)
  return db


def test_report_no_trades(tmp_path):
  db = tmp_path / "empty.db"
  init_db(db)
  result = report(db_path=db, verbose=False)
  assert result == {}


def test_report_win_rate(tmp_path):
  db = _setup_db(tmp_path, [("won", 120.0, 0.72), ("won", 120.0, 0.68), ("lost", -380.0, 0.65)])
  metrics = report(db_path=db, verbose=False)
  assert metrics["total_trades"] == 3
  assert metrics["wins"] == 2
  assert abs(metrics["win_rate"] - 2/3) < 0.01


def test_report_cumulative_pnl(tmp_path):
  db = _setup_db(tmp_path, [("won", 120.0, 0.70), ("lost", -380.0, 0.70)])
  metrics = report(db_path=db, verbose=False)
  assert abs(metrics["cumulative_pnl"] - (120.0 - 380.0)) < 0.01


def test_report_max_drawdown(tmp_path):
  db = _setup_db(tmp_path, [
    ("won", 100.0, 0.70), ("lost", -300.0, 0.70), ("lost", -300.0, 0.70),
  ])
  metrics = report(db_path=db, verbose=False)
  assert metrics["max_drawdown"] > 0


def test_report_pop_calibration(tmp_path):
  # 2 wins (predicted 0.70 avg), actual win_rate = 1.0 → error = 0.30
  db = _setup_db(tmp_path, [("won", 120.0, 0.70), ("won", 120.0, 0.70)])
  metrics = report(db_path=db, verbose=False)
  assert abs(metrics["pop_calibration_error"] - 0.30) < 0.01
