# Trade Management v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Loss-side exits (2× credit stop, 21-DTE management, MFE/MAE tracking), entry-quality gates (credit floor, IV-rank minimum), and a market-POP benchmark column with Brier comparison.

**Architecture:** All exits live in `position_auditor` (which already marks spreads to real mids); entry gates join the existing filter gauntlet; `pop_market` flows snapshot → feature row → journal → accuracy tracker. New journal columns via the established `PRAGMA table_info` migration pattern.

**Tech Stack:** Python 3.14, sqlite3, pandas, pytest. No new dependencies.

## Global Constraints

- Paper trading only.
- Env config, exact defaults, read at call time: `TABFM_STOP_LOSS_MULT=2.0`, `TABFM_MANAGE_DTE=21`, `TABFM_MIN_CREDIT_RATIO=0.30`, `TABFM_MIN_IV_RANK=30.0`.
- Stop-loss and 21-DTE rules fire ONLY on real marks (`_spread_mark` not None) — the intrinsic fallback must never trigger them. Profit target keeps its existing behavior (fires on either valuation).
- Auditor evaluation order per open trade: expiry settlement → stop-loss → profit target → 21-DTE.
- New status `"stopped"` counts as a loss everywhere (`accuracy_tracker`, `portfolio_summary`); calibrator needs no change (outcome 1 only for won/partial).
- `pop_market` is NOT added to `FEATURE_COLS`.
- Missing keys are neutral: `_passes_filters` uses `.get("iv_rank", 50.0)` and skips the credit-ratio check when `entry_credit` is absent — existing fixtures must keep passing without modification.
- 2-space indent; no Co-Authored-By / Claude / Anthropic commit trailers.
- Suite baseline: 114 tests. Run: `PYTHONPATH=. python3 -m pytest tabfm/trading/tests/ -q --ignore=tabfm/trading/tests/test_hist_adapter.py --ignore=tabfm/trading/tests/test_live_adapter.py --ignore=tabfm/trading/tests/test_run_nightly.py`
- Paths relative to `/Users/alberto2perez/src/tabfm-options-trader`.

---

### Task 1: Journal columns + loss-side exits + status consumers

**Files:**
- Modify: `tabfm/trading/store/journal.py`
- Modify: `tabfm/trading/pipeline/position_auditor.py`
- Modify: `tabfm/trading/pipeline/accuracy_tracker.py`
- Modify: `tabfm/trading/pipeline/portfolio.py`
- Test: `tabfm/trading/tests/test_position_auditor_exits.py`

**Interfaces:**
- Consumes: existing `_spread_mark`, `close_trade`, `get_open_trades`.
- Produces: journal columns `pop_market REAL`, `mfe REAL`, `mae REAL` (+ migration); `update_excursions(trade_id: int, mfe: float, mae: float, path: Path = _DEFAULT_DB) -> None`; status `"stopped"`; `insert_trade` persists `pop_market` (used by Task 3).

- [ ] **Step 1: Write the failing tests**

```python
# tabfm/trading/tests/test_position_auditor_exits.py
import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd

from tabfm.trading.pipeline.position_auditor import audit_positions
from tabfm.trading.store.journal import init_db, insert_trade, get_open_trades

AS_OF = date(2026, 7, 24)


class _MarkAdapter:
  """Adapter stub returning a fixed spread mark via its options chain."""

  def __init__(self, short_mid: float, long_mid: float, underlying: float = 700.0):
    self.short_mid = short_mid
    self.long_mid = long_mid
    self.underlying = underlying

  def get_underlying(self, ticker, as_of):
    return {"close": self.underlying}

  def get_options_chain(self, ticker, as_of):
    return pd.DataFrame([
      {"strike": 680.0, "expiry": pd.Timestamp("2026-08-21"), "option_type": "put",
       "mid": self.short_mid, "bid": self.short_mid - 0.02, "ask": self.short_mid + 0.02,
       "open_interest": 500, "delta": 0.3, "iv": 0.2, "dte": 28},
      {"strike": 675.0, "expiry": pd.Timestamp("2026-08-21"), "option_type": "put",
       "mid": self.long_mid, "bid": self.long_mid - 0.02, "ask": self.long_mid + 0.02,
       "open_interest": 500, "delta": 0.25, "iv": 0.2, "dte": 28},
    ])

  def get_vix(self, as_of):
    return 18.0


class _NoChainAdapter(_MarkAdapter):
  def __init__(self, underlying: float = 700.0):
    super().__init__(0.0, 0.0, underlying)

  def get_options_chain(self, ticker, as_of):
    return pd.DataFrame()


def _open_trade(db, credit=2.0, expiry="2026-08-21"):
  init_db(db)
  return insert_trade(dict(
    date_entered="2026-07-20", ticker="SPY", direction="put_spread",
    strike_short=680.0, strike_long=675.0, expiry=expiry, dte=28,
    entry_credit=credit, spread_width=5.0, contracts=1, max_loss=300.0,
    max_profit=200.0, pop_predicted=0.7, pop_raw=0.7, exp_return=0.2,
    regime="normal|sideways|fair",
  ), db)


def _status(db, tid):
  conn = sqlite3.connect(db)
  conn.row_factory = sqlite3.Row
  return dict(conn.execute(
    "SELECT * FROM paper_trades WHERE trade_id=?", (tid,)).fetchone())


def test_stop_loss_fires_at_double_credit(tmp_path):
  db = tmp_path / "j.db"
  tid = _open_trade(db, credit=2.0)
  # spread mark = 4.5 - 0.4 = 4.1 >= 2.0 * 2.0 -> stop
  closed = audit_positions(_MarkAdapter(4.5, 0.4), AS_OF, db)
  assert len(closed) == 1
  row = _status(db, tid)
  assert row["status"] == "stopped"
  assert row["actual_pnl"] == -210.0  # (2.0 - 4.1) * 1 * 100


def test_stop_does_not_fire_below_multiple(tmp_path):
  db = tmp_path / "j.db"
  tid = _open_trade(db, credit=2.0)
  # mark = 3.0 - 0.2 = 2.8 < 4.0 -> stays open (and profit target not hit)
  audit_positions(_MarkAdapter(3.0, 0.2), AS_OF, db)
  assert _status(db, tid)["status"] == "open"


def test_stop_never_fires_on_intrinsic_fallback(tmp_path):
  db = tmp_path / "j.db"
  tid = _open_trade(db, credit=2.0)
  # No chain: deep-ITM intrinsic would look like a huge loss, but the rule
  # requires a real mark. Underlying 650 -> intrinsic 5.0 (max loss zone).
  audit_positions(_NoChainAdapter(underlying=650.0), AS_OF, db)
  assert _status(db, tid)["status"] == "open"


def test_dte_management_closes_profitable_as_partial(tmp_path):
  db = tmp_path / "j.db"
  tid = _open_trade(db, credit=2.0, expiry="2026-08-07")  # 14 DTE from AS_OF
  # mark = 1.2 - 0.2 = 1.0 -> unrealized +100 (50% of 200 -> profit target
  # would also fire; set mark so it's below target): 1.6 - 0.3 = 1.3 -> +70
  closed = audit_positions(_MarkAdapter(1.6, 0.3), AS_OF, db)
  row = _status(db, tid)
  assert row["status"] == "partial"
  assert row["actual_pnl"] == 70.0


def test_dte_management_closes_loser_as_stopped(tmp_path):
  db = tmp_path / "j.db"
  tid = _open_trade(db, credit=2.0, expiry="2026-08-07")
  # mark = 2.8 - 0.3 = 2.5 -> unrealized -50, below stop multiple, 14 DTE
  audit_positions(_MarkAdapter(2.8, 0.3), AS_OF, db)
  row = _status(db, tid)
  assert row["status"] == "stopped"
  assert row["actual_pnl"] == -50.0


def test_dte_rule_respects_env_override(tmp_path, monkeypatch):
  monkeypatch.setenv("TABFM_MANAGE_DTE", "5")
  db = tmp_path / "j.db"
  tid = _open_trade(db, credit=2.0, expiry="2026-08-07")  # 14 DTE > 5
  audit_positions(_MarkAdapter(1.6, 0.3), AS_OF, db)
  assert _status(db, tid)["status"] == "open"


def test_excursions_tracked_and_widen_only(tmp_path):
  db = tmp_path / "j.db"
  tid = _open_trade(db, credit=2.0)
  audit_positions(_MarkAdapter(2.4, 0.2), AS_OF, db)   # mark 2.2 -> -20
  row = _status(db, tid)
  assert row["mae"] == -20.0 and row["mfe"] == -20.0
  audit_positions(_MarkAdapter(1.4, 0.2), AS_OF, db)   # mark 1.2 -> +80
  row = _status(db, tid)
  assert row["mfe"] == 80.0 and row["mae"] == -20.0    # mae must not shrink


def test_stopped_counts_as_loss_in_summary(tmp_path):
  from tabfm.trading.pipeline.portfolio import portfolio_summary
  db = tmp_path / "j.db"
  _open_trade(db, credit=2.0)
  audit_positions(_MarkAdapter(4.5, 0.4), AS_OF, db)  # -> stopped
  out = portfolio_summary(db, as_of=AS_OF)
  assert "(0W / 1L)" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. python3 -m pytest tabfm/trading/tests/test_position_auditor_exits.py -q`
Expected: failures (no `stopped` status, no `mfe`/`mae` columns).

- [ ] **Step 3: Implement journal changes**

In `tabfm/trading/store/journal.py`:
- In `_SCHEMA`, after the `pop_raw       REAL,` line add:
```python
  pop_market    REAL,
  mfe           REAL,
  mae           REAL,
```
- In `init_db`'s migration block, extend to:
```python
    cols = {r[1] for r in conn.execute("PRAGMA table_info(paper_trades)")}
    for col in ("pop_raw", "pop_market", "mfe", "mae"):
      if col not in cols:
        conn.execute(f"ALTER TABLE paper_trades ADD COLUMN {col} REAL")
```
- In `insert_trade`, add `pop_market` to the column list and VALUES
  (`:pop_market`), and change the params dict to
  `{**trade, "pop_raw": trade.get("pop_raw"), "pop_market": trade.get("pop_market")}`.
- Add:
```python
def update_excursions(
  trade_id: int, mfe: float, mae: float, path: Path = _DEFAULT_DB
) -> None:
  with sqlite3.connect(path) as conn:
    conn.execute(
      "UPDATE paper_trades SET mfe=?, mae=? WHERE trade_id=?",
      (mfe, mae, trade_id),
    )
```

- [ ] **Step 4: Implement the auditor exits**

In `tabfm/trading/pipeline/position_auditor.py`: add `import os` at the top,
import `update_excursions` from the journal module, and replace the body of
the `for trade in open_trades:` loop from `S = underlying["close"]` to the
end of the loop with:

```python
    S = underlying["close"]
    credit = trade["entry_credit"]
    width = trade["spread_width"]
    contracts = trade["contracts"]

    # 1. Expiry settlement
    if as_of >= expiry:
      if _is_winner(trade, S):
        pnl = round(credit * contracts * 100, 2)
        status = "won"
      else:
        pnl = round(-(width - credit) * contracts * 100, 2)
        status = "lost"
      close_trade(trade["trade_id"], status, pnl, str(as_of), db_path)
      closed.append({**trade, "status": status, "actual_pnl": pnl})
      continue

    mark = _spread_mark(adapter, trade, as_of)
    current_val = mark if mark is not None else _estimate_current_value(trade, S)
    unrealized = (credit - current_val) * contracts * 100
    max_profit = credit * contracts * 100

    # Track excursions from real marks only (intrinsic has no time value)
    if mark is not None:
      prev_mfe = trade["mfe"] if trade.get("mfe") is not None else unrealized
      prev_mae = trade["mae"] if trade.get("mae") is not None else unrealized
      update_excursions(
        trade["trade_id"],
        round(max(prev_mfe, unrealized), 2),
        round(min(prev_mae, unrealized), 2),
        db_path,
      )

    # 2. Stop-loss: closing cost reached stop_mult x credit (real marks only)
    stop_mult = float(os.environ.get("TABFM_STOP_LOSS_MULT", "2.0"))
    if mark is not None and mark >= stop_mult * credit:
      pnl = round(unrealized, 2)
      close_trade(trade["trade_id"], "stopped", pnl, str(as_of), db_path)
      closed.append({**trade, "status": "stopped", "actual_pnl": pnl})
      continue

    # 3. Profit target (existing behavior, either valuation)
    if unrealized >= max_profit * _EARLY_CLOSE_THRESHOLD:
      close_trade(trade["trade_id"], "partial", round(unrealized, 2), str(as_of), db_path)
      closed.append({**trade, "status": "partial", "actual_pnl": round(unrealized, 2)})
      continue

    # 4. DTE management: exit the gamma zone (real marks only)
    manage_dte = int(os.environ.get("TABFM_MANAGE_DTE", "21"))
    if mark is not None and (expiry - as_of).days <= manage_dte:
      pnl = round(unrealized, 2)
      status = "partial" if pnl >= 0 else "stopped"
      close_trade(trade["trade_id"], status, pnl, str(as_of), db_path)
      closed.append({**trade, "status": status, "actual_pnl": pnl})
```

Note: `trade` comes from `get_open_trades` which returns plain dicts —
`trade.get("mfe")` works; after the Task 1 migration the keys always exist.
Update the module docstring/function docstring to mention the four rules.

- [ ] **Step 5: Status consumers**

`tabfm/trading/pipeline/accuracy_tracker.py`: change
`losses = sum(1 for t in trades if t["status"] == "lost")` to
`losses = sum(1 for t in trades if t["status"] in ("lost", "stopped"))`.

`tabfm/trading/pipeline/portfolio.py`: change
`losses = sum(1 for t in closed if t["status"] == "lost")` to
`losses = sum(1 for t in closed if t["status"] in ("lost", "stopped"))`.

- [ ] **Step 6: Run tests, then full suite**

Run: `PYTHONPATH=. python3 -m pytest tabfm/trading/tests/test_position_auditor_exits.py -q`
Expected: 8 passed.
Full suite: all pass.

- [ ] **Step 7: Commit**

```bash
git add tabfm/trading/store/journal.py tabfm/trading/pipeline/position_auditor.py tabfm/trading/pipeline/accuracy_tracker.py tabfm/trading/pipeline/portfolio.py tabfm/trading/tests/test_position_auditor_exits.py
git commit -m "feat(trading): loss-side exits — 2x credit stop, DTE management, MFE/MAE tracking"
```

---

### Task 2: Entry-quality gauntlet checks

**Files:**
- Modify: `tabfm/trading/pipeline/trade_recommender.py` (`_passes_filters`)
- Test: append to `tabfm/trading/tests/test_trade_recommender.py`

**Interfaces:** none new — two additional checks inside `_passes_filters`.

- [ ] **Step 1: Add failing tests**

Append to `tabfm/trading/tests/test_trade_recommender.py`:

```python
# ---- entry-quality gates ----

def test_filter_rejects_thin_credit():
  thin = {**_GOOD, "entry_credit": 1.40}  # 1.40 / 5.0 = 0.28 < 0.30
  assert not _passes_filters(thin)


def test_filter_accepts_adequate_credit():
  ok = {**_GOOD, "entry_credit": 1.55}  # 0.31
  assert _passes_filters(ok)


def test_filter_skips_credit_check_when_absent():
  no_credit = {k: v for k, v in _GOOD.items() if k != "entry_credit"}
  assert _passes_filters(no_credit)


def test_filter_rejects_cheap_iv():
  assert not _passes_filters({**_GOOD, "iv_rank": 29.0})


def test_filter_accepts_rich_iv():
  assert _passes_filters({**_GOOD, "iv_rank": 31.0})


def test_filter_iv_defaults_neutral_when_absent():
  assert _passes_filters(_GOOD)  # fixture has no iv_rank key -> neutral 50


def test_entry_gate_env_overrides(monkeypatch):
  monkeypatch.setenv("TABFM_MIN_CREDIT_RATIO", "0.50")
  assert not _passes_filters(dict(_GOOD))  # 0.45 < 0.50 now
  monkeypatch.setenv("TABFM_MIN_CREDIT_RATIO", "0.30")
  monkeypatch.setenv("TABFM_MIN_IV_RANK", "60")
  assert not _passes_filters({**_GOOD, "iv_rank": 50.0})
```

- [ ] **Step 2: Verify they fail**

Run: `PYTHONPATH=. python3 -m pytest tabfm/trading/tests/test_trade_recommender.py -q`
Expected: the new tests fail; existing pass.

- [ ] **Step 3: Implement**

In `tabfm/trading/pipeline/trade_recommender.py`, add `import os` at the
top, and append to `_passes_filters` (before `return True`):

```python
  # Entry quality: enough credit for the width, and only sell rich premium.
  credit = row.get("entry_credit")
  if credit is not None:
    min_ratio = float(os.environ.get("TABFM_MIN_CREDIT_RATIO", "0.30"))
    if credit / row["spread_width_dollars"] < min_ratio:
      return False
  min_iv_rank = float(os.environ.get("TABFM_MIN_IV_RANK", "30.0"))
  if float(row.get("iv_rank", 50.0)) < min_iv_rank:
    return False
```

Also extend `tabfm/trading/tests/conftest.py`'s autouse fixture var list with
`"TABFM_MIN_CREDIT_RATIO", "TABFM_MIN_IV_RANK"` (env hygiene).

- [ ] **Step 4: Run the file's tests, then full suite**

Expected: 26 pass in the file; full suite green. If any pre-existing test in
OTHER files fails on the new checks (candidates missing `iv_rank` are
neutral, so only thin-credit fixtures could trip — e.g., an integration stub
whose credit ratio < 0.30), adjust that fixture's `entry_credit` upward with
a comment rather than weakening the gate.

- [ ] **Step 5: Commit**

```bash
git add tabfm/trading/pipeline/trade_recommender.py tabfm/trading/tests/test_trade_recommender.py tabfm/trading/tests/conftest.py
git commit -m "feat(trading): entry-quality gates — credit/width floor and IV-rank minimum"
```

---

### Task 3: pop_market benchmark plumbing

**Files:**
- Modify: `tabfm/trading/pipeline/feature_engineer.py` (row dict)
- Modify: `tabfm/trading/pipeline/paper_executor.py` (record dict)
- Modify: `tabfm/trading/pipeline/accuracy_tracker.py` (Brier section)
- Modify: `docs/NIGHTLY_CLOUD_RUN.md` (fetch mapping)
- Test: `tabfm/trading/tests/test_pop_market.py`

**Interfaces:**
- Consumes: journal `pop_market` column + `insert_trade` persistence (Task 1).
- Produces: rows/journal carry `pop_market`; `report()` returns
  `brier_tabfm`, `brier_market`, `brier_n` when any closed trade has
  `pop_market`.

- [ ] **Step 1: Write the failing tests**

```python
# tabfm/trading/tests/test_pop_market.py
from datetime import date, timedelta

import pandas as pd

from tabfm.trading.pipeline.accuracy_tracker import report
from tabfm.trading.pipeline.feature_engineer import engineer_features
from tabfm.trading.store.journal import init_db, insert_trade, close_trade

AS_OF = date(2026, 7, 24)


def _chain(with_pop: bool) -> pd.DataFrame:
  rows = []
  for i, strike in enumerate([95.0, 100.0]):
    r = {
      "strike": strike, "expiry": AS_OF + timedelta(days=14),
      "option_type": "put", "bid": 1.4 + i, "ask": 1.6 + i,
      "mid": 1.5 + i, "open_interest": 300, "delta": 0.20 + i * 0.1,
      "iv": 0.22, "dte": 14,
    }
    if with_pop:
      r["pop_market"] = 0.75 - i * 0.05
    rows.append(r)
  return pd.DataFrame(rows)


def _chain_data(with_pop: bool) -> dict:
  return {
    "ticker": "SPY", "sector": "index_etf", "vix": 18.5,
    "chain": _chain(with_pop),
    "underlying": {
      "close": 100.0, "sma20": 98.0, "sma50": 95.0, "atr14": 1.5,
      "hv20": 0.18, "volume": 5e7, "volume_zscore": 0.4,
      "momentum_5d": 0.01, "momentum_20d": 0.03,
      "rsi_14": 55.0, "macd_line": 0.5, "macd_signal": 0.3, "macd_histogram": 0.2,
    },
  }


def test_pop_market_copied_from_short_leg():
  rows = engineer_features(_chain_data(with_pop=True), AS_OF, iv_rank=55.0)
  assert rows, "fixture must yield a candidate"
  # short leg is the 100-strike (delta 0.30) -> pop_market 0.70
  assert rows[0]["pop_market"] == 0.70


def test_pop_market_none_when_chain_lacks_it():
  rows = engineer_features(_chain_data(with_pop=False), AS_OF, iv_rank=55.0)
  assert rows[0]["pop_market"] is None


def _closed_trade(pop_pred, pop_mkt, status):
  return dict(
    date_entered="2026-07-01", ticker="SPY", direction="put_spread",
    strike_short=700.0, strike_long=695.0, expiry="2026-07-18", dte=17,
    entry_credit=2.0, spread_width=5.0, contracts=1, max_loss=300.0,
    max_profit=200.0, pop_predicted=pop_pred, pop_raw=pop_pred,
    pop_market=pop_mkt, exp_return=0.2, regime="normal|sideways|fair",
  ), status


def test_brier_comparison_in_report(tmp_path):
  db = tmp_path / "j.db"
  init_db(db)
  for (trade, status), pnl in [
    (_closed_trade(0.8, 0.7, "won"), 200.0),
    (_closed_trade(0.6, 0.7, "lost"), -300.0),
    (_closed_trade(0.9, 0.8, "partial"), 100.0),
  ]:
    tid = insert_trade(trade, db)
    close_trade(tid, status, pnl, "2026-07-18", db)
  m = report(db_path=db, verbose=False)
  # outcomes: 1, 0, 1
  # tabfm: ((0.8-1)^2 + (0.6-0)^2 + (0.9-1)^2) / 3 = (0.04+0.36+0.01)/3
  assert m["brier_tabfm"] == round((0.04 + 0.36 + 0.01) / 3, 4)
  # market: ((0.7-1)^2 + (0.7-0)^2 + (0.8-1)^2) / 3 = (0.09+0.49+0.04)/3
  assert m["brier_market"] == round((0.09 + 0.49 + 0.04) / 3, 4)
  assert m["brier_n"] == 3


def test_brier_absent_without_pop_market(tmp_path):
  db = tmp_path / "j.db"
  init_db(db)
  trade, status = _closed_trade(0.8, None, "won")
  tid = insert_trade(trade, db)
  close_trade(tid, status, 200.0, "2026-07-18", db)
  m = report(db_path=db, verbose=False)
  assert "brier_tabfm" not in m
```

- [ ] **Step 2: Verify they fail**

Run: `PYTHONPATH=. python3 -m pytest tabfm/trading/tests/test_pop_market.py -q`
Expected: failures (`pop_market` KeyError in rows; no brier keys).

- [ ] **Step 3: Implement**

`tabfm/trading/pipeline/feature_engineer.py` — in the row dict, after the
`"earnings_flag": "no_earnings",` line add:

```python
          "pop_market": (
            float(short["pop_market"])
            if "pop_market" in short.index and pd.notna(short.get("pop_market"))
            else None
          ),
```

`tabfm/trading/pipeline/paper_executor.py` — in the `record` dict, after
`"pop_raw": ...` add:

```python
    "pop_market": trade.get("pop_market"),
```

`tabfm/trading/pipeline/accuracy_tracker.py` — after the `metrics = {...}`
dict is built and before the `if verbose:` block, add:

```python
  benchmarked = [t for t in trades if t.get("pop_market") is not None]
  if benchmarked:
    def _brier(key: str) -> float:
      return sum(
        (float(t[key]) - (1 if t["status"] in ("won", "partial") else 0)) ** 2
        for t in benchmarked
      ) / len(benchmarked)
    metrics["brier_tabfm"] = round(_brier("pop_predicted"), 4)
    metrics["brier_market"] = round(_brier("pop_market"), 4)
    metrics["brier_n"] = len(benchmarked)
```

and inside the verbose print block (before the closing box line) add:

```python
  if "brier_tabfm" in metrics and verbose:
    print(f"  Brier (lower=better): TabFM {metrics['brier_tabfm']:.4f} vs "
          f"market {metrics['brier_market']:.4f}  (n={metrics['brier_n']})")
```

(Adapt placement to the existing f-string box: a plain extra print after the
box is acceptable — keep it simple.)

`docs/NIGHTLY_CLOUD_RUN.md` — in the option-quote fetch bullet, extend the
row mapping sentence with: chain rows must also carry
`"pop_market": <float chance_of_profit_short>` from the SHORT-capable quote
fields (`quote.chance_of_profit_short`); omit the key when the field is
null.

- [ ] **Step 4: Run tests, full suite**

Expected: 4 pass in the new file; full suite green.

- [ ] **Step 5: Commit**

```bash
git add tabfm/trading/pipeline/feature_engineer.py tabfm/trading/pipeline/paper_executor.py tabfm/trading/pipeline/accuracy_tracker.py docs/NIGHTLY_CLOUD_RUN.md tabfm/trading/tests/test_pop_market.py
git commit -m "feat(trading): market-POP benchmark column with Brier comparison"
```

---

## Self-Review

- **Spec coverage:** stop-loss/21-DTE/MFE-MAE + stopped status + consumers (Task 1) ✓; credit floor + IV gate with neutral defaults (Task 2) ✓; pop_market snapshot→row→journal→Brier + docs (Task 3) ✓; config table values match spec ✓; real-marks-only guard on stop/DTE rules ✓.
- **Placeholders:** none; every step has code. The one adaptive instruction (Task 2 Step 4 fixture note; Task 3 print placement) states the invariant to preserve.
- **Type consistency:** `update_excursions` signature matches auditor call; `pop_market` key name identical across engineer/executor/journal/tracker/tests; `stopped` literal identical across auditor/tracker/portfolio/tests.
- **Interaction check:** Task 1's dte-management test uses expiry 2026-08-07 (14 days from AS_OF 2026-07-24) with default TABFM_MANAGE_DTE=21 → fires; env-override test sets 5 → doesn't. Profit-target precedence verified by choosing marks where unrealized < 50% of max profit. `_GOOD` fixture (credit ratio 0.45, no iv_rank key) passes both new gates unchanged.
