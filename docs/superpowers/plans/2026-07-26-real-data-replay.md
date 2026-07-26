# Real-Data Replay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replay the strategy over real historical SPY option marks (cached parquet) to produce trustworthy closed-trade statistics, using the identical pipeline.

**Architecture:** `_implied_vol` back-solves IV from real mids. `ReplayAdapter` subclasses `HistAdapter` (real SPY/VIX underlying) and overrides `get_options_chain` to serve the real cached chain. `run_replay` drives the identical `run()` loop over the cache's date range in an isolated temp journal/store. The real-marks cache is built separately (in-session via MCP) to `data/replay/spy_real_chains.parquet`.

**Tech Stack:** Python 3.14, pandas, numpy, scipy, pytest. No new dependencies.

## Global Constraints

- Paper trading only.
- Cache schema (produced by the data builder; consumed here): columns `date` (YYYY-MM-DD str), `ticker`, `strike` (float), `expiry` (YYYY-MM-DD str), `option_type` ("put"/"call"), `bid`, `ask`, `mid`, `delta` (abs), `iv`, `open_interest` (int), `dte` (int).
- `ReplayAdapter.get_options_chain(ticker, as_of)` returns rows where `date == as_of` and the expiry is 28–45 DTE from as_of, as a DataFrame with the pipeline's chain columns (expiry as `pd.Timestamp`).
- `run_replay` must use an ISOLATED temp journal/store (never `data/`), like `run_backtest`.
- New tests must not hit the network: `_implied_vol` is pure; `ReplayAdapter.get_options_chain` reads a seeded parquet; the runner test monkeypatches `run`. Inherited `get_underlying`/VIX (network) are HistAdapter's, already excluded from the suite run.
- 2-space indent; no Co-Authored-By / Claude / Anthropic commit trailers.
- Suite baseline: 180 tests. Run: `PYTHONPATH=. python3 -m pytest tabfm/trading/tests/ -q --ignore=tabfm/trading/tests/test_hist_adapter.py --ignore=tabfm/trading/tests/test_live_adapter.py --ignore=tabfm/trading/tests/test_run_nightly.py`
- Paths relative to `/Users/alberto2perez/src/tabfm-options-trader`.

---

### Task 1: implied-vol back-solve + ReplayAdapter

**Files:**
- Modify: `tabfm/trading/adapters/historical.py` (add `_implied_vol`)
- Create: `tabfm/trading/adapters/replay.py`
- Test: `tabfm/trading/tests/test_replay_adapter.py`

**Interfaces:**
- Produces: `_implied_vol(price, S, K, T, opt, lo=0.01, hi=3.0, tol=1e-4) -> float | None`; `ReplayAdapter(cache_path, as_of)` with `get_options_chain` overriding HistAdapter.

- [ ] **Step 1: Write the failing tests**

```python
# tabfm/trading/tests/test_replay_adapter.py
from datetime import date

import pandas as pd

from tabfm.trading.adapters.historical import _implied_vol, _bs_price
from tabfm.trading.adapters.replay import ReplayAdapter


def test_implied_vol_round_trips():
  S, K, T, opt = 740.0, 720.0, 30 / 365.0, "put"
  price = _bs_price(S, K, T, 0.22, opt)
  iv = _implied_vol(price, S, K, T, opt)
  assert iv is not None
  assert abs(iv - 0.22) < 0.01


def test_implied_vol_none_below_intrinsic():
  # A put worth less than intrinsic (K-S) has no positive-vol solution
  S, K, T, opt = 700.0, 740.0, 30 / 365.0, "put"
  assert _implied_vol(1.0, S, K, T, opt) is None


def _seed_cache(tmp_path):
  rows = [
    # as_of 2026-03-02, expiry 2026-04-17 → 46 DTE (OUT of 28-45)
    {"date": "2026-03-02", "ticker": "SPY", "strike": 720.0, "expiry": "2026-04-17",
     "option_type": "put", "bid": 3.0, "ask": 3.1, "mid": 3.05, "delta": 0.30,
     "iv": 0.22, "open_interest": 500, "dte": 46},
    # as_of 2026-03-20, expiry 2026-04-17 → 28 DTE (IN range)
    {"date": "2026-03-20", "ticker": "SPY", "strike": 700.0, "expiry": "2026-04-17",
     "option_type": "put", "bid": 2.0, "ask": 2.1, "mid": 2.05, "delta": 0.28,
     "iv": 0.24, "open_interest": 500, "dte": 28},
    {"date": "2026-03-20", "ticker": "SPY", "strike": 695.0, "expiry": "2026-04-17",
     "option_type": "put", "bid": 1.5, "ask": 1.6, "mid": 1.55, "delta": 0.22,
     "iv": 0.24, "open_interest": 500, "dte": 28},
  ]
  p = tmp_path / "cache.parquet"
  pd.DataFrame(rows).to_parquet(p, index=False)
  return p


def test_chain_filters_to_date_and_dte_window(tmp_path):
  cache = _seed_cache(tmp_path)
  adapter = ReplayAdapter(cache, as_of=date(2026, 4, 17))
  # 2026-03-20 has a 28-DTE expiry → returned
  chain = adapter.get_options_chain("SPY", date(2026, 3, 20))
  assert len(chain) == 2
  assert set(chain["strike"]) == {700.0, 695.0}
  assert str(chain["expiry"].iloc[0].date()) == "2026-04-17"
  # 2026-03-02's only expiry is 46 DTE → filtered out → empty
  assert adapter.get_options_chain("SPY", date(2026, 3, 2)).empty


def test_no_lookahead(tmp_path):
  cache = _seed_cache(tmp_path)
  adapter = ReplayAdapter(cache, as_of=date(2026, 3, 20))
  import pytest
  with pytest.raises(AssertionError):
    adapter.get_options_chain("SPY", date(2026, 4, 1))  # after as_of
```

- [ ] **Step 2: Run to verify failure** (`_implied_vol` / `replay` missing).

- [ ] **Step 3: Implement**

In `tabfm/trading/adapters/historical.py`, add near `_bs_price`:

```python
def _implied_vol(price, S, K, T, opt, lo=0.01, hi=3.0, tol=1e-4):
  """Back-solve implied vol so _bs_price(S,K,T,sigma,opt) == price, by
  bisection. Returns None when price is outside the [lo,hi]-vol range (e.g.
  below intrinsic or an unmatchable quote)."""
  p_lo = _bs_price(S, K, T, lo, opt)
  p_hi = _bs_price(S, K, T, hi, opt)
  if not (p_lo <= price <= p_hi):
    return None
  for _ in range(60):
    mid = (lo + hi) / 2
    p = _bs_price(S, K, T, mid, opt)
    if abs(p - price) < tol:
      return mid
    if p < price:
      lo = mid
    else:
      hi = mid
  return (lo + hi) / 2
```

Create `tabfm/trading/adapters/replay.py`:

```python
"""ReplayAdapter: serves REAL historical option marks from a cached parquet,
with real SPY/VIX underlying inherited from HistAdapter. Lets the identical
pipeline replay over real prices — trustworthy stats without waiting weeks."""
from datetime import date

import pandas as pd

from .historical import HistAdapter


class ReplayAdapter(HistAdapter):
  def __init__(self, cache_path, as_of: date) -> None:
    super().__init__(as_of=as_of)
    self._chains = pd.read_parquet(cache_path)

  def get_options_chain(self, ticker: str, as_of: date) -> pd.DataFrame:
    self._assert_no_lookahead(as_of)
    df = self._chains[
      (self._chains["ticker"] == ticker) & (self._chains["date"] == str(as_of))
    ].copy()
    if df.empty:
      return df
    df["dte"] = df["expiry"].map(lambda e: (date.fromisoformat(str(e)) - as_of).days)
    df = df[(df["dte"] >= 28) & (df["dte"] <= 45)]
    if df.empty:
      return df
    df["expiry"] = pd.to_datetime(df["expiry"])
    return df.reset_index(drop=True)
```

- [ ] **Step 4: Run new tests + full suite.** Green expected.

- [ ] **Step 5: Commit**

```bash
git add tabfm/trading/adapters/historical.py tabfm/trading/adapters/replay.py tabfm/trading/tests/test_replay_adapter.py
git commit -m "feat(replay): implied-vol back-solve and ReplayAdapter over real option-mark cache"
```

---

### Task 2: replay runner

**Files:**
- Create: `tabfm/trading/backtest/replay_runner.py`
- Test: `tabfm/trading/tests/test_replay_runner.py`

**Interfaces:**
- Consumes: `ReplayAdapter` (Task 1); `run` from run_nightly; `report`, `turning_point_report`; `trading_days` from backtest.runner.
- Produces: `run_replay(cache_path, clf_model=None, reg_model=None, tickers=("SPY",)) -> dict`.

- [ ] **Step 1: Write the failing test**

```python
# tabfm/trading/tests/test_replay_runner.py
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
```

- [ ] **Step 2: Verify it fails** (module missing).

- [ ] **Step 3: Implement**

```python
# tabfm/trading/backtest/replay_runner.py
"""Replay the strategy over real historical option marks (cached parquet).

Trustworthy closed-trade statistics on REAL prices — the evidence a 2-week
live paper window cannot produce. Isolated temp journal/store; never touches
the live data/ book."""
import tempfile
from datetime import date
from pathlib import Path

import pandas as pd

from ..adapters.replay import ReplayAdapter
from ..pipeline.accuracy_tracker import report
from ..pipeline.turning_points import turning_point_report
from ..run_nightly import run
from ..store.journal import init_db
from .runner import trading_days


def run_replay(cache_path, clf_model=None, reg_model=None, tickers=("SPY",)) -> dict:
  chains = pd.read_parquet(cache_path)
  dates = sorted(date.fromisoformat(str(d)) for d in chains["date"].unique())
  days = trading_days(dates[0], dates[-1])

  scratch = Path(tempfile.mkdtemp(prefix="tabfm_replay_"))
  db_path = scratch / "journal.db"
  store_path = scratch / "store.parquet"
  print(f"[Replay] isolated data dir: {scratch}")
  init_db(db_path)

  if clf_model is None or reg_model is None:
    import torch
    from tabfm import tabfm_v1_0_0_pytorch as tabfm_backend
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    clf_model = clf_model or tabfm_backend.load(model_type="classification", device=device)
    reg_model = reg_model or tabfm_backend.load(model_type="regression", device=device)
    print(f"[Replay] TabFM models on {device}")

  adapter = ReplayAdapter(cache_path, as_of=days[-1])

  # Restrict the watchlist to the replay tickers
  import tabfm.trading.watchlist as wl
  import tabfm.trading.pipeline.chain_fetcher as cf
  from tabfm.trading.watchlist import Ticker
  live = [Ticker(t, "index_etf") for t in tickers]
  wl.WATCHLIST = live
  cf.WATCHLIST = live

  for i, sim_date in enumerate(days):
    run(adapter, clf_model, reg_model, as_of=sim_date, db_path=db_path, store_path=store_path)
    if (i + 1) % 20 == 0:
      print(f"[Replay] {i+1}/{len(days)} days")

  metrics = report(db_path=db_path, verbose=True)
  turning_point_report(store_path, db_path, verbose=True)
  return metrics
```

- [ ] **Step 4: Run new test + full suite.** Green expected.

- [ ] **Step 5: Commit**

```bash
git add tabfm/trading/backtest/replay_runner.py tabfm/trading/tests/test_replay_runner.py
git commit -m "feat(replay): run_replay drives the pipeline over the real-mark cache"
```

---

## Self-Review

- **Spec coverage:** implied-vol back-solve + round-trip/None-below-intrinsic (Task 1) ✓; ReplayAdapter cache filtering to date + 28–45 DTE + no-lookahead, real underlying inherited (Task 1) ✓; run_replay date-range iteration + isolation + identical run() loop + report/turning-points (Task 2) ✓; cache schema fixed and shared with the data builder ✓.
- **Placeholders:** none; complete code throughout.
- **Type consistency:** cache columns identical across ReplayAdapter, runner, and the data-builder subagent spec; `run_replay(cache_path, clf_model, reg_model, tickers)` matches the test; `ReplayAdapter(cache_path, as_of)` matches both callers.
- **No-network:** back-solve pure; adapter chain-read from seeded parquet; runner test monkeypatches `run`/`report`/`turning_point_report`. Inherited `get_underlying`/VIX are HistAdapter's (network) — not exercised by these tests.
- **Interaction note:** `run_replay` sets `wl.WATCHLIST`/`cf.WATCHLIST` to the replay tickers (SPY) exactly as the manual backtests do; the isolated temp dir keeps the live `data/` book untouched.
