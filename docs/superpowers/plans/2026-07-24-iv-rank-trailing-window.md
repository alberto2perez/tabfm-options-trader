# IV-Rank Trailing Window Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compute IV rank from a fixed 252-trading-day trailing VIX series (via the adapter) instead of the accumulating history store, so the IV-rank entry gate reflects real volatility percentile.

**Architecture:** Adapters gain `get_vix_series(as_of, days=252) -> list[float]`. `compute_iv_rank` is reworked to take that series. `run_nightly` wires it in; the snapshot carries a `vix_series` from yfinance `^VIX`.

**Tech Stack:** Python 3.14, pandas, numpy, pytest. No new dependencies.

## Global Constraints

- Paper trading only; no order-placement APIs.
- `compute_iv_rank` signature CHANGES to `(current_vix: float, vix_series: list[float]) -> float`; returns `50.0` when the cleaned series has `< 30` points; else `round(below/len*100, 2)`; skips `None` values.
- `get_vix_series` returns floats oldest→newest, at most `days`, on/before `as_of`. Base default `[]`.
- The store's `vix_level` column and `load_store` usage elsewhere in history_store stay; only `compute_iv_rank`'s body/signature changes.
- Gate threshold `TABFM_MIN_IV_RANK=30` unchanged; no new env vars.
- 2-space indent; no Co-Authored-By / Claude / Anthropic commit trailers.
- Suite baseline: 161 tests. Run: `PYTHONPATH=. python3 -m pytest tabfm/trading/tests/ -q --ignore=tabfm/trading/tests/test_hist_adapter.py --ignore=tabfm/trading/tests/test_live_adapter.py --ignore=tabfm/trading/tests/test_run_nightly.py`
- Paths relative to `/Users/alberto2perez/src/tabfm-options-trader`.

---

### Task 1: get_vix_series adapters + compute_iv_rank rework

**Files:**
- Modify: `tabfm/trading/adapters/base.py` (add `get_vix_series`)
- Modify: `tabfm/trading/adapters/historical.py` (add `get_vix_series`)
- Modify: `tabfm/trading/adapters/snapshot.py` (add `get_vix_series`)
- Modify: `tabfm/trading/store/history_store.py` (`compute_iv_rank` rework)
- Modify: `tabfm/trading/tests/test_store.py` (update the two callers)
- Test: `tabfm/trading/tests/test_iv_rank.py`, `tabfm/trading/tests/test_vix_series.py`

**Interfaces:**
- Produces: `DataAdapter.get_vix_series(as_of, days=252) -> list[float]` (default `[]`); `HistAdapter.get_vix_series`; `SnapshotAdapter.get_vix_series`; `compute_iv_rank(current_vix, vix_series) -> float`.

- [ ] **Step 1: Write the failing tests**

```python
# tabfm/trading/tests/test_iv_rank.py
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
```

```python
# tabfm/trading/tests/test_vix_series.py
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
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=. python3 -m pytest tabfm/trading/tests/test_iv_rank.py tabfm/trading/tests/test_vix_series.py -q`
Expected: failures (`get_vix_series` missing; `compute_iv_rank` still store-based).

- [ ] **Step 3: Implement**

`tabfm/trading/store/history_store.py` — replace the whole `compute_iv_rank`
function with:

```python
def compute_iv_rank(current_vix: float, vix_series: list) -> float:
  """IV rank = percentile of current VIX within a trailing VIX series.

  Neutral 50 when the series has < 30 points (cold-start friendly — fresh
  systems still trade)."""
  clean = [float(v) for v in vix_series if v is not None]
  if len(clean) < 30:
    return 50.0
  below = sum(1 for v in clean if v < current_vix)
  return round(below / len(clean) * 100, 2)
```

`tabfm/trading/adapters/base.py` — add to `class DataAdapter`:

```python
  def get_vix_series(self, as_of: date, days: int = 252) -> list:
    """Trailing daily VIX closes (floats, oldest→newest) on/before as_of."""
    return []
```

`tabfm/trading/adapters/historical.py` — add to `class HistAdapter`:

```python
  def get_vix_series(self, as_of: date, days: int = 252) -> list:
    self._assert_no_lookahead(as_of)
    df = self._history("^VIX", lookback=400)
    df = df[df.index <= pd.Timestamp(as_of)]
    return [float(v) for v in df["Close"].tail(days)]
```

`tabfm/trading/adapters/snapshot.py` — add to `class SnapshotAdapter`:

```python
  def get_vix_series(self, as_of: date, days: int = 252) -> list:
    series = self._s.get("vix_series") or []
    return [float(v) for v in series][-days:]
```

`tabfm/trading/tests/test_store.py` — update the two callers to the new
signature (they no longer take a store path):

```python
def test_compute_iv_rank_no_history(tmp_parquet):
  rank = compute_iv_rank(20.0, [15.0] * 10)   # < 30 points
  assert rank == 50.0  # neutral default when insufficient history


def test_compute_iv_rank_with_history(tmp_parquet):
  series = [float(10 + i) for i in range(40)]  # 10..49, ≥ 30 points
  rank = compute_iv_rank(15.0, series)          # 15 → 5 values below → 12.5
  assert 0.0 <= rank <= 100.0
```

(Leave the `tmp_parquet` fixture arg for signature compatibility even though
unused; or drop it — implementer's choice, keep the suite green.)

- [ ] **Step 4: Run the new tests + full suite.** Expected: green (161 baseline − 0 + new).

- [ ] **Step 5: Commit**

```bash
git add tabfm/trading/adapters/base.py tabfm/trading/adapters/historical.py tabfm/trading/adapters/snapshot.py tabfm/trading/store/history_store.py tabfm/trading/tests/test_store.py tabfm/trading/tests/test_iv_rank.py tabfm/trading/tests/test_vix_series.py
git commit -m "feat(trading): IV rank from trailing 252-day VIX series via adapter"
```

---

### Task 2: Wire into run_nightly + snapshot schema + docs

**Files:**
- Modify: `tabfm/trading/run_nightly.py`
- Modify: `docs/NIGHTLY_CLOUD_RUN.md`
- Modify: `tabfm/trading/adapters/snapshot.py` docstring (schema note)
- Test: `tabfm/trading/tests/test_iv_rank_integration.py`

**Interfaces:**
- Consumes: `adapter.get_vix_series` + `compute_iv_rank` (Task 1).
- Produces: `run_nightly` computes iv_rank from the adapter's VIX series; snapshot documents `vix_series`.

- [ ] **Step 1: Write the failing integration test**

```python
# tabfm/trading/tests/test_iv_rank_integration.py
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
  # VIX 16 sits below the top of the 12–25 range → a real mid rank, gate-passing
  assert iv_rank >= 30.0
```

- [ ] **Step 2: Verify it fails** (only if the wiring/plumbing is absent; the
  assertion should pass once Task 1 exists — so this test mainly guards the
  end-to-end path. If it already passes after Task 1, that is acceptable;
  proceed to wire run_nightly and keep it green.)

- [ ] **Step 3: Implement the wiring**

In `tabfm/trading/run_nightly.py`, replace:

```python
    iv_rank = compute_iv_rank(vix_now, store_path)
```

with (hoist out of the per-chain loop since it is market-wide — compute once
before the loop and reuse):

```python
    iv_rank = compute_iv_rank(vix_now, adapter.get_vix_series(as_of))
```

(Minimal change: leave it inside the loop if hoisting complicates the diff;
it returns the same value each iteration. Prefer hoisting to a single call
above the `for chain_data in chain_data_list:` loop and referencing the
variable inside — implementer's choice, keep behavior identical.)

`tabfm/trading/adapters/snapshot.py` — extend the module/class docstring's
schema list with: `"vix_series": [float, ...]  # ~252 trailing daily VIX
closes on/before as_of (from yfinance ^VIX)`.

`docs/NIGHTLY_CLOUD_RUN.md` — in the snapshot-build step, add a bullet: the
snapshot must include `vix_series` — ~252 trailing daily `^VIX` closes
on/before the run date, fetched from yfinance (`yfinance.download("^VIX",
...)`), NOT from a VIXY proxy. It feeds the IV-rank entry gate; without it
iv_rank falls back to a neutral 50.

- [ ] **Step 4: Run the integration test + full suite.** Green expected.

- [ ] **Step 5: Commit**

```bash
git add tabfm/trading/run_nightly.py tabfm/trading/adapters/snapshot.py docs/NIGHTLY_CLOUD_RUN.md tabfm/trading/tests/test_iv_rank_integration.py
git commit -m "feat(trading): wire trailing-window IV rank into nightly + snapshot vix_series"
```

---

## Self-Review

- **Spec coverage:** get_vix_series on base/hist/snapshot (Task 1) ✓;
  compute_iv_rank rework + <30 neutral + percentile (Task 1) ✓; test_store
  callers updated (Task 1) ✓; run_nightly wiring (Task 2) ✓; snapshot schema
  + docs (Task 2) ✓; no new env vars ✓.
- **Placeholders:** none; every step has complete code.
- **Type consistency:** `get_vix_series(as_of, days=252) -> list[float]`
  identical across base/hist/snapshot and the run_nightly call;
  `compute_iv_rank(current_vix, vix_series)` matches every caller (run_nightly,
  test_store, test_iv_rank, integration).
- **No-network:** unit tests use SnapshotAdapter / pure compute_iv_rank;
  HistAdapter.get_vix_series (yfinance) is exercised only by the live backtest,
  consistent with the existing test_hist_adapter exclusion.
