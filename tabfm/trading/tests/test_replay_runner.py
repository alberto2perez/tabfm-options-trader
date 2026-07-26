from datetime import date

import pandas as pd

import tabfm.trading.backtest.replay_runner as rr


def _seed(tmp_path):
  rows = [
    {"date": "2026-03-18", "ticker": "SPY", "strike": 700.0, "expiry": "2026-04-17",
     "option_type": "put", "bid": 2.0, "ask": 2.1, "mid": 2.05, "delta": 0.30,
     "iv": 0.24, "open_interest": 500, "dte": 30},
    {"date": "2026-03-19", "ticker": "SPY", "strike": 700.0, "expiry": "2026-04-17",
     "option_type": "put", "bid": 2.0, "ask": 2.1, "mid": 2.05, "delta": 0.30,
     "iv": 0.24, "open_interest": 500, "dte": 29},
  ]
  p = tmp_path / "cache.parquet"
  pd.DataFrame(rows).to_parquet(p, index=False)
  return p


def test_run_replay_iterates_cache_dates_isolated(tmp_path, monkeypatch):
  cache = _seed(tmp_path)
  calls = []

  def _fake_run(adapter, clf, reg, as_of, db_path, store_path):
    calls.append((as_of, db_path))
    return None

  monkeypatch.setattr(rr, "run", _fake_run)
  monkeypatch.setattr(rr, "report", lambda db_path, verbose=True: {"total_trades": 0})
  monkeypatch.setattr(rr, "turning_point_report", lambda *a, **k: {"n_flips": 0})

  out = rr.run_replay(cache, clf_model=object(), reg_model=object())

  # Iterated the cache's trading-day range (2026-03-18, 03-19)
  assert [c[0] for c in calls] == [date(2026, 3, 18), date(2026, 3, 19)]
  # Isolated: db_path is NOT under the repo's data/ dir
  assert "data/journal.db" not in str(calls[0][1])
  assert out == {"total_trades": 0}
