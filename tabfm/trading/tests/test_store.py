import tempfile
from pathlib import Path
import pandas as pd
import pytest
from tabfm.trading.store.journal import (
  init_db, insert_trade, get_open_trades, close_trade, get_all_closed_trades,
)
from tabfm.trading.store.history_store import (
  append_rows, load_store, get_regime_rows, compute_iv_rank,
)

SAMPLE_TRADE = {
  "date_entered": "2025-01-10",
  "ticker": "SPY",
  "direction": "put_spread",
  "strike_short": 480.0,
  "strike_long": 475.0,
  "expiry": "2025-01-17",
  "dte": 7,
  "entry_credit": 1.20,
  "spread_width": 5.0,
  "contracts": 1,
  "max_loss": 380.0,
  "max_profit": 120.0,
  "pop_predicted": 0.72,
  "exp_return": 0.18,
  "regime": "normal|uptrend|fair",
}


@pytest.fixture
def tmp_db(tmp_path):
  db = tmp_path / "test.db"
  init_db(db)
  return db


@pytest.fixture
def tmp_parquet(tmp_path):
  return tmp_path / "test.parquet"


def test_insert_and_get_open(tmp_db):
  trade_id = insert_trade(SAMPLE_TRADE, tmp_db)
  assert trade_id == 1
  open_trades = get_open_trades(tmp_db)
  assert len(open_trades) == 1
  assert open_trades[0]["ticker"] == "SPY"
  assert open_trades[0]["status"] == "open"


def test_close_trade(tmp_db):
  trade_id = insert_trade(SAMPLE_TRADE, tmp_db)
  close_trade(trade_id, "won", 120.0, "2025-01-17", tmp_db)
  assert get_open_trades(tmp_db) == []
  closed = get_all_closed_trades(tmp_db)
  assert len(closed) == 1
  assert closed[0]["status"] == "won"
  assert closed[0]["actual_pnl"] == 120.0


def test_append_and_load_store(tmp_parquet):
  rows = [{"date": "2025-01-10", "vix_bucket": "normal", "trend_direction": "uptrend",
            "iv_regime": "fair", "iv_rank": 45.0, "profitable": 1, "return_pct": 0.3}]
  append_rows(rows, tmp_parquet)
  df = load_store(tmp_parquet)
  assert len(df) == 1
  assert df["vix_bucket"].iloc[0] == "normal"


def test_append_twice_accumulates(tmp_parquet):
  rows = [{"date": "2025-01-10", "vix_bucket": "normal", "trend_direction": "uptrend",
            "iv_regime": "fair", "iv_rank": 45.0, "profitable": 1, "return_pct": 0.3}]
  append_rows(rows, tmp_parquet)
  append_rows(rows, tmp_parquet)
  assert len(load_store(tmp_parquet)) == 2


def test_get_regime_rows_exact_match(tmp_parquet):
  # 9 rows matching exact regime, 1 row with different regime
  rows = [
    {"date": "2025-01-0%d" % i, "vix_bucket": "normal", "trend_direction": "uptrend",
     "iv_regime": "fair", "iv_rank": 45.0, "profitable": 1, "return_pct": 0.3}
    for i in range(1, 10)
  ]
  rows += [{"date": "2025-01-10", "vix_bucket": "spike", "trend_direction": "downtrend",
             "iv_regime": "expensive", "iv_rank": 90.0, "profitable": 0, "return_pct": -1.0}]
  append_rows(rows, tmp_parquet)
  # n=9: exact match satisfies the threshold → returns only the 9 matching rows
  result = get_regime_rows("normal", "uptrend", "fair", "2026-01-01", n=9, path=tmp_parquet)
  assert len(result) == 9
  assert all(result["vix_bucket"] == "normal")
  # n=60: exact match (9 rows) < 60, falls through all levels → returns all 10 rows
  result_fallback = get_regime_rows("normal", "uptrend", "fair", "2026-01-01", n=60, path=tmp_parquet)
  assert len(result_fallback) == 10


def test_get_regime_rows_fallback(tmp_parquet):
  rows = [{"date": "2025-01-0%d" % i, "vix_bucket": "low", "trend_direction": "uptrend",
            "iv_regime": "fair", "iv_rank": 10.0, "profitable": 1, "return_pct": 0.2}
          for i in range(1, 5)]
  append_rows(rows, tmp_parquet)
  # ask for "spike" which doesn't exist — should fall back to all rows
  result = get_regime_rows("spike", "downtrend", "expensive", "2026-01-01", n=60, path=tmp_parquet)
  assert len(result) == 4


def test_compute_iv_rank_no_history(tmp_parquet):
  rank = compute_iv_rank(20.0, tmp_parquet)
  assert rank == 50.0  # neutral default when no history


def test_compute_iv_rank_with_history(tmp_parquet):
  rows = [{"date": "2025-01-0%d" % i, "vix_level": float(10 + i),
            "vix_bucket": "normal", "trend_direction": "uptrend",
            "iv_regime": "fair", "iv_rank": 50.0, "profitable": 1, "return_pct": 0.2}
          for i in range(1, 10)]
  append_rows(rows, tmp_parquet)
  # vix_levels are 11,12,...,19. current_vix=15 → ~44th percentile
  rank = compute_iv_rank(15.0, tmp_parquet)
  assert 0.0 <= rank <= 100.0
