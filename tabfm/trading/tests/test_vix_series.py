import json
from datetime import date

from tabfm.trading.adapters.base import DataAdapter
from tabfm.trading.adapters.snapshot import SnapshotAdapter


class _Bare(DataAdapter):
  def get_underlying(self, ticker, as_of): return {}
  def get_options_chain(self, ticker, as_of): return None
  def get_vix(self, as_of): return 20.0


def test_base_default_empty():
  assert _Bare().get_vix_series(date(2026, 7, 24)) == []


def _snap(tmp_path, extra):
  base = {"as_of": "2026-07-24", "vix": 18.0, "tickers": {}, "closes": {}}
  base.update(extra)
  p = tmp_path / "s.json"
  p.write_text(json.dumps(base))
  return SnapshotAdapter(p)


def test_snapshot_passthrough(tmp_path):
  a = _snap(tmp_path, {"vix_series": [12.0, 13.0, 14.0]})
  assert a.get_vix_series(date(2026, 7, 24)) == [12.0, 13.0, 14.0]


def test_snapshot_absent_returns_empty(tmp_path):
  a = _snap(tmp_path, {})
  assert a.get_vix_series(date(2026, 7, 24)) == []
