# Measurement Pack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Realistic fills (slippage + fees), a dumb-baseline shadow book, and exp_return calibration — so every number the system compounds on is verifiable.

**Architecture:** Friction is applied inside `execute_paper_trade` (one formula, env-tunable). The baseline arm is a `strategy` column in the journal with default-filtered helpers so it can never touch sizing/learning. Return calibration mirrors the existing Platt pattern in `calibrator.py`.

**Tech Stack:** Python 3.14, sqlite3, numpy, pandas, pytest. No new dependencies.

## Global Constraints

- Paper trading only.
- Env config, exact defaults, read at call time: `TABFM_SLIPPAGE_FRAC=0.50`, `TABFM_FEES_RT=0.20`.
- Friction formula exactly: `adjusted = round(max(mid − slip_frac × (bid_ask_pct × mid) − fees_rt/100, 0.01), 2)`; missing/None `bid_ask_pct` → combined spread 0, fees still apply.
- Journal stores ADJUSTED `entry_credit` plus new `entry_credit_mid`; `max_loss`/`max_profit` derive from the adjusted credit.
- Baseline isolation matrix: bankroll, dedup/exposure, Platt + return calibrators see ONLY `strategy='model'` (the journal helpers' default); the position auditor manages BOTH books (`strategy=None`); tracker and portfolio report both.
- Baseline enters every run — including gated nights — 1 contract, no dedup (stacking is the dumb strategy's nature), no RECOMMENDATIONS.md entry.
- Return calibration: identity below 25 closed model trades or zero variance; fit on `exp_return_raw` (fallback `exp_return`) vs `actual_pnl / max_loss` via `numpy.polyfit(x, y, 1)`.
- Add `TABFM_SLIPPAGE_FRAC`, `TABFM_FEES_RT` to the conftest autouse delenv list. Where an EXISTING test breaks only because fills are now friction-adjusted and friction is irrelevant to its purpose, set `monkeypatch.setenv("TABFM_SLIPPAGE_FRAC", "0")` and `monkeypatch.setenv("TABFM_FEES_RT", "0")` in that test rather than changing its assertions; if the test genuinely asserts stored credit values, update the expected numbers with a comment instead.
- 2-space indent; no Co-Authored-By / Claude / Anthropic commit trailers.
- Suite baseline: 135 tests. Run: `PYTHONPATH=. python3 -m pytest tabfm/trading/tests/ -q --ignore=tabfm/trading/tests/test_hist_adapter.py --ignore=tabfm/trading/tests/test_live_adapter.py --ignore=tabfm/trading/tests/test_run_nightly.py`
- Paths relative to `/Users/alberto2perez/src/tabfm-options-trader`.

---

### Task 1: Friction model

**Files:**
- Modify: `tabfm/trading/store/journal.py` (schema + migration + insert gain `entry_credit_mid`)
- Modify: `tabfm/trading/pipeline/paper_executor.py`
- Modify: `tabfm/trading/tests/conftest.py` (env scrub additions)
- Test: `tabfm/trading/tests/test_friction.py`

**Interfaces:**
- Consumes: existing journal insert/init patterns.
- Produces: `_apply_friction(mid_credit: float, bid_ask_pct: float) -> float` in paper_executor (used by Task 2's baseline too); journal column `entry_credit_mid`; `format_recommendation` shows `est. fill (mid $X)`.

- [ ] **Step 1: Write the failing tests**

```python
# tabfm/trading/tests/test_friction.py
from datetime import date

from tabfm.trading.pipeline.paper_executor import (
  _apply_friction, execute_paper_trade, format_recommendation,
)
from tabfm.trading.store.journal import init_db, get_open_trades

_TRADE = {
  "ticker": "SPY", "direction": "put_spread", "strike_short": 700.0,
  "strike_long": 695.0, "expiry": "2026-08-21", "dte": 28,
  "entry_credit": 2.25, "spread_width_dollars": 5.0, "bid_ask_pct": 0.10,
  "contracts": 2, "pop_predicted": 0.7, "exp_return": 0.2,
  "vix_bucket": "normal", "trend_direction": "sideways", "iv_regime": "fair",
  "total_risk": 550.0, "iv_rank": 50.0,
}


def test_friction_formula():
  # 2.25 - 0.5 * (0.10 * 2.25) - 0.20/100 = 2.25 - 0.1125 - 0.002 = 2.1355 -> 2.14
  assert _apply_friction(2.25, 0.10) == 2.14


def test_friction_floor():
  assert _apply_friction(0.05, 3.0) == 0.01


def test_friction_missing_spread_fees_only():
  # 2.25 - 0 - 0.002 -> 2.25 (rounds back)
  assert _apply_friction(2.25, 0.0) == 2.25
  # visible with bigger fees
  import os
  os.environ["TABFM_FEES_RT"] = "2.0"
  try:
    assert _apply_friction(2.25, 0.0) == 2.23
  finally:
    del os.environ["TABFM_FEES_RT"]


def test_friction_env_overrides(monkeypatch):
  monkeypatch.setenv("TABFM_SLIPPAGE_FRAC", "1.0")
  monkeypatch.setenv("TABFM_FEES_RT", "0.0")
  # 2.25 - 1.0 * 0.225 = 2.025 -> 2.02 (banker's-safe: round(2.025, 2))
  assert _apply_friction(2.25, 0.10) in (2.02, 2.03)  # float repr tolerance


def test_executor_stores_adjusted_and_mid(tmp_path):
  db = tmp_path / "j.db"
  init_db(db)
  execute_paper_trade(dict(_TRADE), date(2026, 7, 24), db)
  row = get_open_trades(db)[0]
  assert row["entry_credit"] == 2.14
  assert row["entry_credit_mid"] == 2.25
  # max_loss / max_profit derive from the ADJUSTED credit
  assert row["max_loss"] == round(2 * (5.0 - 2.14) * 100, 2)
  assert row["max_profit"] == round(2 * 2.14 * 100, 2)


def test_recommendation_shows_fill_and_mid():
  out = format_recommendation(dict(_TRADE), 1, date(2026, 7, 24))
  assert "2.14" in out and "2.25" in out
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=. python3 -m pytest tabfm/trading/tests/test_friction.py -q`
Expected: FAIL (`_apply_friction` doesn't exist).

- [ ] **Step 3: Implement**

`tabfm/trading/store/journal.py`:
- `_SCHEMA`: after `entry_credit  REAL NOT NULL,` add `  entry_credit_mid REAL,`
- migration loop: extend the tuple to `("pop_raw", "pop_market", "mfe", "mae", "entry_credit_mid")`
- `insert_trade`: add `entry_credit_mid` to columns/VALUES; params dict gains `"entry_credit_mid": trade.get("entry_credit_mid")`.

`tabfm/trading/pipeline/paper_executor.py`: add `import os` and:

```python
def _apply_friction(mid_credit: float, bid_ask_pct: float) -> float:
  """Round-trip fill friction applied at entry: half the combined bid/ask
  spread plus regulatory fees. Keeps every downstream number (P&L, bankroll,
  calibration) compounding on realistic fills."""
  slip_frac = float(os.environ.get("TABFM_SLIPPAGE_FRAC", "0.50"))
  fees_rt = float(os.environ.get("TABFM_FEES_RT", "0.20"))
  combined_spread = (bid_ask_pct or 0.0) * mid_credit
  return round(max(mid_credit - slip_frac * combined_spread - fees_rt / 100.0, 0.01), 2)
```

In `execute_paper_trade`, before building `record`:

```python
  mid_credit = trade["entry_credit"]
  fill_credit = _apply_friction(mid_credit, float(trade.get("bid_ask_pct") or 0.0))
```

and in `record` replace the three credit-derived entries:

```python
    "entry_credit": fill_credit,
    "entry_credit_mid": mid_credit,
    "max_loss": round(trade["contracts"] * (trade["spread_width_dollars"] - fill_credit) * 100, 2),
    "max_profit": round(trade["contracts"] * fill_credit * 100, 2),
```

In `format_recommendation`, compute the same fill and show both — replace the
`entry_credit=trade["entry_credit"],` template argument with
`entry_credit=_apply_friction(trade["entry_credit"], float(trade.get("bid_ask_pct") or 0.0)),`
and change the template line
`  Entry Credit ${entry_credit} mid-price` to
`  Entry Credit ${entry_credit} est. fill (mid ${entry_credit_mid})`,
passing `entry_credit_mid=trade["entry_credit"]` as a new format argument.
Update `max_profit_per`/`max_loss_per` args to use the fill credit for
consistency.

`tabfm/trading/tests/conftest.py`: add `"TABFM_SLIPPAGE_FRAC", "TABFM_FEES_RT"` to the delenv tuple.

- [ ] **Step 4: Run new tests, then the full suite; repair collateral per the Global Constraints rule (zero-friction env pins for tests unrelated to friction; expected-number updates where a test asserts stored credits — e.g., test_paper_executor and the pop_market executor test may need this).**

- [ ] **Step 5: Commit**

```bash
git add -A tabfm/trading docs 2>/dev/null; git add tabfm/trading/store/journal.py tabfm/trading/pipeline/paper_executor.py tabfm/trading/tests/
git commit -m "feat(trading): realistic fills — slippage and fees applied at entry"
```

---

### Task 2: Dumb baseline shadow book

**Files:**
- Modify: `tabfm/trading/store/journal.py` (strategy column, filtered helpers)
- Create: `tabfm/trading/pipeline/baseline.py`
- Modify: `tabfm/trading/pipeline/paper_executor.py` (strategy passthrough)
- Modify: `tabfm/trading/pipeline/position_auditor.py` (audit all strategies)
- Modify: `tabfm/trading/pipeline/accuracy_tracker.py` (baseline comparison)
- Modify: `tabfm/trading/pipeline/portfolio.py` (shadow-book line)
- Modify: `tabfm/trading/run_nightly.py` (enter baseline each run)
- Test: `tabfm/trading/tests/test_baseline_arm.py`

**Interfaces:**
- Consumes: `_apply_friction` via `execute_paper_trade` (Task 1).
- Produces: `get_open_trades(path, strategy="model")` / `get_all_closed_trades(path, strategy="model")` with `strategy=None` = all; `execute_paper_trade(trade, as_of, path, strategy="model")`; `enter_baseline_trade(chain_data_list, as_of, db_path) -> int | None` in `tabfm.trading.pipeline.baseline`; tracker metrics `baseline_trades`, `baseline_pnl`, `baseline_win_rate`, `model_vs_baseline_pnl`.

- [ ] **Step 1: Write the failing tests**

```python
# tabfm/trading/tests/test_baseline_arm.py
from datetime import date

import pandas as pd

from tabfm.trading.pipeline.accuracy_tracker import report
from tabfm.trading.pipeline.bankroll import get_bankroll
from tabfm.trading.pipeline.baseline import enter_baseline_trade
from tabfm.trading.store.journal import (
  init_db, insert_trade, close_trade, get_open_trades, get_all_closed_trades,
)

AS_OF = date(2026, 7, 24)


def _spy_chain_data():
  chain = pd.DataFrame([
    {"strike": 700.0, "expiry": pd.Timestamp("2026-08-21"), "option_type": "put",
     "bid": 3.40, "ask": 3.50, "mid": 3.45, "open_interest": 500,
     "delta": 0.31, "iv": 0.2, "dte": 28},
    {"strike": 695.0, "expiry": pd.Timestamp("2026-08-21"), "option_type": "put",
     "bid": 2.40, "ask": 2.50, "mid": 2.45, "open_interest": 500,
     "delta": 0.24, "iv": 0.2, "dte": 28},
    {"strike": 690.0, "expiry": pd.Timestamp("2026-08-21"), "option_type": "put",
     "bid": 1.70, "ask": 1.80, "mid": 1.75, "open_interest": 500,
     "delta": 0.18, "iv": 0.2, "dte": 28},
  ])
  return [{"ticker": "SPY", "sector": "index_etf", "chain": chain,
           "underlying": {"close": 738.0}, "vix": 18.5}]


def test_baseline_enters_one_contract(tmp_path):
  db = tmp_path / "j.db"
  init_db(db)
  tid = enter_baseline_trade(_spy_chain_data(), AS_OF, db)
  assert tid is not None
  rows = get_open_trades(db, strategy="baseline")
  assert len(rows) == 1
  r = rows[0]
  assert r["strategy"] == "baseline"
  assert r["contracts"] == 1
  # short = delta closest to 0.30 -> 700 strike; long = adjacent below -> 695
  assert r["strike_short"] == 700.0 and r["strike_long"] == 695.0


def test_baseline_stacks_across_nights(tmp_path):
  db = tmp_path / "j.db"
  init_db(db)
  enter_baseline_trade(_spy_chain_data(), AS_OF, db)
  enter_baseline_trade(_spy_chain_data(), date(2026, 7, 27), db)
  assert len(get_open_trades(db, strategy="baseline")) == 2


def test_baseline_skips_without_spy(tmp_path):
  db = tmp_path / "j.db"
  init_db(db)
  assert enter_baseline_trade([], AS_OF, db) is None


def test_baseline_invisible_to_model_helpers_and_bankroll(tmp_path):
  db = tmp_path / "j.db"
  init_db(db)
  enter_baseline_trade(_spy_chain_data(), AS_OF, db)
  assert get_open_trades(db) == []                 # default filters to model
  assert get_all_closed_trades(db) == []
  bk = get_bankroll(db)
  assert bk.equity == 2000.0                       # untouched by baseline
  # close the baseline trade at a loss; bankroll must still ignore it
  tid = get_open_trades(db, strategy=None)[0]["trade_id"]
  close_trade(tid, "lost", -300.0, "2026-08-21", db)
  assert get_bankroll(db).equity == 2000.0


def test_tracker_reports_both_arms(tmp_path):
  db = tmp_path / "j.db"
  init_db(db)
  base = dict(
    date_entered="2026-07-01", ticker="SPY", direction="put_spread",
    strike_short=700.0, strike_long=695.0, expiry="2026-07-18", dte=17,
    entry_credit=2.0, spread_width=5.0, contracts=1, max_loss=300.0,
    max_profit=200.0, pop_predicted=0.7, pop_raw=0.7, exp_return=0.2,
    regime="normal|sideways|fair",
  )
  t1 = insert_trade(base, db)                       # model (default)
  close_trade(t1, "won", 200.0, "2026-07-18", db)
  t2 = insert_trade({**base, "strategy": "baseline"}, db)
  close_trade(t2, "lost", -300.0, "2026-07-18", db)
  m = report(db_path=db, verbose=False)
  assert m["total_trades"] == 1                     # model-only headline
  assert m["baseline_trades"] == 1
  assert m["baseline_pnl"] == -300.0
  assert m["model_vs_baseline_pnl"] == 500.0
```

Note for the implementer: `insert_trade` must accept an optional
`"strategy"` key in the trade dict (defaulting to `'model'`).

- [ ] **Step 2: Verify failure** (`No module named ...baseline`, missing strategy column).

- [ ] **Step 3: Implement**

`journal.py`:
- `_SCHEMA`: add `  strategy      TEXT NOT NULL DEFAULT 'model',` before `status`.
- Migration: after the REAL-columns loop add:
```python
    if "strategy" not in cols:
      conn.execute("ALTER TABLE paper_trades ADD COLUMN strategy TEXT")
    conn.execute("UPDATE paper_trades SET strategy='model' WHERE strategy IS NULL")
```
- `insert_trade`: add `strategy` to columns/VALUES; params gains
  `"strategy": trade.get("strategy", "model")`.
- Helpers:
```python
def get_open_trades(path: Path = _DEFAULT_DB, strategy: str | None = "model") -> list[dict]:
  with sqlite3.connect(path) as conn:
    conn.row_factory = sqlite3.Row
    q = "SELECT * FROM paper_trades WHERE status = 'open'"
    params: tuple = ()
    if strategy is not None:
      q += " AND strategy = ?"
      params = (strategy,)
    return [dict(r) for r in conn.execute(q, params).fetchall()]
```
  (same pattern for `get_all_closed_trades` with `status != 'open'`.)

`paper_executor.py`: `execute_paper_trade(trade, as_of, path=_DEFAULT_DB, strategy="model")`; record gains `"strategy": strategy`.

`position_auditor.py`: `open_trades = get_open_trades(db_path, strategy=None)`.

New `tabfm/trading/pipeline/baseline.py`:
```python
"""Dumb-baseline shadow book: always sell the ~30-delta SPY put spread.

One virtual contract every run — no gates, no model, no bankroll. Exists
purely to measure what the full stack adds over the simplest premium-selling
rule. Never touches sizing or learning (journal helpers filter by strategy).
"""
from datetime import date
from pathlib import Path

from .paper_executor import execute_paper_trade
from ..store.journal import _DEFAULT_DB


def enter_baseline_trade(
  chain_data_list: list, as_of: date, db_path: Path = _DEFAULT_DB
) -> int | None:
  spy = next((c for c in chain_data_list if c["ticker"] == "SPY"), None)
  if spy is None or not len(spy["chain"]):
    return None
  chain = spy["chain"]
  puts = chain[chain["option_type"] == "put"]
  puts = puts[(puts["delta"] >= 0.15) & (puts["delta"] <= 0.40)]
  if puts.empty:
    return None
  short = puts.iloc[(puts["delta"] - 0.30).abs().argsort().iloc[0]]
  longs = chain[
    (chain["option_type"] == "put") & (chain["strike"] < short["strike"])
  ].sort_values("strike")
  if longs.empty:
    return None
  long = longs.iloc[-1]
  credit = float(short["bid"]) - float(long["ask"])
  width = float(short["strike"]) - float(long["strike"])
  if credit <= 0 or width <= 0:
    return None
  ba = (float(short["ask"]) - float(short["bid"])) + (float(long["ask"]) - float(long["bid"]))
  expiry = short["expiry"]
  expiry_date = expiry.date() if hasattr(expiry, "date") else expiry
  trade = {
    "ticker": "SPY", "direction": "put_spread",
    "strike_short": float(short["strike"]), "strike_long": float(long["strike"]),
    "expiry": str(expiry_date), "dte": int(short["dte"]),
    "entry_credit": round(credit, 2), "spread_width_dollars": round(width, 2),
    "bid_ask_pct": round(ba / credit, 4), "contracts": 1,
    "pop_predicted": 0.0, "exp_return": 0.0,
    "vix_bucket": "na", "trend_direction": "na", "iv_regime": "na",
  }
  tid = execute_paper_trade(trade, as_of, db_path, strategy="baseline")
  print(f"[Baseline] sold SPY {trade['strike_short']:g}/{trade['strike_long']:g} "
        f"exp {trade['expiry']} (trade {tid})")
  return tid
```

`run_nightly.py`: import `enter_baseline_trade` from `.pipeline.baseline`;
call `enter_baseline_trade(chain_data_list, as_of, db_path)` immediately
after the `chain_data_list = fetch_chains(adapter, as_of)` line (before the
gate — the baseline is deliberately dumb and trades on gated nights too).

`accuracy_tracker.py`: import stays `get_all_closed_trades`; after the Brier
block add:
```python
  baseline = get_all_closed_trades(db_path, strategy="baseline")
  if baseline:
    b_wins = sum(1 for t in baseline if t["status"] in ("won", "partial"))
    b_pnl = sum(float(t["actual_pnl"] or 0) for t in baseline)
    metrics["baseline_trades"] = len(baseline)
    metrics["baseline_pnl"] = round(b_pnl, 2)
    metrics["baseline_win_rate"] = round(b_wins / len(baseline), 4)
    metrics["model_vs_baseline_pnl"] = round(cumulative_pnl - b_pnl, 2)
```
plus a verbose print line
`Baseline (dumb): N trades · $X P&L · model − baseline = $Y`.

`portfolio.py`: after the closed-stats lines add:
```python
  b_open = get_open_trades(db_path, strategy="baseline")
  b_closed = get_all_closed_trades(db_path, strategy="baseline")
  if b_open or b_closed:
    b_pnl = sum(float(t["actual_pnl"] or 0) for t in b_closed)
    lines.append(
      f"  BASELINE (shadow): {len(b_open)} open · {len(b_closed)} closed · P&L ${b_pnl:,.2f}"
    )
```

- [ ] **Step 4: Run new tests + full suite.** Watch specifically: any test
  asserting journal row counts via `strategy=None` semantics, and the
  event-gate integration test (baseline entry adds a `[Baseline]` line and a
  journal row with `strategy='baseline'` — its "no journal insert on gated
  night" assertion counts ALL rows; update that assertion to count
  `WHERE strategy='model'` with a comment).

- [ ] **Step 5: Commit**

```bash
git add tabfm/trading docs
git commit -m "feat(trading): dumb-baseline shadow book with model-vs-baseline reporting"
```

---

### Task 3: exp_return calibration

**Files:**
- Modify: `tabfm/trading/store/journal.py` (exp_return_raw column)
- Modify: `tabfm/trading/pipeline/calibrator.py`
- Modify: `tabfm/trading/pipeline/paper_executor.py` (persist raw)
- Modify: `tabfm/trading/run_nightly.py` (fit + apply)
- Test: `tabfm/trading/tests/test_return_calibration.py`

**Interfaces:**
- Consumes: journal helpers (model-filtered by default), numpy.
- Produces: `fit_return_calibration(db_path=_DEFAULT_DB, min_trades=25) -> tuple[float, float] | None`; `calibrate_return(exp_return, params) -> float`; journal column `exp_return_raw`.

- [ ] **Step 1: Write the failing tests**

```python
# tabfm/trading/tests/test_return_calibration.py
from tabfm.trading.pipeline.calibrator import fit_return_calibration, calibrate_return
from tabfm.trading.store.journal import init_db, insert_trade, close_trade

import pytest


def _trade(exp_return):
  return dict(
    date_entered="2026-07-01", ticker="SPY", direction="put_spread",
    strike_short=700.0, strike_long=695.0, expiry="2026-07-18", dte=17,
    entry_credit=2.0, spread_width=5.0, contracts=1, max_loss=300.0,
    max_profit=200.0, pop_predicted=0.7, pop_raw=0.7,
    exp_return=exp_return, exp_return_raw=exp_return,
    regime="normal|sideways|fair",
  )


def _seed_biased(db, n):
  """Model predicts 2x reality: realized fraction = 0.5 * predicted."""
  init_db(db)
  for i in range(n):
    pred = 0.10 + (i % 10) * 0.02          # 0.10 .. 0.28, has variance
    realized_frac = 0.5 * pred
    tid = insert_trade(_trade(pred), db)
    close_trade(tid, "won", realized_frac * 300.0, f"2026-07-{(i % 27) + 1:02d}", db)


def test_identity_below_min_trades(tmp_path):
  db = tmp_path / "j.db"
  _seed_biased(db, 10)
  assert fit_return_calibration(db) is None


def test_recovers_linear_bias(tmp_path):
  db = tmp_path / "j.db"
  _seed_biased(db, 30)
  params = fit_return_calibration(db)
  assert params is not None
  a, b = params
  assert a == pytest.approx(0.5, abs=0.02)
  assert b == pytest.approx(0.0, abs=0.01)
  assert calibrate_return(0.20, params) == pytest.approx(0.10, abs=0.01)


def test_no_variance_returns_none(tmp_path):
  db = tmp_path / "j.db"
  init_db(db)
  for i in range(30):
    tid = insert_trade(_trade(0.20), db)   # constant prediction
    close_trade(tid, "won", 30.0, f"2026-07-{(i % 27) + 1:02d}", db)
  assert fit_return_calibration(db) is None
```

- [ ] **Step 2: Verify failure** (functions don't exist).

- [ ] **Step 3: Implement**

`journal.py`: extend the REAL-column migration tuple with `"exp_return_raw"`
and add `exp_return_raw REAL,` to `_SCHEMA` (after `exp_return`); add to
`insert_trade` columns/VALUES with params
`"exp_return_raw": trade.get("exp_return_raw")`.

`calibrator.py` — append:

```python
def fit_return_calibration(
  db_path: Path = _DEFAULT_DB, min_trades: int = 25
) -> tuple[float, float] | None:
  """Linear map from predicted exp_return to realized return fraction.

  Realized fraction = actual_pnl / max_loss for closed model trades. Returns
  (slope, intercept) or None below min_trades / without prediction variance.
  """
  trades = get_all_closed_trades(db_path)
  xs, ys = [], []
  for t in trades:
    max_loss = float(t.get("max_loss") or 0)
    if max_loss <= 0 or t.get("actual_pnl") is None:
      continue
    pred = t.get("exp_return_raw")
    if pred is None:
      pred = t.get("exp_return")
    if pred is None:
      continue
    xs.append(float(pred))
    ys.append(float(t["actual_pnl"]) / max_loss)
  if len(xs) < min_trades:
    return None
  x_arr, y_arr = np.array(xs), np.array(ys)
  if float(x_arr.std()) < 1e-9:
    return None
  slope, intercept = np.polyfit(x_arr, y_arr, 1)
  return float(slope), float(intercept)


def calibrate_return(exp_return: float, params: tuple[float, float]) -> float:
  slope, intercept = params
  return float(slope * exp_return + intercept)
```

(`calibrator.py` already imports numpy as np and `get_all_closed_trades`.)

`paper_executor.py` — record gains
`"exp_return_raw": trade.get("exp_return_raw", trade["exp_return"]),`.

`run_nightly.py` — import `fit_return_calibration, calibrate_return`
alongside the existing calibrator imports, and immediately after the Platt
block add:

```python
  # Return calibration: map predicted exp_return onto realized fractions.
  ret_params = fit_return_calibration(db_path)
  if ret_params is not None:
    for c in all_candidates:
      if c["pop_predicted"] == 0.5 and c["exp_return"] == 0.0:
        continue  # fallback-scored
      c["exp_return_raw"] = c["exp_return"]
      c["exp_return"] = round(calibrate_return(c["exp_return_raw"], ret_params), 4)
```

- [ ] **Step 4: Run new tests + full suite.**

- [ ] **Step 5: Commit**

```bash
git add tabfm/trading
git commit -m "feat(trading): exp_return calibration from realized outcomes"
```

---

## Self-Review

- **Spec coverage:** friction formula/env/floor/mid column/card display (Task 1) ✓; strategy column + filtered helpers + isolation matrix + auditor-all + baseline entry incl. gated nights + tracker/portfolio reporting (Task 2) ✓; exp_return_raw + fit/apply with identity fallback (Task 3) ✓; conftest env additions (Task 1) ✓; sizing-uses-mid approximation is documented in the spec, not code ✓.
- **Placeholders:** none. Collateral-repair instructions state the decision rule (zero-friction pins vs expected-number updates) rather than "fix as needed".
- **Type consistency:** `strategy` kwarg name/default identical across journal, executor, auditor, tracker, portfolio, baseline; `enter_baseline_trade` signature matches run_nightly call; calibrator function names match run_nightly imports; `_apply_friction` used by both executor and format_recommendation.
- **Ordering:** Task 2 depends on Task 1's `_apply_friction` (via executor) and its journal migration style; Task 3 is independent of Task 2 except journal file merges — tasks are sequential as always.
