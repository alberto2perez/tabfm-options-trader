# Bankroll-Managed Position Sizing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace static dollar risk caps with fixed-fractional sizing from tracked equity (starting capital + realized P&L), including a drawdown circuit breaker.

**Architecture:** A pure `bankroll.py` module computes a `Bankroll` snapshot from the journal each run (equity, peak, drawdown, recovery mode, slice/exposure limits). `select_trade` sizes contracts from `min(slice, exposure − open risk)` instead of static caps. The portfolio summary gains a bankroll block so every run explains its sizing.

**Tech Stack:** Python 3.14, sqlite3 (via existing journal helpers), pytest. No new dependencies.

## Global Constraints

- Paper trading only; no order-placement APIs.
- Env config with exact defaults, read at call time: `TABFM_STARTING_CAPITAL=2000`, `TABFM_RISK_PER_TRADE=0.15`, `TABFM_MAX_EXPOSURE=0.45`, `TABFM_DRAWDOWN_BRAKE=0.25`.
- Recovery mode (drawdown from peak > brake) halves the slice fraction; exits only at a new all-time equity high.
- Equity = starting + realized P&L only (open positions never marked to market); floored at 0 → zero limits.
- `_MAX_RISK`, `_MAX_PORTFOLIO_RISK`, the `TABFM_MAX_PORTFOLIO_RISK` env var, the `_contracts()` helper, and `_passes_filters`' `spread_width_dollars * 100 > 1000` check are all REMOVED. `_MAX_CONTRACTS = 10` stays.
- Dedup rule and all other gauntlet checks unchanged.
- 2-space indentation.
- Suite baseline before this plan: 97 tests. Run: `PYTHONPATH=. python3 -m pytest tabfm/trading/tests/ -q --ignore=tabfm/trading/tests/test_hist_adapter.py --ignore=tabfm/trading/tests/test_live_adapter.py --ignore=tabfm/trading/tests/test_run_nightly.py`
- No Co-Authored-By / Claude / Anthropic trailers in commit messages.
- All paths relative to repo root `/Users/alberto2perez/src/tabfm-options-trader`.

---

### Task 1: Bankroll module

**Files:**
- Create: `tabfm/trading/pipeline/bankroll.py`
- Test: `tabfm/trading/tests/test_bankroll.py`

**Interfaces:**
- Consumes: `get_all_closed_trades`, `_DEFAULT_DB` from `tabfm.trading.store.journal` (existing).
- Produces: `Bankroll` dataclass (fields `starting, realized, equity, peak_equity, drawdown_pct, recovery_mode, slice_limit, exposure_limit`, all floats except `recovery_mode: bool`); `get_bankroll(db_path: Path = _DEFAULT_DB) -> Bankroll`; `default_bankroll() -> Bankroll` (empty-journal equivalent for legacy callers). Tasks 2–3 import all three from `tabfm.trading.pipeline.bankroll`.

- [ ] **Step 1: Write the failing tests**

```python
# tabfm/trading/tests/test_bankroll.py
from datetime import date
from pathlib import Path

import pytest

from tabfm.trading.pipeline.bankroll import Bankroll, get_bankroll, default_bankroll
from tabfm.trading.store.journal import init_db, insert_trade, close_trade


def _trade(pop=0.6):
  return dict(
    date_entered="2026-07-01", ticker="SPY", direction="put_spread",
    strike_short=700.0, strike_long=695.0, expiry="2026-07-18", dte=17,
    entry_credit=2.0, spread_width=5.0, contracts=1, max_loss=300.0,
    max_profit=200.0, pop_predicted=pop, pop_raw=pop, exp_return=0.2,
    regime="normal|sideways|fair",
  )


def _seed(db, pnls):
  init_db(db)
  for i, pnl in enumerate(pnls):
    tid = insert_trade(_trade(), db)
    close_trade(tid, "won" if pnl > 0 else "lost", pnl, f"2026-07-{10 + i:02d}", db)


def test_empty_journal_defaults(tmp_path):
  db = tmp_path / "j.db"
  init_db(db)
  bk = get_bankroll(db)
  assert bk.starting == 2000.0
  assert bk.realized == 0.0
  assert bk.equity == 2000.0
  assert bk.peak_equity == 2000.0
  assert bk.drawdown_pct == 0.0
  assert bk.recovery_mode is False
  assert bk.slice_limit == pytest.approx(300.0)
  assert bk.exposure_limit == pytest.approx(900.0)


def test_wins_raise_equity_and_peak(tmp_path):
  db = tmp_path / "j.db"
  _seed(db, [200.0, 200.0])
  bk = get_bankroll(db)
  assert bk.equity == 2400.0
  assert bk.peak_equity == 2400.0
  assert bk.slice_limit == pytest.approx(360.0)


def test_losses_shrink_slice(tmp_path):
  db = tmp_path / "j.db"
  _seed(db, [-300.0])
  bk = get_bankroll(db)
  assert bk.equity == 1700.0
  assert bk.peak_equity == 2000.0
  assert bk.drawdown_pct == pytest.approx(0.15)
  assert bk.recovery_mode is False
  assert bk.slice_limit == pytest.approx(255.0)


def test_drawdown_over_brake_triggers_recovery(tmp_path):
  db = tmp_path / "j.db"
  # Peak 2400 after wins, then losses to 1700: drawdown 700/2400 = 29.2% > 25%
  _seed(db, [200.0, 200.0, -300.0, -300.0, -100.0])
  bk = get_bankroll(db)
  assert bk.equity == 1700.0
  assert bk.peak_equity == 2400.0
  assert bk.recovery_mode is True
  # Recovery halves the fraction: 1700 * 0.075
  assert bk.slice_limit == pytest.approx(127.5)


def test_recovery_exits_on_new_high(tmp_path):
  db = tmp_path / "j.db"
  # Deep drawdown then a run back above the old peak
  _seed(db, [200.0, 200.0, -300.0, -300.0, -100.0, 400.0, 400.0])
  bk = get_bankroll(db)
  assert bk.equity == 2500.0
  assert bk.peak_equity == 2500.0
  assert bk.recovery_mode is False
  assert bk.slice_limit == pytest.approx(375.0)


def test_equity_floor_zeroes_limits(tmp_path):
  db = tmp_path / "j.db"
  _seed(db, [-1500.0, -800.0])
  bk = get_bankroll(db)
  assert bk.equity == 0.0
  assert bk.slice_limit == 0.0
  assert bk.exposure_limit == 0.0


def test_env_overrides(tmp_path, monkeypatch):
  monkeypatch.setenv("TABFM_STARTING_CAPITAL", "5000")
  monkeypatch.setenv("TABFM_RISK_PER_TRADE", "0.10")
  monkeypatch.setenv("TABFM_MAX_EXPOSURE", "0.30")
  db = tmp_path / "j.db"
  init_db(db)
  bk = get_bankroll(db)
  assert bk.starting == 5000.0
  assert bk.slice_limit == pytest.approx(500.0)
  assert bk.exposure_limit == pytest.approx(1500.0)


def test_default_bankroll_matches_empty_journal():
  bk = default_bankroll()
  assert bk.equity == 2000.0
  assert bk.slice_limit == pytest.approx(300.0)
  assert bk.recovery_mode is False


def test_closed_trades_ordered_by_close_date(tmp_path):
  db = tmp_path / "j.db"
  init_db(db)
  # Insert out of order; walk must follow date_closed order for correct peak.
  t1 = insert_trade(_trade(), db)
  t2 = insert_trade(_trade(), db)
  close_trade(t2, "lost", -300.0, "2026-07-20", db)  # later
  close_trade(t1, "won", 400.0, "2026-07-11", db)    # earlier
  bk = get_bankroll(db)
  # Walk: 2000 -> 2400 (07-11) -> 2100 (07-20); peak 2400
  assert bk.peak_equity == 2400.0
  assert bk.equity == 2100.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. python3 -m pytest tabfm/trading/tests/test_bankroll.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'tabfm.trading.pipeline.bankroll'`

- [ ] **Step 3: Write the implementation**

```python
# tabfm/trading/pipeline/bankroll.py
"""Tracked-equity bankroll: fixed-fractional sizing limits from the journal.

Equity = starting capital + realized P&L of closed trades. Open positions are
never marked to market — sizing reacts only to realized outcomes. Recovery
mode (drawdown from peak beyond the brake) halves the per-trade slice until
equity sets a new all-time high.
"""
import os
from dataclasses import dataclass
from pathlib import Path

from ..store.journal import get_all_closed_trades, _DEFAULT_DB


@dataclass
class Bankroll:
  starting: float
  realized: float
  equity: float
  peak_equity: float
  drawdown_pct: float
  recovery_mode: bool
  slice_limit: float
  exposure_limit: float


def _config() -> tuple[float, float, float, float]:
  return (
    float(os.environ.get("TABFM_STARTING_CAPITAL", "2000")),
    float(os.environ.get("TABFM_RISK_PER_TRADE", "0.15")),
    float(os.environ.get("TABFM_MAX_EXPOSURE", "0.45")),
    float(os.environ.get("TABFM_DRAWDOWN_BRAKE", "0.25")),
  )


def _build(starting: float, risk_frac: float, max_exposure: float,
           brake: float, closed: list[dict]) -> Bankroll:
  ordered = sorted(
    closed,
    key=lambda t: (str(t.get("date_closed") or ""), t.get("trade_id") or 0),
  )
  equity = peak = starting
  realized = 0.0
  for t in ordered:
    pnl = float(t.get("actual_pnl") or 0)
    realized += pnl
    equity += pnl
    peak = max(peak, equity)
  equity = max(equity, 0.0)
  drawdown = (peak - equity) / peak if peak > 0 else 0.0
  recovery = drawdown > brake
  slice_frac = risk_frac * (0.5 if recovery else 1.0)
  return Bankroll(
    starting=starting,
    realized=round(realized, 2),
    equity=round(equity, 2),
    peak_equity=round(peak, 2),
    drawdown_pct=round(drawdown, 4),
    recovery_mode=recovery,
    slice_limit=round(equity * slice_frac, 2) if equity > 0 else 0.0,
    exposure_limit=round(equity * max_exposure, 2) if equity > 0 else 0.0,
  )


def get_bankroll(db_path: Path = _DEFAULT_DB) -> Bankroll:
  starting, risk_frac, max_exposure, brake = _config()
  try:
    closed = get_all_closed_trades(db_path)
  except Exception:
    closed = []  # missing/uninitialized journal → fresh bankroll
  return _build(starting, risk_frac, max_exposure, brake, closed)


def default_bankroll() -> Bankroll:
  """Bankroll as if the journal were empty — for callers without a db path."""
  starting, risk_frac, max_exposure, brake = _config()
  return _build(starting, risk_frac, max_exposure, brake, [])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. python3 -m pytest tabfm/trading/tests/test_bankroll.py -q`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add tabfm/trading/pipeline/bankroll.py tabfm/trading/tests/test_bankroll.py
git commit -m "feat(trading): bankroll module — tracked equity, drawdown brake, sizing limits"
```

---

### Task 2: Bankroll-driven sizing in select_trade

**Files:**
- Modify: `tabfm/trading/pipeline/trade_recommender.py` (full current content shown below — replace as specified)
- Test: replace `tabfm/trading/tests/test_trade_recommender.py` entirely with the content below

**Interfaces:**
- Consumes: `Bankroll`, `default_bankroll` from Task 1.
- Produces: `select_trade(scored_candidates, open_trades=None, bankroll: Bankroll | None = None)` — Task 3's `run_nightly` passes `bankroll=get_bankroll(db_path)`.

- [ ] **Step 1: Replace the test file**

Overwrite `tabfm/trading/tests/test_trade_recommender.py` with exactly:

```python
from tabfm.trading.pipeline.bankroll import Bankroll
from tabfm.trading.pipeline.trade_recommender import select_trade, _passes_filters

_GOOD = {
  "ticker": "SPY", "direction": "put_spread",
  "spread_width_dollars": 5.0, "entry_credit": 2.25,
  "strike_short": 480.0, "strike_long": 475.0, "expiry": "2026-08-21",
  "bid_ask_pct": 0.10, "open_interest": 200, "dte": 14, "short_delta": 0.25,
  "earnings_flag": "no_earnings", "pop_predicted": 0.72, "exp_return": 0.20,
}

_OPEN_SAME = {
  "ticker": "SPY", "direction": "put_spread",
  "strike_short": 480.0, "strike_long": 475.0, "expiry": "2026-08-21",
  "max_loss": 275.0,
}


def _bk(equity=2000.0, slice_frac=0.15, exposure_frac=0.45, recovery=False):
  frac = slice_frac * (0.5 if recovery else 1.0)
  return Bankroll(
    starting=2000.0, realized=equity - 2000.0, equity=equity,
    peak_equity=max(equity, 2000.0), drawdown_pct=0.0, recovery_mode=recovery,
    slice_limit=round(equity * frac, 2),
    exposure_limit=round(equity * exposure_frac, 2),
  )


# ---- filter gauntlet (unchanged checks) ----

def test_passes_filters_good_candidate():
  assert _passes_filters(_GOOD)


def test_filter_rejects_wide_bid_ask():
  assert not _passes_filters({**_GOOD, "bid_ask_pct": 0.20})


def test_filter_rejects_low_oi():
  assert not _passes_filters({**_GOOD, "open_interest": 50})


def test_filter_rejects_dte_out_of_range():
  assert not _passes_filters({**_GOOD, "dte": 3})
  assert not _passes_filters({**_GOOD, "dte": 60})


def test_filter_rejects_delta_out_of_range():
  assert not _passes_filters({**_GOOD, "short_delta": 0.05})
  assert not _passes_filters({**_GOOD, "short_delta": 0.50})


def test_filter_rejects_earnings_week():
  assert not _passes_filters({**_GOOD, "earnings_flag": "earnings_week"})


def test_wide_spread_no_longer_filtered_but_must_fit_slice():
  # Old static $1000 width check is gone: a 15-wide spread passes the gauntlet
  wide = {**_GOOD, "spread_width_dollars": 15.0, "entry_credit": 5.0}
  assert _passes_filters(wide)
  # ...but (15 - 5) * 100 = $1000/contract doesn't fit a $300 slice → skipped
  assert select_trade([wide], bankroll=_bk()) is None


# ---- selection ----

def test_select_trade_returns_best():
  candidates = [
    {**_GOOD, "pop_predicted": 0.60, "exp_return": 0.10, "ticker": "SPY"},
    {**_GOOD, "pop_predicted": 0.72, "exp_return": 0.20, "ticker": "TSLA"},
  ]
  best = select_trade(candidates, bankroll=_bk())
  assert best["ticker"] == "TSLA"


def test_select_trade_returns_none_when_all_fail_filters():
  assert select_trade([{**_GOOD, "dte": 2}], bankroll=_bk()) is None


def test_select_trade_skips_negative_ev():
  candidates = [{**_GOOD, "pop_predicted": 0.72, "exp_return": -0.10}]
  assert select_trade(candidates, bankroll=_bk()) is None


# ---- dedup ----

def test_dedup_skips_identical_open_position():
  assert select_trade([dict(_GOOD)], open_trades=[_OPEN_SAME], bankroll=_bk()) is None


def test_dedup_allows_different_strikes():
  different = {**_GOOD, "strike_short": 470.0, "strike_long": 465.0}
  best = select_trade([different], open_trades=[_OPEN_SAME], bankroll=_bk())
  assert best is not None
  assert best["strike_short"] == 470.0


# ---- bankroll sizing ----

def test_slice_sizes_one_contract_at_default_equity():
  # slice $300, (5 - 2.25) * 100 = $275/contract → exactly 1 contract
  best = select_trade([dict(_GOOD)], bankroll=_bk())
  assert best["contracts"] == 1
  assert best["total_risk"] == 500.0  # contracts * width * 100


def test_larger_equity_sizes_more_contracts():
  # equity 6000 → slice 900 → floor(900 / 275) = 3 contracts
  best = select_trade([dict(_GOOD)], bankroll=_bk(equity=6000.0))
  assert best["contracts"] == 3


def test_exposure_budget_blocks_when_book_full():
  # exposure 900; open risk 800 → budget min(300, 100) < 275 → no trade
  opens = [
    {**_OPEN_SAME, "strike_short": 470.0, "max_loss": 400.0},
    {**_OPEN_SAME, "strike_short": 460.0, "max_loss": 400.0},
  ]
  assert select_trade(
    [{**_GOOD, "strike_short": 450.0, "strike_long": 445.0}],
    open_trades=opens, bankroll=_bk(),
  ) is None


def test_recovery_mode_halves_slice():
  # recovery: slice 2000 * 0.075 = 150 < 275 → no trade even with empty book
  assert select_trade([dict(_GOOD)], bankroll=_bk(recovery=True)) is None


def test_max_contracts_cap_binds():
  # Huge equity, tiny per-contract loss: slice 15000, loss $75 → 200 raw, capped 10
  cheap = {**_GOOD, "spread_width_dollars": 1.0, "entry_credit": 0.25}
  best = select_trade([cheap], bankroll=_bk(equity=100_000.0))
  assert best["contracts"] == 10


def test_zero_budget_returns_none_without_scoring():
  bk = _bk(equity=0.0)
  bk = Bankroll(**{**bk.__dict__, "slice_limit": 0.0, "exposure_limit": 0.0})
  assert select_trade([dict(_GOOD)], bankroll=bk) is None


def test_legacy_none_bankroll_uses_defaults(monkeypatch):
  monkeypatch.delenv("TABFM_STARTING_CAPITAL", raising=False)
  best = select_trade([dict(_GOOD)])  # no bankroll arg
  assert best is not None
  assert best["contracts"] == 1  # default $2k equity → $300 slice → 1 contract
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `PYTHONPATH=. python3 -m pytest tabfm/trading/tests/test_trade_recommender.py -q`
Expected: FAIL — `TypeError: select_trade() got an unexpected keyword argument 'bankroll'` (and import of `Bankroll` succeeds only if Task 1 is merged).

- [ ] **Step 3: Rewrite trade_recommender.py**

The file currently starts with:

```python
import math
import os

_MAX_RISK = 1000.0
_MAX_CONTRACTS = 10
# Portfolio-level cap: total max loss across ALL open positions plus the new
# trade may not exceed this. Override with TABFM_MAX_PORTFOLIO_RISK.
_MAX_PORTFOLIO_RISK = float(os.environ.get("TABFM_MAX_PORTFOLIO_RISK", "1500"))
```

Replace the header with:

```python
import math

from .bankroll import Bankroll, default_bankroll

_MAX_CONTRACTS = 10
```

In `_passes_filters`, delete the first check (`if row["spread_width_dollars"] * 100 > _MAX_RISK: return False`); keep the remaining five checks exactly as they are.

Delete the `_contracts()` helper entirely.

Keep `_is_open_duplicate` unchanged. Replace `select_trade` with:

```python
def select_trade(
  scored_candidates: list[dict],
  open_trades: list[dict] | None = None,
  bankroll: Bankroll | None = None,
) -> dict | None:
  """Apply filter gauntlet and return the single highest expected-value trade.

  Sizing is bankroll-driven: each trade risks at most the per-trade slice,
  and total open max loss stays within the exposure limit. Candidates
  identical to an open position are skipped.
  """
  open_trades = open_trades or []
  if bankroll is None:
    bankroll = default_bankroll()

  open_risk = sum(float(t.get("max_loss") or 0) for t in open_trades)
  budget = min(bankroll.slice_limit, bankroll.exposure_limit - open_risk)
  if budget <= 0:
    return None

  survivors = [
    c for c in scored_candidates
    if _passes_filters(c) and not _is_open_duplicate(c, open_trades)
  ]
  if not survivors:
    return None

  sized = []
  for c in survivors:
    # True per-contract max loss; entry_credit may be absent in synthetic tests
    loss_per_contract = (c["spread_width_dollars"] - c.get("entry_credit", 0.0)) * 100
    if loss_per_contract <= 0:
      continue
    c["contracts"] = min(math.floor(budget / loss_per_contract), _MAX_CONTRACTS)
    if c["contracts"] < 1:
      continue  # doesn't fit the bankroll budget
    sized.append(c)
  survivors = sized
  if not survivors:
    return None

  for c in survivors:
    c["total_risk"] = c["contracts"] * c["spread_width_dollars"] * 100
    c["score"] = c["pop_predicted"] * c["exp_return"]

  # Candidates where TabFM gave a real (non-fallback) prediction
  tabfm_scored = [
    c for c in survivors
    if not (c["pop_predicted"] == 0.5 and c["exp_return"] == 0.0)
  ]
  if tabfm_scored:
    positive_ev = [c for c in tabfm_scored if c["score"] > 0]
    if not positive_ev:
      return None  # model has predictions and all are negative EV → skip
    return max(positive_ev, key=lambda c: c["score"])

  # Cold-start: no TabFM context yet → rank structurally by credit yield
  best = max(survivors, key=lambda c: c["entry_credit"] / c["spread_width_dollars"])
  best["score"] = round(best["entry_credit"] / best["spread_width_dollars"], 4)
  return best
```

- [ ] **Step 4: Run the affected tests, then the full suite**

Run: `PYTHONPATH=. python3 -m pytest tabfm/trading/tests/test_trade_recommender.py -q`
Expected: 19 passed

Run the full suite. Expected: ONE failure — `test_event_gate_integration.py::test_ungated_night_reaches_selection` (its stub spread's per-contract loss ≈ $444 exceeds the default $300 slice). That failure is fixed in Task 3; everything else must pass.

- [ ] **Step 5: Commit**

```bash
git add tabfm/trading/pipeline/trade_recommender.py tabfm/trading/tests/test_trade_recommender.py
git commit -m "feat(trading): bankroll-driven sizing replaces static risk caps"
```

---

### Task 3: Pipeline wiring, portfolio summary block, integration tests

**Files:**
- Modify: `tabfm/trading/run_nightly.py` (one import + one call site)
- Modify: `tabfm/trading/pipeline/portfolio.py` (full replacement below)
- Modify: `tabfm/trading/tests/test_event_gate_integration.py` (env fix for the ungated test)
- Test: `tabfm/trading/tests/test_bankroll_integration.py`

**Interfaces:**
- Consumes: `get_bankroll` (Task 1); `select_trade(..., bankroll=...)` (Task 2).
- Produces: every run's summary contains a `BANKROLL:` block; sizing in the live pipeline follows the journal's realized P&L.

- [ ] **Step 1: Write the failing integration test**

```python
# tabfm/trading/tests/test_bankroll_integration.py
from datetime import date

from tabfm.trading.pipeline.bankroll import get_bankroll
from tabfm.trading.pipeline.portfolio import portfolio_summary
from tabfm.trading.store.journal import init_db, insert_trade, close_trade


def _trade():
  return dict(
    date_entered="2026-07-01", ticker="SPY", direction="put_spread",
    strike_short=700.0, strike_long=695.0, expiry="2026-07-18", dte=17,
    entry_credit=2.0, spread_width=5.0, contracts=1, max_loss=300.0,
    max_profit=200.0, pop_predicted=0.6, pop_raw=0.6, exp_return=0.2,
    regime="normal|sideways|fair",
  )


def test_summary_contains_bankroll_block(tmp_path):
  db = tmp_path / "j.db"
  init_db(db)
  tid = insert_trade(_trade(), db)
  close_trade(tid, "lost", -300.0, "2026-07-20", db)
  out = portfolio_summary(db, as_of=date(2026, 7, 24))
  assert "BANKROLL" in out
  assert "1,700" in out          # equity after the loss
  assert "NORMAL" in out         # 15% drawdown < 25% brake
  assert "Slice $255.00" in out  # 1700 * 0.15


def test_summary_shows_recovery_mode(tmp_path):
  db = tmp_path / "j.db"
  init_db(db)
  for pnl, day in [(200.0, "10"), (200.0, "11"), (-300.0, "12"),
                   (-300.0, "13"), (-100.0, "14")]:
    tid = insert_trade(_trade(), db)
    close_trade(tid, "won" if pnl > 0 else "lost", pnl, f"2026-07-{day}", db)
  out = portfolio_summary(db, as_of=date(2026, 7, 24))
  assert "RECOVERY" in out
  assert get_bankroll(db).recovery_mode is True
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=. python3 -m pytest tabfm/trading/tests/test_bankroll_integration.py -q`
Expected: FAIL — "BANKROLL" not in the summary output.

- [ ] **Step 3: Implement**

Overwrite `tabfm/trading/pipeline/portfolio.py` with:

```python
"""End-of-run portfolio summary: bankroll, open book, closed history, P&L."""
from datetime import date
from pathlib import Path

from .bankroll import get_bankroll
from ..store.journal import get_open_trades, get_all_closed_trades, _DEFAULT_DB


def portfolio_summary(db_path: Path = _DEFAULT_DB, as_of: date | None = None) -> str:
  opens = get_open_trades(db_path)
  closed = get_all_closed_trades(db_path)
  bk = get_bankroll(db_path)

  open_risk = sum(float(t["max_loss"] or 0) for t in opens)
  open_profit = sum(float(t["max_profit"] or 0) for t in opens)

  lines = []
  lines.append("╔══════════════════════════════════════════════════════╗")
  lines.append("  PORTFOLIO SUMMARY" + (f"  ·  {as_of}" if as_of else ""))
  lines.append("╠══════════════════════════════════════════════════════╣")

  mode = "RECOVERY (slice halved)" if bk.recovery_mode else "NORMAL"
  free = max(bk.exposure_limit - open_risk, 0.0)
  lines.append(f"  BANKROLL: equity ${bk.equity:,.2f}  (start ${bk.starting:,.0f} · realized ${bk.realized:,.2f})")
  lines.append(f"  Peak ${bk.peak_equity:,.2f} · drawdown {bk.drawdown_pct * 100:.1f}% · mode {mode}")
  lines.append(f"  Slice ${bk.slice_limit:,.2f} · exposure cap ${bk.exposure_limit:,.2f} (${open_risk:,.2f} open · ${free:,.2f} free)")
  lines.append("  ──────────────────────────────────────────────────────")

  lines.append(f"  OPEN POSITIONS ({len(opens)})")
  for t in opens:
    dte = ""
    if as_of is not None:
      try:
        dte = f"  ·  {(date.fromisoformat(str(t['expiry'])) - as_of).days}d left"
      except ValueError:
        pass
    lines.append(
      f"    #{t['trade_id']} {t['ticker']} {t['direction'].replace('_', ' ')} "
      f"{t['strike_short']:g}/{t['strike_long']:g} exp {t['expiry']} "
      f"x{t['contracts']}  risk ${t['max_loss']:.0f}{dte}"
    )
  if not opens:
    lines.append("    (none)")

  lines.append("  ──────────────────────────────────────────────────────")
  wins = sum(1 for t in closed if t["status"] in ("won", "partial"))
  losses = sum(1 for t in closed if t["status"] == "lost")
  realized = sum(float(t["actual_pnl"] or 0) for t in closed)
  lines.append(f"  CLOSED: {len(closed)}  ({wins}W / {losses}L)"
               + (f"  ·  win rate {wins / len(closed) * 100:.0f}%" if closed else ""))
  lines.append(f"  Realized P&L:     ${realized:,.2f}")
  lines.append(f"  Open max risk:    ${open_risk:,.2f}")
  lines.append(f"  Open max profit:  ${open_profit:,.2f}")
  if closed:
    avg_pop = sum(float(t["pop_predicted"] or 0) for t in closed) / len(closed)
    lines.append(f"  Avg POP (closed): {avg_pop * 100:.1f}%  vs realized {wins / len(closed) * 100:.1f}%")
  lines.append("╚══════════════════════════════════════════════════════╝")
  return "\n".join(lines)
```

In `tabfm/trading/run_nightly.py`:
- Add to the imports (next to the other pipeline imports):
  ```python
  from .pipeline.bankroll import get_bankroll
  ```
- Change the selection call from
  ```python
  best = select_trade(all_candidates, open_trades=get_open_trades(db_path))
  ```
  to
  ```python
  best = select_trade(
    all_candidates,
    open_trades=get_open_trades(db_path),
    bankroll=get_bankroll(db_path),
  )
  ```

In `tabfm/trading/tests/test_event_gate_integration.py`, in `test_ungated_night_reaches_selection`, add as the first line of the test body:

```python
  monkeypatch.setenv("TABFM_STARTING_CAPITAL", "5000")
```

(The stub spread's per-contract loss is ≈ $444; at $5,000 equity the slice is $750, so the cold-start pick still places its 1-contract trade. The test's signature already accepts `monkeypatch`.)

- [ ] **Step 4: Run the integration tests, then the full suite**

Run: `PYTHONPATH=. python3 -m pytest tabfm/trading/tests/test_bankroll_integration.py tabfm/trading/tests/test_event_gate_integration.py -q`
Expected: all pass.

Run the full suite. Expected: all pass (97 baseline − 2 removed + 3 new recommender + 9 bankroll + 2 integration ≈ 109; exact count may differ by one—what matters is zero failures).

- [ ] **Step 5: Commit**

```bash
git add tabfm/trading/run_nightly.py tabfm/trading/pipeline/portfolio.py tabfm/trading/tests/test_bankroll_integration.py tabfm/trading/tests/test_event_gate_integration.py
git commit -m "feat(trading): wire bankroll into nightly sizing and portfolio summary"
```

---

## Self-Review

- **Spec coverage:** Bankroll dataclass + equity walk + peak + recovery + floor + env config (Task 1) ✓; static caps and `_contracts` removed, gauntlet width check removed, budget sizing with `_MAX_CONTRACTS`, `bankroll=None` legacy path (Task 2) ✓; run_nightly wiring + summary block on all paths (portfolio_summary is called on trade/no-trade/gated paths already) (Task 3) ✓; behavior consequences need no code; book reset already done pre-plan (`8dff93b`) ✓.
- **Placeholders:** none — full file contents or exact replacement snippets everywhere.
- **Type consistency:** `Bankroll` field names match across all three tasks (`slice_limit`, `exposure_limit`, `recovery_mode`); `select_trade` keyword `bankroll=` matches Task 3's call; test helper `_bk()` constructs the Task 1 dataclass with all eight fields.
- **Known interaction check:** `test_ungated_night_reaches_selection` breakage is anticipated in Task 2 Step 4 and fixed in Task 3 — tasks are ordered so the suite is only transiently red between the two commits, with the failure explicitly documented.
