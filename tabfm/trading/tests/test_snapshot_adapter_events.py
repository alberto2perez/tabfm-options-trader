import json
from datetime import date

from tabfm.trading.adapters.snapshot import SnapshotAdapter

_SNAP = {
  "as_of": "2026-07-24",
  "vix": 18.81,
  "tickers": {},
  "closes": {},
  "events": {"earnings": [{"symbol": "GOOGL", "date": "2026-07-28", "when": "amc"}]},
  "vix_history": [["2026-07-22", 18.4], ["2026-07-23", 18.7], ["2026-07-24", 18.81]],
}


def _adapter(tmp_path, snap):
  p = tmp_path / "snap.json"
  p.write_text(json.dumps(snap))
  return SnapshotAdapter(p)


def test_get_events_passthrough(tmp_path):
  a = _adapter(tmp_path, _SNAP)
  assert a.get_events(date(2026, 7, 24))["earnings"][0]["symbol"] == "GOOGL"


def test_get_events_missing_returns_none(tmp_path):
  snap = {k: v for k, v in _SNAP.items() if k != "events"}
  assert _adapter(tmp_path, snap).get_events(date(2026, 7, 24)) is None


def test_get_vix_history_filters_and_tails(tmp_path):
  a = _adapter(tmp_path, _SNAP)
  hist = a.get_vix_history(date(2026, 7, 23), n=2)
  assert hist == [["2026-07-22", 18.4], ["2026-07-23", 18.7]]


def test_get_vix_history_missing_returns_empty(tmp_path):
  snap = {k: v for k, v in _SNAP.items() if k != "vix_history"}
  assert _adapter(tmp_path, snap).get_vix_history(date(2026, 7, 24)) == []
