import tempfile
from pathlib import Path
from datetime import date
from tabfm.trading.pipeline.paper_executor import execute_paper_trade, format_recommendation
from tabfm.trading.store.journal import init_db, get_open_trades

_TRADE = {
  "ticker": "SPY", "direction": "put_spread",
  "strike_short": 480.0, "strike_long": 475.0,
  "expiry": "2025-01-17", "dte": 7, "entry_credit": 1.20,
  "spread_width_dollars": 5.0, "contracts": 2, "total_risk": 1000.0,
  "pop_predicted": 0.72, "exp_return": 0.18, "iv_rank": 55.0,
  "vix_bucket": "normal", "trend_direction": "uptrend", "iv_regime": "fair",
}
AS_OF = date(2025, 1, 10)


def test_execute_paper_trade_inserts_record(tmp_path):
  db = tmp_path / "test.db"
  init_db(db)
  trade_id = execute_paper_trade(_TRADE, AS_OF, path=db)
  assert trade_id == 1
  open_trades = get_open_trades(db)
  assert len(open_trades) == 1
  assert open_trades[0]["ticker"] == "SPY"


def test_format_recommendation_contains_key_fields():
  output = format_recommendation(_TRADE, 42, AS_OF)
  assert "SPY" in output
  assert "PUT SPREAD" in output
  assert "480" in output
  assert "72.0%" in output
  assert "trade_id: 42" in output
