from datetime import date

from tabfm.trading.store.market_history import record_market_day, load_market_history


def test_record_and_load_roundtrip(tmp_path):
  record_market_day(tmp_path, date(2026, 7, 23), 18.7, 0.21)
  record_market_day(tmp_path, date(2026, 7, 24), 18.81, 0.22)
  hist = load_market_history(tmp_path)
  assert len(hist) == 2
  assert hist[-1] == {"date": "2026-07-24", "vix": 18.81, "median_iv": 0.22}


def test_record_same_day_overwrites(tmp_path):
  record_market_day(tmp_path, date(2026, 7, 24), 18.0, 0.20)
  record_market_day(tmp_path, date(2026, 7, 24), 18.81, 0.22)
  hist = load_market_history(tmp_path)
  assert len(hist) == 1
  assert hist[0]["vix"] == 18.81


def test_none_median_iv_roundtrips(tmp_path):
  record_market_day(tmp_path, date(2026, 7, 24), 18.81, None)
  assert load_market_history(tmp_path)[0]["median_iv"] is None


def test_load_missing_file_returns_empty(tmp_path):
  assert load_market_history(tmp_path) == []


def test_load_respects_n(tmp_path):
  for day in range(1, 15):
    record_market_day(tmp_path, date(2026, 7, day), 15.0 + day, 0.2)
  hist = load_market_history(tmp_path, n=5)
  assert len(hist) == 5
  assert hist[-1]["date"] == "2026-07-14"
