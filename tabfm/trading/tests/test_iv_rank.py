from tabfm.trading.store.history_store import compute_iv_rank


def test_neutral_below_30_points():
  assert compute_iv_rank(20.0, [15.0] * 10) == 50.0
  assert compute_iv_rank(20.0, []) == 50.0


def test_percentile_math():
  series = [float(i) for i in range(100)]   # 0..99
  # current 40 → 40 values strictly below → 40.0
  assert compute_iv_rank(40.0, series) == 40.0


def test_skips_none_values():
  series = [10.0, None, 20.0, None] + [12.0] * 30
  r = compute_iv_rank(15.0, series)
  assert 0.0 <= r <= 100.0


def test_mid_range_vix_unblocks_gate():
  # VIX 16 against a year spanning 12–25 → well above the 30 gate floor
  import random
  rng = random.Random(1)
  series = [rng.uniform(12.0, 25.0) for _ in range(252)]
  assert compute_iv_rank(16.0, series) >= 30.0
