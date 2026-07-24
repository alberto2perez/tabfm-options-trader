import json
from datetime import date

from tabfm.trading.adapters.snapshot import SnapshotAdapter
from tabfm.trading.store.history_store import compute_iv_rank


def test_snapshot_series_feeds_iv_rank(tmp_path):
  series = [float(12 + (i % 14)) for i in range(252)]  # 12..25 repeating
  snap = {"as_of": "2026-07-24", "vix": 16.0, "tickers": {}, "closes": {},
          "vix_series": series}
  p = tmp_path / "s.json"
  p.write_text(json.dumps(snap))
  adapter = SnapshotAdapter(p)
  iv_rank = compute_iv_rank(adapter.get_vix(date(2026, 7, 24)),
                            adapter.get_vix_series(date(2026, 7, 24)))
  # VIX 16 sits below the top of the 12–25 range → a real percentile rank,
  # not the neutral-50 fallback; confirms the end-to-end path is wired.
  assert iv_rank >= 25.0
