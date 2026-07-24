# Vol Skew + Trend Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add per-strike volatility skew to the synthetic backtest chain, and a trend-guard advisory that tells the user what to do with open positions when the trend turns against them.

**Architecture:** Skew is a per-strike sigma in `_synthetic_chain`. Trend guard is a read-only `pipeline/trend_guard.py` reusing `_spread_mark` (real marks) and `_trend_direction`, wired into the nightly and midday runs as an advisory only.

**Tech Stack:** Python 3.14, pandas, pytest. No new dependencies.

## Global Constraints

- Paper trading only; no order-placement APIs.
- Env config, read at call time: `TABFM_BACKTEST_SKEW=2.5`, `TABFM_TREND_GUARD=on`.
- Trend guard is ADVISORY ONLY — it never closes or modifies a position; it prints and logs recommendations. Guards the MODEL book only (`strategy="model"`).
- Skew: `sigma_K = max(base_sigma * max(1 + slope*(1 - K/S), 0.4), 0.05)`; `sigma_K` drives price, delta, and the written `iv`.
- Add `TABFM_BACKTEST_SKEW`, `TABFM_TREND_GUARD` to `tabfm/trading/tests/conftest.py` autouse delenv list.
- 2-space indent; no Co-Authored-By / Claude / Anthropic commit trailers.
- Suite baseline: 169 tests. Run: `PYTHONPATH=. python3 -m pytest tabfm/trading/tests/ -q --ignore=tabfm/trading/tests/test_hist_adapter.py --ignore=tabfm/trading/tests/test_live_adapter.py --ignore=tabfm/trading/tests/test_run_nightly.py`
- New test files must not hit the network (use `_synthetic_chain` directly / stub adapters).
- Paths relative to `/Users/alberto2perez/src/tabfm-options-trader`.

---

### Task 1: Volatility skew in the synthetic chain

**Files:**
- Modify: `tabfm/trading/adapters/historical.py`
- Modify: `tabfm/trading/tests/conftest.py`
- Test: `tabfm/trading/tests/test_vol_skew.py`

**Interfaces:**
- Produces: module-level `_skew_slope() -> float`; `_synthetic_chain` now writes per-strike IV.

- [ ] **Step 1: Write the failing tests**

```python
# tabfm/trading/tests/test_vol_skew.py
from datetime import date

from tabfm.trading.adapters.historical import _synthetic_chain, _skew_slope
from tabfm.trading.pipeline.feature_engineer import engineer_features
from tabfm.trading.pipeline.trade_recommender import _passes_filters

AS_OF = date(2026, 7, 24)


def test_skew_slope_default_and_override(monkeypatch):
  assert _skew_slope() == 2.5
  monkeypatch.setenv("TABFM_BACKTEST_SKEW", "1.0")
  assert _skew_slope() == 1.0


def test_iv_ordering_put_richer_than_call():
  chain = _synthetic_chain(740.0, 0.15, AS_OF)
  ivs = chain.groupby("strike")["iv"].first()
  lo = ivs.index[ivs.index <= 690][-1]      # OTM put strike
  atm = min(ivs.index, key=lambda k: abs(k - 740))
  hi = ivs.index[ivs.index >= 790][0]       # OTM call strike
  assert ivs[lo] > ivs[atm] > ivs[hi]


def test_skew_enriches_put_premium(monkeypatch):
  monkeypatch.setenv("TABFM_BACKTEST_SKEW", "0")
  flat = _synthetic_chain(740.0, 0.15, AS_OF)
  monkeypatch.setenv("TABFM_BACKTEST_SKEW", "2.5")
  skewed = _synthetic_chain(740.0, 0.15, AS_OF)

  def put_mid(chain, k):
    r = chain[(chain["strike"] == k) & (chain["option_type"] == "put") & (chain["dte"] == 30)]
    return float(r["mid"].iloc[0])

  strike = 700.0  # sub-ATM put
  assert put_mid(skewed, strike) > put_mid(flat, strike)


def test_skewed_chain_still_yields_passing_put_spread():
  chain = _synthetic_chain(740.0, 0.15, AS_OF)
  cd = {"ticker": "SPY", "sector": "index_etf", "chain": chain, "vix": 18.0,
        "underlying": {"close": 740.0, "sma20": 735.0, "sma50": 730.0, "atr14": 9.0,
                       "hv20": 0.15, "volume": 8e7, "volume_zscore": 0.2,
                       "momentum_5d": 0.005, "momentum_20d": 0.02, "rsi_14": 55.0,
                       "macd_line": 1.0, "macd_signal": 0.8, "macd_histogram": 0.2}}
  rows = engineer_features(cd, AS_OF, iv_rank=50.0)
  puts = [r for r in rows if r["direction"] == "put_spread" and r["spread_width_dollars"] == 5.0]
  assert any(_passes_filters(r) for r in puts)
```

- [ ] **Step 2: Run to verify failure** (`_skew_slope` missing).

- [ ] **Step 3: Implement**

In `tabfm/trading/adapters/historical.py`, add near `_iv_premium`:

```python
def _skew_slope() -> float:
  """Equity-index volatility skew slope (per unit moneyness). OTM puts get
  higher IV than ATM than OTM calls — matches the index smirk and makes the
  modeled cost of closing a short put rise as spot falls toward the strike."""
  return float(os.environ.get("TABFM_BACKTEST_SKEW", "2.5"))
```

Replace `_synthetic_chain` with:

```python
def _synthetic_chain(S: float, hv20: float, as_of: date) -> pd.DataFrame:
  base_sigma = max(hv20 * _iv_premium(), 0.05)
  slope = _skew_slope()
  rows = []
  for dte in _DTE_WINDOWS:
    expiry = as_of + timedelta(days=dte)
    T = dte / 365.0
    for K in _strike_grid(S):
      skew_factor = max(1 + slope * (1 - K / S), 0.4)
      sigma_K = max(base_sigma * skew_factor, 0.05)
      for opt in ("call", "put"):
        price = _bs_price(S, K, T, sigma_K, opt)
        delta = abs(_bs_delta(S, K, T, sigma_K, opt))
        rows.append({
          "strike": K, "expiry": expiry, "option_type": opt,
          "bid": round(price * 0.996, 2), "ask": round(price * 1.004, 2),
          "mid": round(price, 2), "open_interest": 500,
          "delta": round(delta, 4), "iv": round(sigma_K, 4), "dte": dte,
        })
  return pd.DataFrame(rows)
```

`conftest.py`: add `"TABFM_BACKTEST_SKEW"` to the delenv tuple.

- [ ] **Step 4: Run new tests + full suite.** Expected: green. Note — the
  existing `test_synthetic_chain.py::test_thirty_delta_5wide_spread_clears_credit_floor`
  may now land on a different 30-delta strike due to skew shifting deltas; if it
  fails only because the credit/width or delta band moved, adjust that test's
  spot/hv or assertion tolerance with a comment (skew is realistic; keep the
  gauntlet-pass requirement). Do NOT weaken `_passes_filters`.

- [ ] **Step 5: Commit**

```bash
git add tabfm/trading/adapters/historical.py tabfm/trading/tests/test_vol_skew.py tabfm/trading/tests/conftest.py tabfm/trading/tests/test_synthetic_chain.py
git commit -m "feat(backtest): volatility skew in synthetic chain — put IV richer than call"
```

---

### Task 2: Trend-guard module

**Files:**
- Create: `tabfm/trading/pipeline/trend_guard.py`
- Test: `tabfm/trading/tests/test_trend_guard.py`

**Interfaces:**
- Consumes: `_spread_mark` (position_auditor), `_trend_direction` (feature_engineer).
- Produces: `assess_trend_risk(open_trades: list[dict], adapter, as_of: date) -> list[dict]` — one entry per challenged position with keys `trade_id, ticker, direction, trend, unrealized, loss_fraction, dte_left, stop_level, action, message`.

- [ ] **Step 1: Write the failing tests**

```python
# tabfm/trading/tests/test_trend_guard.py
from datetime import date

import pandas as pd

from tabfm.trading.pipeline.trend_guard import assess_trend_risk

AS_OF = date(2026, 7, 24)


class _Stub:
  """close/sma set the trend; short_mid/long_mid set the spread mark."""
  def __init__(self, close, sma20, sma50, short_mid, long_mid):
    self.close, self.sma20, self.sma50 = close, sma20, sma50
    self.short_mid, self.long_mid = short_mid, long_mid

  def get_underlying(self, ticker, as_of):
    return {"close": self.close, "sma20": self.sma20, "sma50": self.sma50}

  def get_options_chain(self, ticker, as_of):
    return pd.DataFrame([
      {"strike": 680.0, "expiry": pd.Timestamp("2026-08-21"), "option_type": "put",
       "mid": self.short_mid, "bid": self.short_mid, "ask": self.short_mid,
       "open_interest": 500, "delta": 0.3, "iv": 0.2, "dte": 28},
      {"strike": 675.0, "expiry": pd.Timestamp("2026-08-21"), "option_type": "put",
       "mid": self.long_mid, "bid": self.long_mid, "ask": self.long_mid,
       "open_interest": 500, "delta": 0.2, "iv": 0.2, "dte": 28},
    ])

  def get_vix(self, as_of):
    return 18.0


def _put_trade():
  return dict(trade_id=1, ticker="SPY", direction="put_spread",
              strike_short=680.0, strike_long=675.0, expiry="2026-08-21",
              entry_credit=2.0, spread_width=5.0, contracts=1, max_loss=300.0)


def _downtrend(short_mid, long_mid):
  return _Stub(690.0, 700.0, 710.0, short_mid, long_mid)   # close<sma20<sma50


def test_put_spread_challenged_by_downtrend_losing_consider():
  # mark = 3.0 - 0.2 = 2.8 → unrealized (2-2.8)*100 = -80 → 80/300 = 0.27 → CONSIDER
  alerts = assess_trend_risk([_put_trade()], _downtrend(3.0, 0.2), AS_OF)
  assert len(alerts) == 1
  a = alerts[0]
  assert a["action"] == "CONSIDER CLOSING"
  assert a["trend"] == "downtrend"
  assert a["unrealized"] < 0
  assert "SPY" in a["message"]


def test_put_spread_close_now_at_half_max_loss():
  # mark = 3.7 - 0.2 = 3.5 → unrealized -150 → 150/300 = 0.5 → CLOSE NOW
  alerts = assess_trend_risk([_put_trade()], _downtrend(3.7, 0.2), AS_OF)
  assert alerts[0]["action"] == "CLOSE NOW"
  assert alerts[0]["loss_fraction"] >= 0.5


def test_no_alert_when_trend_favorable():
  # uptrend (close>sma20>sma50) under a put spread → not adverse
  up = _Stub(710.0, 700.0, 690.0, 3.5, 0.2)
  assert assess_trend_risk([_put_trade()], up, AS_OF) == []


def test_no_alert_when_adverse_but_winning():
  # downtrend but mark = 1.0 - 0.2 = 0.8 < credit 2.0 → unrealized +120 → not challenged
  assert assess_trend_risk([_put_trade()], _downtrend(1.0, 0.2), AS_OF) == []


def test_call_spread_challenged_by_uptrend():
  up = _Stub(710.0, 700.0, 690.0, 3.7, 0.2)   # uptrend
  call = dict(trade_id=2, ticker="SPY", direction="call_spread",
              strike_short=680.0, strike_long=685.0, expiry="2026-08-21",
              entry_credit=2.0, spread_width=5.0, contracts=1, max_loss=300.0)
  # NOTE: _spread_mark reads the 'call' side of the chain; extend the stub to
  # serve calls if needed. If the stub only has puts, this test asserts the
  # adverse+trend path via a put-shaped chain is acceptable — keep the stub
  # returning the same two rows but option_type 'call' for this case.
  alerts = assess_trend_risk([call], _CallStub(710.0, 700.0, 690.0, 3.7, 0.2), AS_OF)
  assert alerts and alerts[0]["trend"] == "uptrend"


def test_disabled_via_env(monkeypatch):
  monkeypatch.setenv("TABFM_TREND_GUARD", "off")
  assert assess_trend_risk([_put_trade()], _downtrend(3.7, 0.2), AS_OF) == []


class _CallStub(_Stub):
  def get_options_chain(self, ticker, as_of):
    return pd.DataFrame([
      {"strike": 680.0, "expiry": pd.Timestamp("2026-08-21"), "option_type": "call",
       "mid": self.short_mid, "bid": self.short_mid, "ask": self.short_mid,
       "open_interest": 500, "delta": 0.3, "iv": 0.2, "dte": 28},
      {"strike": 685.0, "expiry": pd.Timestamp("2026-08-21"), "option_type": "call",
       "mid": self.long_mid, "bid": self.long_mid, "ask": self.long_mid,
       "open_interest": 500, "delta": 0.2, "iv": 0.2, "dte": 28},
    ])
```

- [ ] **Step 2: Run to verify failure** (module missing).

- [ ] **Step 3: Implement**

```python
# tabfm/trading/pipeline/trend_guard.py
"""Trend guard: advise on open positions the trend has turned against.

Advisory only — never closes or modifies a position. A credit spread is only
"challenged" when the adverse trend is CONFIRMED by an actual unrealized loss,
so a position comfortably OTM through a trend wiggle is not flagged (avoids
noise-trading). Its real value is live on real marks; in backtests the
synthetic marks under-price panic costs, so it exercises the logic only."""
import os
from datetime import date

from .feature_engineer import _trend_direction
from .position_auditor import _spread_mark


def assess_trend_risk(open_trades: list, adapter, as_of: date) -> list:
  if os.environ.get("TABFM_TREND_GUARD", "on").lower() == "off":
    return []
  challenged = []
  for t in open_trades:
    try:
      u = adapter.get_underlying(t["ticker"], as_of)
    except Exception:
      continue
    trend = _trend_direction(u["close"], u["sma20"], u["sma50"])
    adverse = (
      (t["direction"] == "put_spread" and trend == "downtrend")
      or (t["direction"] == "call_spread" and trend == "uptrend")
    )
    if not adverse:
      continue
    mark = _spread_mark(adapter, t, as_of)
    if mark is None:
      continue
    credit = float(t["entry_credit"])
    contracts = int(t["contracts"])
    unrealized = (credit - mark) * contracts * 100
    if unrealized >= 0:
      continue  # adverse trend but position still profitable — not an emergency
    max_loss = float(t.get("max_loss") or 0) or (
      (float(t["spread_width"]) - credit) * contracts * 100)
    loss_fraction = (-unrealized / max_loss) if max_loss > 0 else 0.0
    dte_left = (date.fromisoformat(str(t["expiry"])) - as_of).days
    stop_amount = credit * contracts * 100  # loss realized if the 2x stop fires
    legs = f"{t['strike_short']:g}/{t['strike_long']:g}"
    dirn = t["direction"].replace("_", " ")
    if loss_fraction >= 0.5:
      action = "CLOSE NOW"
      message = (f"#{t['trade_id']} {t['ticker']} {dirn} {legs}: adverse {trend}, "
                 f"at {loss_fraction:.0%} of max loss ({dte_left}d left) — exit now "
                 f"rather than wait for the 2x stop (~-${stop_amount:.0f}).")
    else:
      action = "CONSIDER CLOSING"
      message = (f"#{t['trade_id']} {t['ticker']} {dirn} {legs}: adverse {trend}, "
                 f"losing ${abs(unrealized):.0f} ({dte_left}d left, stop "
                 f"~-${stop_amount:.0f}) — close early or roll the tested side "
                 f"if the trend persists.")
    challenged.append({
      "trade_id": t["trade_id"], "ticker": t["ticker"], "direction": t["direction"],
      "trend": trend, "unrealized": round(unrealized, 2),
      "loss_fraction": round(loss_fraction, 4), "dte_left": dte_left,
      "stop_level": round(stop_amount, 2), "action": action, "message": message,
    })
  return challenged
```

- [ ] **Step 4: Run new tests + full suite.**

- [ ] **Step 5: Commit**

```bash
git add tabfm/trading/pipeline/trend_guard.py tabfm/trading/tests/test_trend_guard.py
git commit -m "feat(trading): trend-guard advisory for directionally-challenged positions"
```

---

### Task 3: Wire trend guard into nightly + midday + report

**Files:**
- Modify: `tabfm/trading/run_nightly.py`
- Modify: `docs/NIGHTLY_CLOUD_RUN.md`
- Test: `tabfm/trading/tests/test_trend_guard_integration.py`

**Interfaces:**
- Consumes: `assess_trend_risk` (Task 2).
- Produces: `run` and `run_audit_only` print a `[TrendGuard]` section and write a `## <date> — TREND ALERT` block to RECOMMENDATIONS.md when positions are challenged; `_log_trend_alert(alerts, as_of, db_path)` helper.

- [ ] **Step 1: Write the failing integration test**

```python
# tabfm/trading/tests/test_trend_guard_integration.py
from datetime import date
from pathlib import Path

import pandas as pd

from tabfm.trading.run_nightly import run_audit_only
from tabfm.trading.store.journal import init_db, insert_trade


class _DowntrendAdapter:
  def get_underlying(self, ticker, as_of):
    return {"close": 690.0, "sma20": 700.0, "sma50": 710.0}   # downtrend
  def get_options_chain(self, ticker, as_of):
    return pd.DataFrame([
      {"strike": 680.0, "expiry": pd.Timestamp("2026-08-21"), "option_type": "put",
       "mid": 2.8, "bid": 2.78, "ask": 2.82, "open_interest": 500,
       "delta": 0.3, "iv": 0.2, "dte": 28},
      {"strike": 675.0, "expiry": pd.Timestamp("2026-08-21"), "option_type": "put",
       "mid": 0.2, "bid": 0.18, "ask": 0.22, "open_interest": 500,
       "delta": 0.2, "iv": 0.2, "dte": 28},
    ])
  def get_vix(self, as_of):
    return 18.0


def _open_put(db):
  return insert_trade(dict(
    date_entered="2026-07-20", ticker="SPY", direction="put_spread",
    strike_short=680.0, strike_long=675.0, expiry="2026-08-21", dte=28,
    entry_credit=2.0, spread_width=5.0, contracts=1, max_loss=300.0,
    max_profit=200.0, pop_predicted=0.7, pop_raw=0.7, exp_return=0.2,
    regime="normal|downtrend|fair",
  ), db)


def test_midday_audit_emits_trend_alert(tmp_path, capsys):
  db = tmp_path / "j.db"
  init_db(db)
  _open_put(db)
  # mark 2.8-0.2=2.6 → unrealized -60 (losing), downtrend adverse → alert.
  # Set the stop high enough that the auditor does NOT close it first
  # (2.6 < 2x credit=4.0), so it survives to the trend-guard step.
  run_audit_only(_DowntrendAdapter(), date(2026, 7, 24), db_path=db,
                 store_path=tmp_path / "s.parquet")
  out = capsys.readouterr().out
  assert "[TrendGuard]" in out
  assert "CONSIDER CLOSING" in out or "CLOSE NOW" in out
  md = (tmp_path / "RECOMMENDATIONS.md").read_text()
  assert "TREND ALERT" in md
```

- [ ] **Step 2: Verify it fails** (`[TrendGuard]` not printed).

- [ ] **Step 3: Implement the wiring**

In `tabfm/trading/run_nightly.py`:
- Add import: `from .pipeline.trend_guard import assess_trend_risk`.
- Add a shared advisory helper (near `_log_recommendation`):

```python
def _emit_trend_guard(adapter, as_of, db_path) -> None:
  from .store.journal import get_open_trades
  alerts = assess_trend_risk(get_open_trades(db_path, strategy="model"), adapter, as_of)
  if not alerts:
    return
  print("[TrendGuard] Directionally challenged open positions:")
  for a in alerts:
    print(f"  {a['action']}: {a['message']}")
  md = Path(db_path).parent / "RECOMMENDATIONS.md"
  header = "# Nightly Recommendations\n\n"
  existing = ""
  if md.exists():
    existing = md.read_text()
    if existing.startswith(header):
      existing = existing[len(header):]
  lines = "\n".join(f"- {a['action']}: {a['message']}" for a in alerts)
  entry = f"## {as_of} — TREND ALERT\n\n{lines}\n\n"
  md.write_text(header + entry + existing)
```

- In `run`, after the `audit_positions` block (after the `if closed:` print),
  add: `_emit_trend_guard(adapter, as_of, db_path)`.
- In `run_audit_only`, after the `audit_positions` + closed print and before
  `portfolio_summary`, add: `_emit_trend_guard(adapter, as_of, db_path)`.

`docs/NIGHTLY_CLOUD_RUN.md`: add a short note under the final-message section:
the run may print a `[TrendGuard]` advisory and write a `TREND ALERT` block to
RECOMMENDATIONS.md — include these in the report; they are recommendations to
manage open positions (advisory only, no auto-action).

- [ ] **Step 4: Run the integration test + full suite.** Green expected.

- [ ] **Step 5: Commit**

```bash
git add tabfm/trading/run_nightly.py docs/NIGHTLY_CLOUD_RUN.md tabfm/trading/tests/test_trend_guard_integration.py
git commit -m "feat(trading): emit trend-guard advisory in nightly and midday runs"
```

---

## Self-Review

- **Spec coverage:** per-strike skew + slope env + IV ordering + richer put credit + gauntlet-pass (Task 1) ✓; assess_trend_risk with adverse+losing gating, CLOSE/CONSIDER thresholds, favorable/winning/off cases, call mirror (Task 2) ✓; nightly + midday wiring + RECOMMENDATIONS.md alert + docs (Task 3) ✓; advisory-only, model-book-only, env toggles ✓.
- **Placeholders:** none; complete code throughout.
- **Type consistency:** `assess_trend_risk(open_trades, adapter, as_of)` signature identical in Task 2 def, tests, and Task 3 caller; the challenged-dict keys match across module + tests; `_emit_trend_guard` uses `get_open_trades(..., strategy="model")` (merged helper).
- **No-network:** skew tests call `_synthetic_chain` directly; trend-guard tests use stub adapters; integration uses a stub adapter + tmp db.
- **Interaction note:** Task 1's skew shifts strike deltas → the existing
  `test_synthetic_chain` acceptance test may need a tolerance/spot tweak (called
  out in Task 1 Step 4); do not weaken the gauntlet.
