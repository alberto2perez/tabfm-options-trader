# Backtest Realism + Midday Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the backtest's synthetic chain realistic ($5/$1 strike grid + IV risk premium), add a turning-point report, and add a midday audit-only pass.

**Architecture:** Chain generation is refactored into a pure `_synthetic_chain(S, hv20, as_of)` so it is testable without network. The turning-point report is a read-only analytics module over the history store + journal. The midday audit reuses `audit_positions` behind a new entry-free `run_audit_only`.

**Tech Stack:** Python 3.14, pandas, numpy, sqlite3, pytest. No new dependencies.

## Global Constraints

- Paper trading only; no order-placement APIs.
- Env config, read at call time: `TABFM_BACKTEST_IV_PREMIUM=1.25`.
- Do NOT change the friction, bankroll, gate, calibrator, or baseline logic.
- New tests go in NEW files in `tabfm/trading/tests/` (never `test_hist_adapter.py`, which needs network and is excluded from the suite run).
- `run_audit_only` performs NO chain scoring, NO event gate, NO baseline entry, NO `select_trade` — audit + summary only.
- 2-space indent; no Co-Authored-By / Claude / Anthropic commit trailers.
- Suite baseline: 153 tests. Run: `PYTHONPATH=. python3 -m pytest tabfm/trading/tests/ -q --ignore=tabfm/trading/tests/test_hist_adapter.py --ignore=tabfm/trading/tests/test_live_adapter.py --ignore=tabfm/trading/tests/test_run_nightly.py`
- Add `TABFM_BACKTEST_IV_PREMIUM` to `tabfm/trading/tests/conftest.py` autouse delenv list.
- Paths relative to `/Users/alberto2perez/src/tabfm-options-trader`.

---

### Task 1: Synthetic chain realism

**Files:**
- Modify: `tabfm/trading/adapters/historical.py`
- Modify: `tabfm/trading/tests/conftest.py`
- Test: `tabfm/trading/tests/test_synthetic_chain.py`

**Interfaces:**
- Produces: module-level `_strike_grid(S: float) -> list[float]`, `_iv_premium() -> float`, `_synthetic_chain(S: float, hv20: float, as_of: date) -> pd.DataFrame`; `HistAdapter.get_options_chain` now delegates to `_synthetic_chain`.

- [ ] **Step 1: Write the failing tests**

```python
# tabfm/trading/tests/test_synthetic_chain.py
from datetime import date

from tabfm.trading.adapters.historical import (
  _strike_grid, _iv_premium, _synthetic_chain,
)
from tabfm.trading.pipeline.feature_engineer import engineer_features
from tabfm.trading.pipeline.trade_recommender import _passes_filters

AS_OF = date(2026, 7, 24)


def test_strike_grid_large_underlying_5_spacing():
  grid = _strike_grid(740.0)
  steps = {round(grid[i + 1] - grid[i], 2) for i in range(len(grid) - 1)}
  assert steps == {5.0}
  assert min(grid) <= 740 * 0.86 and max(grid) >= 740 * 1.14


def test_strike_grid_small_underlying_1_spacing():
  grid = _strike_grid(29.0)
  steps = {round(grid[i + 1] - grid[i], 2) for i in range(len(grid) - 1)}
  assert steps == {1.0}


def test_iv_premium_default_and_override(monkeypatch):
  assert _iv_premium() == 1.25
  monkeypatch.setenv("TABFM_BACKTEST_IV_PREMIUM", "1.40")
  assert _iv_premium() == 1.40


def test_iv_premium_scales_written_iv(monkeypatch):
  low = _synthetic_chain(740.0, 0.15, AS_OF)
  monkeypatch.setenv("TABFM_BACKTEST_IV_PREMIUM", "2.0")
  high = _synthetic_chain(740.0, 0.15, AS_OF)
  assert high["iv"].iloc[0] > low["iv"].iloc[0]


def test_thirty_delta_5wide_spread_clears_credit_floor():
  chain = _synthetic_chain(740.0, 0.15, AS_OF)
  chain_data = {
    "ticker": "SPY", "sector": "index_etf", "chain": chain, "vix": 18.0,
    "underlying": {
      "close": 740.0, "sma20": 735.0, "sma50": 730.0, "atr14": 9.0,
      "hv20": 0.15, "volume": 8e7, "volume_zscore": 0.2,
      "momentum_5d": 0.005, "momentum_20d": 0.02, "rsi_14": 55.0,
      "macd_line": 1.0, "macd_signal": 0.8, "macd_histogram": 0.2,
    },
  }
  rows = engineer_features(chain_data, AS_OF, iv_rank=50.0)
  spreads = [r for r in rows if r["spread_width_dollars"] == 5.0]
  assert spreads, "expected $5-wide spreads on the $5 grid"
  ratios = [r["entry_credit"] / r["spread_width_dollars"] for r in spreads]
  assert max(ratios) >= 0.30, f"no $5 spread clears the 0.30 floor; max={max(ratios):.3f}"
  assert any(_passes_filters(r) for r in spreads), "no $5 spread passes the gauntlet"
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=. python3 -m pytest tabfm/trading/tests/test_synthetic_chain.py -q`
Expected: ImportError (`_strike_grid` / `_synthetic_chain` don't exist).

- [ ] **Step 3: Implement**

In `tabfm/trading/adapters/historical.py`, add `import os` if absent. Replace
the `_STRIKE_RANGE = np.arange(...)` line with:

```python
def _strike_grid(S: float) -> list[float]:
  """Fixed-dollar strike grid matching real chains: $5 for large underlyings
  (SPY/QQQ), $1 for small (IWM-sized). Spans ~0.85–1.15 × spot."""
  step = 5.0 if S >= 50.0 else 1.0
  lo = step * round(S * 0.85 / step)
  hi = step * round(S * 1.15 / step)
  n = int(round((hi - lo) / step)) + 1
  return [round(lo + i * step, 2) for i in range(n)]


def _iv_premium() -> float:
  """Variance-risk premium: implied vol runs above realized. Synthetic IV =
  hv20 × this so credit/width lands in the real-market range."""
  return float(os.environ.get("TABFM_BACKTEST_IV_PREMIUM", "1.25"))


def _synthetic_chain(S: float, hv20: float, as_of: date) -> "pd.DataFrame":
  sigma = max(hv20 * _iv_premium(), 0.05)
  rows = []
  for dte in _DTE_WINDOWS:
    expiry = as_of + timedelta(days=dte)
    T = dte / 365.0
    for K in _strike_grid(S):
      for opt in ("call", "put"):
        price = _bs_price(S, K, T, sigma, opt)
        delta = abs(_bs_delta(S, K, T, sigma, opt))
        rows.append({
          "strike": K, "expiry": expiry, "option_type": opt,
          "bid": round(price * 0.99, 2), "ask": round(price * 1.01, 2),
          "mid": round(price, 2), "open_interest": 500,
          "delta": round(delta, 4), "iv": round(sigma, 4), "dte": dte,
        })
  return pd.DataFrame(rows)
```

Replace the body of `HistAdapter.get_options_chain` with:

```python
  def get_options_chain(self, ticker: str, as_of: date) -> pd.DataFrame:
    self._assert_no_lookahead(as_of)
    u = self.get_underlying(ticker, as_of)
    return _synthetic_chain(u["close"], u["hv20"], as_of)
```

`conftest.py`: add `"TABFM_BACKTEST_IV_PREMIUM"` to the delenv tuple.

- [ ] **Step 4: Run the new tests, then the full suite.** Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add tabfm/trading/adapters/historical.py tabfm/trading/tests/test_synthetic_chain.py tabfm/trading/tests/conftest.py
git commit -m "feat(backtest): realistic synthetic chain — $5/$1 strike grid and IV risk premium"
```

---

### Task 2: Turning-point report

**Files:**
- Create: `tabfm/trading/pipeline/turning_points.py`
- Modify: `tabfm/trading/backtest/runner.py` (call it after the accuracy report)
- Test: `tabfm/trading/tests/test_turning_points.py`

**Interfaces:**
- Consumes: `load_store` from history_store; `get_all_closed_trades` from journal.
- Produces: `turning_point_report(store_path, db_path, ticker="SPY", verbose=True) -> dict` with keys `n_flips`, `flips` (list of `{date, from, to, trades_entered_before, losers_into_reversal, pnl_after}`), `trades_into_reversals`.

- [ ] **Step 1: Write the failing tests**

```python
# tabfm/trading/tests/test_turning_points.py
from pathlib import Path

from tabfm.trading.pipeline.turning_points import turning_point_report
from tabfm.trading.store.history_store import append_rows
from tabfm.trading.store.journal import init_db, insert_trade, close_trade


def _store_row(d, trend):
  return {"date": d, "ticker": "SPY", "trend_direction": trend,
          "vix_bucket": "normal", "iv_regime": "fair", "price_close": 740.0}


def _seed_store(path):
  rows = []
  for d in ["2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04", "2026-06-05"]:
    rows.append(_store_row(d, "uptrend"))
  for d in ["2026-06-08", "2026-06-09", "2026-06-10", "2026-06-11", "2026-06-12"]:
    rows.append(_store_row(d, "downtrend"))
  append_rows(rows, path)


def _trade(date_entered):
  return dict(
    date_entered=date_entered, ticker="SPY", direction="put_spread",
    strike_short=735.0, strike_long=730.0, expiry="2026-06-26", dte=21,
    entry_credit=2.0, spread_width=5.0, contracts=1, max_loss=300.0,
    max_profit=200.0, pop_predicted=0.7, pop_raw=0.7, exp_return=0.2,
    regime="normal|uptrend|fair",
  )


def test_detects_flip_and_trade_into_reversal(tmp_path):
  store = tmp_path / "store.parquet"
  db = tmp_path / "j.db"
  _seed_store(store)
  init_db(db)
  tid = insert_trade(_trade("2026-06-05"), db)   # entered day before the flip
  close_trade(tid, "lost", -300.0, "2026-06-26", db)

  m = turning_point_report(store, db, verbose=False)
  assert m["n_flips"] == 1
  flip = m["flips"][0]
  assert flip["from"] == "uptrend" and flip["to"] == "downtrend"
  assert flip["date"] == "2026-06-08"
  assert flip["trades_entered_before"] == 1
  assert flip["losers_into_reversal"] == 1
  assert m["trades_into_reversals"] == 1


def test_empty_when_single_trend(tmp_path):
  store = tmp_path / "store.parquet"
  db = tmp_path / "j.db"
  append_rows([_store_row("2026-06-01", "uptrend"),
               _store_row("2026-06-02", "uptrend")], store)
  init_db(db)
  m = turning_point_report(store, db, verbose=False)
  assert m["n_flips"] == 0
  assert m["flips"] == []
```

- [ ] **Step 2: Run to verify failure** (module missing).

- [ ] **Step 3: Implement**

```python
# tabfm/trading/pipeline/turning_points.py
"""Turning-point analytics for backtests: did the system see direction
changes coming, and how much did each cost? Read-only over the history store
(per-day trend) and the journal (closed trades)."""
from datetime import date, timedelta
from pathlib import Path

from ..store.history_store import load_store, _DEFAULT_STORE
from ..store.journal import get_all_closed_trades, _DEFAULT_DB

_BEFORE_DAYS = 5     # calendar days before a flip to look for open positions
_AFTER_DAYS = 10     # window after a flip for realized P&L


def _trend_sequence(store, ticker: str) -> list[tuple[str, str]]:
  """One (date, trend) per date for the ticker, ordered — most common trend
  per date to collapse multiple candidate rows."""
  df = store[store["ticker"] == ticker]
  if df.empty or "trend_direction" not in df.columns:
    return []
  seq = []
  for d, sub in df.groupby("date"):
    trend = sub["trend_direction"].mode()
    seq.append((str(d), str(trend.iloc[0]) if len(trend) else "unknown"))
  return sorted(seq, key=lambda x: x[0])


def turning_point_report(
  store_path: Path = _DEFAULT_STORE,
  db_path: Path = _DEFAULT_DB,
  ticker: str = "SPY",
  verbose: bool = True,
) -> dict:
  store = load_store(store_path)
  seq = _trend_sequence(store, ticker) if not store.empty else []
  closed = get_all_closed_trades(db_path, strategy="model")

  flips = []
  prev_trend = None
  for d_str, trend in seq:
    if prev_trend is not None and trend != prev_trend:
      flip_date = date.fromisoformat(d_str)
      before_lo = flip_date - timedelta(days=_BEFORE_DAYS)
      after_hi = flip_date + timedelta(days=_AFTER_DAYS)
      entered_before, losers = 0, 0
      pnl_after = 0.0
      for t in closed:
        if t.get("ticker") != ticker:
          continue
        try:
          entered = date.fromisoformat(str(t["date_entered"]))
        except (ValueError, KeyError, TypeError):
          continue
        if before_lo <= entered < flip_date:
          entered_before += 1
          if t["status"] in ("lost", "stopped"):
            losers += 1
        closed_on = t.get("date_closed")
        if closed_on:
          try:
            cd = date.fromisoformat(str(closed_on))
            if flip_date <= cd <= after_hi:
              pnl_after += float(t.get("actual_pnl") or 0)
          except ValueError:
            pass
      flips.append({
        "date": d_str, "from": prev_trend, "to": trend,
        "trades_entered_before": entered_before,
        "losers_into_reversal": losers,
        "pnl_after": round(pnl_after, 2),
      })
    if trend != prev_trend:
      prev_trend = trend

  metrics = {
    "n_flips": len(flips),
    "flips": flips,
    "trades_into_reversals": sum(f["losers_into_reversal"] for f in flips),
  }

  if verbose and flips:
    print("\n╔══════════════════════════════════════╗")
    print(f"  TURNING POINTS ({ticker})")
    print("╠══════════════════════════════════════╣")
    for f in flips:
      print(f"  {f['date']}  {f['from']} → {f['to']}")
      print(f"    open into reversal: {f['trades_entered_before']} "
            f"({f['losers_into_reversal']} lost) · P&L after: ${f['pnl_after']:.2f}")
    print(f"  Trades caught into reversals: {metrics['trades_into_reversals']}")
    print("╚══════════════════════════════════════╝")
  return metrics
```

In `tabfm/trading/backtest/runner.py`: import
`from ..pipeline.turning_points import turning_point_report`, and change the
final return to:

```python
  metrics = report(db_path=db_path, verbose=True)
  turning_point_report(store_path, db_path, verbose=True)
  return metrics
```

- [ ] **Step 4: Run new tests + full suite.**

- [ ] **Step 5: Commit**

```bash
git add tabfm/trading/pipeline/turning_points.py tabfm/trading/backtest/runner.py tabfm/trading/tests/test_turning_points.py
git commit -m "feat(backtest): turning-point report — behavior around direction changes"
```

---

### Task 3: Midday audit-only pass

**Files:**
- Modify: `tabfm/trading/run_nightly.py` (add `run_audit_only`)
- Create: `tabfm/trading/run_audit.py`
- Modify: `docs/NIGHTLY_CLOUD_RUN.md`
- Test: `tabfm/trading/tests/test_audit_only.py`

**Interfaces:**
- Consumes: `audit_positions`, `portfolio_summary`, `init_db`.
- Produces: `run_nightly.run_audit_only(adapter, as_of, db_path=_DEFAULT_DB, store_path=_DEFAULT_STORE) -> list[dict]`; `python -m tabfm.trading.run_audit [--snapshot PATH]`.

- [ ] **Step 1: Write the failing test**

```python
# tabfm/trading/tests/test_audit_only.py
import sqlite3
from datetime import date

import pandas as pd

from tabfm.trading.run_nightly import run_audit_only
from tabfm.trading.store.journal import init_db, insert_trade


class _MarkAdapter:
  """Marks the open spread at ≥ 2× credit so the stop fires."""
  def get_underlying(self, ticker, as_of):
    return {"close": 700.0}
  def get_options_chain(self, ticker, as_of):
    return pd.DataFrame([
      {"strike": 680.0, "expiry": pd.Timestamp("2026-08-21"), "option_type": "put",
       "mid": 4.50, "bid": 4.48, "ask": 4.52, "open_interest": 500,
       "delta": 0.3, "iv": 0.2, "dte": 28},
      {"strike": 675.0, "expiry": pd.Timestamp("2026-08-21"), "option_type": "put",
       "mid": 0.40, "bid": 0.38, "ask": 0.42, "open_interest": 500,
       "delta": 0.2, "iv": 0.2, "dte": 28},
    ])
  def get_vix(self, as_of):
    return 18.0


def _open(db):
  return insert_trade(dict(
    date_entered="2026-07-20", ticker="SPY", direction="put_spread",
    strike_short=680.0, strike_long=675.0, expiry="2026-08-21", dte=28,
    entry_credit=2.0, spread_width=5.0, contracts=1, max_loss=300.0,
    max_profit=200.0, pop_predicted=0.7, pop_raw=0.7, exp_return=0.2,
    regime="normal|sideways|fair",
  ), db)


def test_audit_only_stops_and_summarizes(tmp_path, capsys):
  db = tmp_path / "j.db"
  init_db(db)
  tid = _open(db)
  before = sqlite3.connect(db).execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0]

  closed = run_audit_only(_MarkAdapter(), date(2026, 7, 24), db_path=db,
                          store_path=tmp_path / "store.parquet")

  after = sqlite3.connect(db).execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0]
  assert after == before                    # no new trades placed
  assert len(closed) == 1                    # the open position was managed
  conn = sqlite3.connect(db); conn.row_factory = sqlite3.Row
  assert dict(conn.execute("SELECT * FROM paper_trades WHERE trade_id=?",
                           (tid,)).fetchone())["status"] == "stopped"
  out = capsys.readouterr().out
  assert "PORTFOLIO SUMMARY" in out
```

- [ ] **Step 2: Run to verify failure** (`run_audit_only` missing).

- [ ] **Step 3: Implement**

In `tabfm/trading/run_nightly.py`, add (after `run`):

```python
def run_audit_only(
  adapter, as_of: date | None = None,
  db_path: Path = _DEFAULT_DB, store_path: Path = _DEFAULT_STORE,
) -> list[dict]:
  """Midday pass: manage OPEN positions (both books) without entering new
  trades — catches a stop-loss breach hours before the nightly close on a
  fast day. No chain scoring, gate, baseline, or selection."""
  if as_of is None:
    as_of = date.today()
  print(f"[MiddayAudit] {as_of}")
  init_db(db_path)
  closed = audit_positions(adapter, as_of, db_path)
  print(f"[MiddayAudit] Closed {len(closed)} position(s)")
  print(portfolio_summary(db_path, as_of))
  return closed
```

Create `tabfm/trading/run_audit.py`:

```python
"""Midday audit-only entry point.

  python -m tabfm.trading.run_audit                 # live Robinhood adapter
  python -m tabfm.trading.run_audit --snapshot PATH # from a midday snapshot
"""
import sys
from datetime import date

from .run_nightly import run_audit_only


def main(argv: list[str]) -> None:
  if "--snapshot" in argv:
    path = argv[argv.index("--snapshot") + 1]
    from .adapters.snapshot import SnapshotAdapter
    adapter = SnapshotAdapter(path)
  else:
    import os
    import robin_stocks.robinhood as rh
    try:
      from dotenv import load_dotenv
      load_dotenv()
    except ImportError:
      pass
    user, pw = os.environ.get("RH_USER"), os.environ.get("RH_PASS")
    if user and pw:
      rh.login(user, pw)
    else:
      rh.login()
    from .adapters.live import LiveAdapter
    adapter = LiveAdapter()
  run_audit_only(adapter, date.today())


if __name__ == "__main__":
  main(sys.argv[1:])
```

`docs/NIGHTLY_CLOUD_RUN.md`: add a section after the nightly steps:

```markdown
## Midday audit (~12:30pm ET, trading days only)

A lighter pass that manages OPEN positions without entering new trades —
value is catching a stop-loss breach hours before the close on a fast day.

1. Query open positions: `get_open_trades(db, strategy=None)` — collect their
   distinct tickers. If none, stop (nothing to audit).
2. Fetch CURRENT option marks + underlying for those tickers only (no
   events/vix_history needed). Build a light snapshot with `tickers`
   (underlying + chain) and `closes`.
3. Run: `python -m tabfm.trading.run_audit --snapshot data/snapshots/<date>-midday.json`
4. Commit `data/` only if a position closed:
   `git commit -m "midday-audit: <date> — closed N"`. Report the summary.
```

- [ ] **Step 4: Run new test + full suite.**

- [ ] **Step 5: Commit**

```bash
git add tabfm/trading/run_nightly.py tabfm/trading/run_audit.py docs/NIGHTLY_CLOUD_RUN.md tabfm/trading/tests/test_audit_only.py
git commit -m "feat(trading): midday audit-only pass for early stop-loss management"
```

---

## Self-Review

- **Spec coverage:** $5/$1 grid + IV premium + acceptance (Task 1) ✓; flip detection + trades-into-reversal + pnl-after, empty on single trend (Task 2) ✓; audit-only no-entry + stop + summary + entry point + docs (Task 3) ✓; env var in config + conftest (Task 1) ✓.
- **Placeholders:** none; every step carries complete code.
- **Type consistency:** `_synthetic_chain` signature matches the adapter call and the test; `turning_point_report` keys match the test assertions; `run_audit_only` signature matches `run_audit.py` and the test; `get_all_closed_trades(db_path, strategy="model")` matches the merged journal helper.
- **No-network:** all three test files avoid `HistAdapter._history` (Task 1 tests call `_synthetic_chain` directly); safe in the excluded-adapter suite run.
