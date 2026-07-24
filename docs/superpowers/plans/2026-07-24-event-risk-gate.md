# Event Risk Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Block new spread entries on high-event-risk days (mega-cap earnings, macro events, VIX/IV spikes) while recording event-context features on every stored row.

**Architecture:** A pure-logic gate module (`event_gate.py`) consumes snapshot-carried earnings events, a committed macro calendar, VIX history, and SPY chain stats, returning `GateResult(gated, reasons, degraded, features)`. `run_nightly.run()` evaluates it after fetching chains: feature rows are always appended and expired rows always labeled, but on gated nights scoring/selection is skipped and the gated day is logged. Market state (VIX + SPY median IV per day) persists in `data/market_history.csv` next to the journal.

**Tech Stack:** Python 3.14, pandas, numpy, pytest. No new dependencies.

## Global Constraints

- Paper trading only; never import or call order-placement APIs.
- Gate blocks NEW ENTRIES only: position audit, expired-row labeling, feature-row append, and the portfolio summary run on every night, gated or not.
- Fail-open: missing earnings data → `degraded=True`, warning printed, run proceeds on remaining layers.
- Threshold env vars with exact defaults: `TABFM_GATE_VIX_5D=0.15`, `TABFM_GATE_IV_HV=1.6`, `TABFM_GATE_IV_JUMP=1.2`; master switch `TABFM_EVENT_GATE=off` disables gating (features still computed). Read env at call time, not import time.
- `MEGA_CAPS = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AVGO"]` exactly.
- Danger window: event on `as_of` (AMC included) or on the next trading session (weekends skipped by weekday rule).
- Feature sentinel: `99.0` when no event within 14 calendar days or data unavailable.
- Code style: 2-space indentation, matching the existing codebase.
- Suite must stay green: `PYTHONPATH=. python3 -m pytest tabfm/trading/tests/ -q --ignore=tabfm/trading/tests/test_hist_adapter.py --ignore=tabfm/trading/tests/test_live_adapter.py --ignore=tabfm/trading/tests/test_run_nightly.py` (63 tests before this plan).
- All paths relative to repo root `/Users/alberto2perez/src/tabfm-options-trader`.

---

### Task 1: Event gate module

**Files:**
- Create: `tabfm/trading/pipeline/event_gate.py`
- Test: `tabfm/trading/tests/test_event_gate.py`

**Interfaces:**
- Consumes: nothing from other tasks (pure logic + stdlib/numpy).
- Produces: `GateResult` dataclass with fields `gated: bool`, `reasons: list[str]`, `degraded: bool`, `features: dict`; `evaluate_event_gate(events, macro_calendar, vix_history, chain_stats, as_of) -> GateResult`; `load_macro_calendar(path=None) -> list[dict]`; constant `MEGA_CAPS`. Later tasks import all four names from `tabfm.trading.pipeline.event_gate`.

- [ ] **Step 1: Write the failing tests**

```python
# tabfm/trading/tests/test_event_gate.py
from datetime import date
import json

from tabfm.trading.pipeline.event_gate import (
  evaluate_event_gate, load_macro_calendar, MEGA_CAPS, GateResult,
)

AS_OF = date(2026, 7, 24)  # a Friday
NO_EVENTS = {"earnings": []}
CALM_VIX = [["2026-07-17", 18.0], ["2026-07-20", 18.2], ["2026-07-21", 18.1],
            ["2026-07-22", 18.4], ["2026-07-23", 18.7], ["2026-07-24", 18.8]]
CALM_CHAIN = {"median_iv": 0.20, "hv20": 0.15, "prev_median_iv": 0.20}


def _gate(events=NO_EVENTS, macro=(), vix=CALM_VIX, chain=CALM_CHAIN, as_of=AS_OF):
  return evaluate_event_gate(events, list(macro), vix, chain, as_of)


def test_calm_day_not_gated():
  r = _gate()
  assert r.gated is False
  assert r.degraded is False


def test_megacap_earnings_today_gates():
  r = _gate(events={"earnings": [{"symbol": "GOOGL", "date": "2026-07-24", "when": "amc"}]})
  assert r.gated is True
  assert any("GOOGL" in x for x in r.reasons)


def test_megacap_earnings_next_session_gates_over_weekend():
  # Friday as_of -> next session is Monday 2026-07-27
  r = _gate(events={"earnings": [{"symbol": "NVDA", "date": "2026-07-27", "when": "bmo"}]})
  assert r.gated is True


def test_earnings_three_sessions_out_does_not_gate():
  r = _gate(events={"earnings": [{"symbol": "AAPL", "date": "2026-07-29", "when": "amc"}]})
  assert r.gated is False


def test_non_megacap_earnings_ignored():
  r = _gate(events={"earnings": [{"symbol": "KO", "date": "2026-07-24", "when": "amc"}]})
  assert r.gated is False


def test_macro_event_next_session_gates():
  r = _gate(macro=[{"date": "2026-07-27", "event": "FOMC rate decision"}])
  assert r.gated is True
  assert any("FOMC" in x for x in r.reasons)


def test_vix_spike_gates():
  spiky = [["2026-07-17", 16.0], ["2026-07-20", 16.5], ["2026-07-21", 17.0],
           ["2026-07-22", 17.5], ["2026-07-23", 18.0], ["2026-07-24", 18.6]]
  # (18.6 - 16.0) / 16.0 = +16.25% > 15%
  r = _gate(vix=spiky)
  assert r.gated is True
  assert any("VIX" in x for x in r.reasons)


def test_vix_below_threshold_does_not_gate():
  mild = [["2026-07-17", 17.0], ["2026-07-20", 17.2], ["2026-07-21", 17.5],
          ["2026-07-22", 17.8], ["2026-07-23", 18.5], ["2026-07-24", 19.3]]
  # +13.5% < 15%
  assert _gate(vix=mild).gated is False


def test_short_vix_history_skips_vix_check():
  assert _gate(vix=[["2026-07-24", 18.8]]).gated is False


def test_iv_spike_gates():
  chain = {"median_iv": 0.30, "hv20": 0.15, "prev_median_iv": 0.20}
  # ratio 2.0 > 1.6 and 0.30 > 0.20 * 1.2
  r = _gate(chain=chain)
  assert r.gated is True
  assert any("IV" in x for x in r.reasons)


def test_high_but_stable_iv_does_not_gate():
  chain = {"median_iv": 0.30, "hv20": 0.15, "prev_median_iv": 0.29}
  assert _gate(chain=chain).gated is False


def test_missing_prev_iv_disables_iv_check():
  chain = {"median_iv": 0.30, "hv20": 0.15, "prev_median_iv": None}
  assert _gate(chain=chain).gated is False


def test_degraded_when_events_none():
  r = _gate(events=None)
  assert r.degraded is True
  assert r.gated is False  # other layers still calm


def test_master_switch_disables_gating(monkeypatch):
  monkeypatch.setenv("TABFM_EVENT_GATE", "off")
  r = _gate(events={"earnings": [{"symbol": "GOOGL", "date": "2026-07-24", "when": "amc"}]})
  assert r.gated is False
  assert r.features["days_to_next_megacap_earnings"] == 0.0


def test_env_threshold_override(monkeypatch):
  monkeypatch.setenv("TABFM_GATE_VIX_5D", "0.10")
  mild = [["2026-07-17", 17.0], ["2026-07-20", 17.2], ["2026-07-21", 17.5],
          ["2026-07-22", 17.8], ["2026-07-23", 18.5], ["2026-07-24", 19.3]]
  assert _gate(vix=mild).gated is True  # +13.5% > overridden 10%


def test_features_computed():
  r = _gate(
    events={"earnings": [{"symbol": "META", "date": "2026-07-29", "when": "amc"}]},
    macro=[{"date": "2026-08-12", "event": "CPI release"}],
  )
  assert r.features["days_to_next_megacap_earnings"] == 3.0  # Fri->Wed = 3 busdays
  assert r.features["days_to_next_macro_event"] == 13.0
  assert round(r.features["vix_5d_change"], 4) == round((18.8 - 18.0) / 18.0, 4)


def test_feature_sentinel_when_no_events():
  r = _gate()
  assert r.features["days_to_next_megacap_earnings"] == 99.0
  assert r.features["days_to_next_macro_event"] == 99.0


def test_load_macro_calendar(tmp_path):
  p = tmp_path / "macro.json"
  p.write_text(json.dumps([{"date": "2026-07-29", "event": "FOMC rate decision"}]))
  cal = load_macro_calendar(p)
  assert cal[0]["event"] == "FOMC rate decision"


def test_load_macro_calendar_missing_file(tmp_path):
  assert load_macro_calendar(tmp_path / "nope.json") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. python3 -m pytest tabfm/trading/tests/test_event_gate.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'tabfm.trading.pipeline.event_gate'`

- [ ] **Step 3: Write the implementation**

```python
# tabfm/trading/pipeline/event_gate.py
"""Event risk gate: block new entries on high-event-risk days.

Layer A: calendar (mega-cap earnings from the snapshot + committed macro
calendar). Layer B: market-priced risk (VIX 5-session spike velocity, IV
rising above realized vol). Also computes the strategy-C event-context
features recorded on every row.
"""
import json
import os
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import numpy as np

MEGA_CAPS = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AVGO"]

_DEFAULT_MACRO_CALENDAR = Path(__file__).parents[3] / "data" / "macro_calendar.json"
_FEATURE_SENTINEL = 99.0
_EVENT_HORIZON_DAYS = 14


@dataclass
class GateResult:
  gated: bool
  reasons: list = field(default_factory=list)
  degraded: bool = False
  features: dict = field(default_factory=dict)


def load_macro_calendar(path: Path | None = None) -> list[dict]:
  path = Path(path) if path is not None else _DEFAULT_MACRO_CALENDAR
  if not path.exists():
    return []
  return json.loads(path.read_text())


def _next_session(d: date) -> date:
  nxt = d + timedelta(days=1)
  while nxt.weekday() >= 5:
    nxt += timedelta(days=1)
  return nxt


def _busdays_until(as_of: date, target: date) -> float:
  if target < as_of:
    return _FEATURE_SENTINEL
  if (target - as_of).days > _EVENT_HORIZON_DAYS:
    return _FEATURE_SENTINEL
  return float(np.busday_count(as_of, target))


def evaluate_event_gate(
  events: dict | None,
  macro_calendar: list,
  vix_history: list,
  chain_stats: dict,
  as_of: date,
) -> GateResult:
  """Evaluate all gate layers for the session.

  events: {"earnings": [{"symbol", "date", "when"}]} from the snapshot, or
    None when the fetch step could not provide earnings data (degraded).
  vix_history: [[YYYY-MM-DD, value], ...] most recent last, incl. today.
  chain_stats: {"median_iv", "hv20", "prev_median_iv"} for the market proxy
    (SPY); may be empty when no chain was fetched.
  """
  reasons: list[str] = []
  degraded = events is None or "earnings" not in (events or {})
  danger = {as_of, _next_session(as_of)}

  # ---- Layer A: mega-cap earnings -------------------------------------
  earnings = (events or {}).get("earnings") or []
  next_earnings = _FEATURE_SENTINEL
  for e in earnings:
    if e.get("symbol") not in MEGA_CAPS:
      continue
    try:
      e_date = date.fromisoformat(str(e["date"]))
    except (KeyError, ValueError):
      continue
    next_earnings = min(next_earnings, _busdays_until(as_of, e_date))
    if e_date in danger:
      when = e.get("when", "unknown")
      day = "today" if e_date == as_of else "next session"
      reasons.append(f"{e['symbol']} reports {day} ({when.upper()})")

  # ---- Layer A: macro calendar ----------------------------------------
  next_macro = _FEATURE_SENTINEL
  for m in macro_calendar or []:
    try:
      m_date = date.fromisoformat(str(m["date"]))
    except (KeyError, ValueError):
      continue
    next_macro = min(next_macro, _busdays_until(as_of, m_date))
    if m_date in danger:
      day = "today" if m_date == as_of else "next session"
      reasons.append(f"{m.get('event', 'macro event')} {day}")

  # ---- Layer B: VIX spike velocity ------------------------------------
  vix_5d_change = 0.0
  if vix_history and len(vix_history) >= 6:
    now = float(vix_history[-1][1])
    base = float(vix_history[-6][1])
    if base > 0:
      vix_5d_change = (now - base) / base
      threshold = float(os.environ.get("TABFM_GATE_VIX_5D", "0.15"))
      if vix_5d_change > threshold:
        reasons.append(f"VIX +{vix_5d_change * 100:.0f}% in 5 sessions")

  # ---- Layer B: IV rising above realized vol --------------------------
  median_iv = (chain_stats or {}).get("median_iv")
  hv20 = (chain_stats or {}).get("hv20")
  prev_iv = (chain_stats or {}).get("prev_median_iv")
  if median_iv and hv20 and prev_iv:
    iv_hv = float(os.environ.get("TABFM_GATE_IV_HV", "1.6"))
    iv_jump = float(os.environ.get("TABFM_GATE_IV_JUMP", "1.2"))
    if median_iv / hv20 > iv_hv and median_iv > prev_iv * iv_jump:
      reasons.append(
        f"options pricing an event (IV/HV={median_iv / hv20:.2f}, "
        f"IV +{(median_iv / prev_iv - 1) * 100:.0f}% d/d)"
      )

  features = {
    "days_to_next_megacap_earnings": next_earnings,
    "days_to_next_macro_event": next_macro,
    "vix_5d_change": round(vix_5d_change, 4),
  }

  enabled = os.environ.get("TABFM_EVENT_GATE", "on").lower() != "off"
  return GateResult(
    gated=bool(reasons) and enabled,
    reasons=reasons,
    degraded=degraded,
    features=features,
  )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. python3 -m pytest tabfm/trading/tests/test_event_gate.py -q`
Expected: 19 passed

- [ ] **Step 5: Commit**

```bash
git add tabfm/trading/pipeline/event_gate.py tabfm/trading/tests/test_event_gate.py
git commit -m "feat(trading): event gate module — calendar + market-priced risk layers"
```

---

### Task 2: Macro calendar seed + market history store

**Files:**
- Create: `data/macro_calendar.json`
- Create: `tabfm/trading/store/market_history.py`
- Test: `tabfm/trading/tests/test_market_history.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `record_market_day(data_dir: Path, as_of: date, vix: float, median_iv: float | None) -> None` and `load_market_history(data_dir: Path, n: int = 10) -> list[dict]` (each dict: `{"date": str, "vix": float, "median_iv": float | None}`, oldest→newest), imported by Task 5 from `tabfm.trading.store.market_history`. The CSV file is named `market_history.csv` inside `data_dir`.

- [ ] **Step 1: Write the failing tests**

```python
# tabfm/trading/tests/test_market_history.py
from datetime import date

from tabfm.trading.store.market_history import record_market_day, load_market_history


def test_record_and_load_roundtrip(tmp_path):
  record_market_day(tmp_path, date(2026, 7, 23), 18.7, 0.21)
  record_market_day(tmp_path, date(2026, 7, 24), 18.81, 0.22)
  hist = load_market_history(tmp_path)
  assert len(hist) == 2
  assert hist[-1] == {"date": "2026-07-24", "vix": 18.81, "median_iv": 0.22}


def test_record_same_day_overwrites(tmp_path):
  record_market_day(tmp_path, date(2026, 7, 24), 18.0, 0.20)
  record_market_day(tmp_path, date(2026, 7, 24), 18.81, 0.22)
  hist = load_market_history(tmp_path)
  assert len(hist) == 1
  assert hist[0]["vix"] == 18.81


def test_none_median_iv_roundtrips(tmp_path):
  record_market_day(tmp_path, date(2026, 7, 24), 18.81, None)
  assert load_market_history(tmp_path)[0]["median_iv"] is None


def test_load_missing_file_returns_empty(tmp_path):
  assert load_market_history(tmp_path) == []


def test_load_respects_n(tmp_path):
  for day in range(1, 15):
    record_market_day(tmp_path, date(2026, 7, day), 15.0 + day, 0.2)
  hist = load_market_history(tmp_path, n=5)
  assert len(hist) == 5
  assert hist[-1]["date"] == "2026-07-14"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. python3 -m pytest tabfm/trading/tests/test_market_history.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'tabfm.trading.store.market_history'`

- [ ] **Step 3: Write the implementation**

```python
# tabfm/trading/store/market_history.py
"""Per-session market state (VIX, SPY median IV) persisted as CSV.

Lives next to the journal (data/market_history.csv, committed) so cloud runs
inherit it from the repo clone and push updates back.
"""
import csv
from datetime import date
from pathlib import Path

_FILENAME = "market_history.csv"


def record_market_day(
  data_dir: Path, as_of: date, vix: float, median_iv: float | None
) -> None:
  path = Path(data_dir) / _FILENAME
  rows = {r["date"]: r for r in load_market_history(data_dir, n=10_000)}
  rows[str(as_of)] = {"date": str(as_of), "vix": vix, "median_iv": median_iv}
  with open(path, "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(["date", "vix", "median_iv"])
    for key in sorted(rows):
      r = rows[key]
      writer.writerow([r["date"], r["vix"], "" if r["median_iv"] is None else r["median_iv"]])


def load_market_history(data_dir: Path, n: int = 10) -> list[dict]:
  path = Path(data_dir) / _FILENAME
  if not path.exists():
    return []
  with open(path, newline="") as f:
    rows = [
      {
        "date": r["date"],
        "vix": float(r["vix"]),
        "median_iv": float(r["median_iv"]) if r["median_iv"] else None,
      }
      for r in csv.DictReader(f)
    ]
  rows.sort(key=lambda r: r["date"])
  return rows[-n:]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. python3 -m pytest tabfm/trading/tests/test_market_history.py -q`
Expected: 5 passed

- [ ] **Step 5: Seed the macro calendar**

Create `data/macro_calendar.json` with exactly:

```json
[
  {"date": "2026-07-29", "event": "FOMC rate decision"},
  {"date": "2026-08-07", "event": "Jobs report (NFP)"},
  {"date": "2026-08-12", "event": "CPI release"},
  {"date": "2026-09-04", "event": "Jobs report (NFP)"},
  {"date": "2026-09-11", "event": "CPI release"},
  {"date": "2026-09-16", "event": "FOMC rate decision"},
  {"date": "2026-10-02", "event": "Jobs report (NFP)"},
  {"date": "2026-10-13", "event": "CPI release"},
  {"date": "2026-10-28", "event": "FOMC rate decision"},
  {"date": "2026-11-06", "event": "Jobs report (NFP)"},
  {"date": "2026-11-12", "event": "CPI release"},
  {"date": "2026-12-04", "event": "Jobs report (NFP)"},
  {"date": "2026-12-09", "event": "FOMC rate decision"},
  {"date": "2026-12-10", "event": "CPI release"}
]
```

Note: dates follow the standard release patterns (FOMC second meeting day, first-Friday NFP, mid-month CPI) and are refreshed/verified quarterly by the cloud agent (Task 5 adds that instruction to the docs).

- [ ] **Step 6: Commit**

```bash
git add tabfm/trading/store/market_history.py tabfm/trading/tests/test_market_history.py data/macro_calendar.json
git commit -m "feat(trading): market history store and seeded 2026 macro calendar"
```

---

### Task 3: Adapter support — events and VIX history

**Files:**
- Modify: `tabfm/trading/adapters/base.py` (append after `get_close`, currently ends at line 25)
- Modify: `tabfm/trading/adapters/historical.py` (append methods to `HistAdapter`)
- Modify: `tabfm/trading/adapters/snapshot.py` (append methods to `SnapshotAdapter`)
- Test: `tabfm/trading/tests/test_snapshot_adapter_events.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: on every adapter, `get_events(as_of: date) -> dict | None` (base default `None` = degraded) and `get_vix_history(as_of: date, n: int = 6) -> list` of `[date_str, float]` oldest→newest (base default `[]`). `HistAdapter.get_events` returns `{"earnings": []}` (layer A inactive in backtests, not degraded). Task 5 calls both on whatever adapter `run()` receives.

- [ ] **Step 1: Write the failing test**

```python
# tabfm/trading/tests/test_snapshot_adapter_events.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python3 -m pytest tabfm/trading/tests/test_snapshot_adapter_events.py -q`
Expected: FAIL with `AttributeError: 'SnapshotAdapter' object has no attribute 'get_events'`

- [ ] **Step 3: Implement the adapter methods**

Append to the end of `class DataAdapter` in `tabfm/trading/adapters/base.py` (after the `get_close` method):

```python
  def get_events(self, as_of: date) -> dict | None:
    """Upcoming market events ({"earnings": [...]}) or None when unavailable."""
    return None

  def get_vix_history(self, as_of: date, n: int = 6) -> list:
    """Recent [date_str, vix] pairs on/before as_of, oldest first."""
    return []
```

Append to the end of `class HistAdapter` in `tabfm/trading/adapters/historical.py` (after `get_vix`):

```python
  def get_events(self, as_of: date) -> dict | None:
    # No historical earnings calendar in v1: layer A inactive, not degraded.
    return {"earnings": []}

  def get_vix_history(self, as_of: date, n: int = 6) -> list:
    self._assert_no_lookahead(as_of)
    df = self._history("^VIX", lookback=60)
    df = df[df.index <= pd.Timestamp(as_of)]
    tail = df["Close"].tail(n)
    return [[str(idx.date()), float(v)] for idx, v in tail.items()]
```

Append to the end of `class SnapshotAdapter` in `tabfm/trading/adapters/snapshot.py` (after `get_close`):

```python
  def get_events(self, as_of: date) -> dict | None:
    return self._s.get("events")

  def get_vix_history(self, as_of: date, n: int = 6) -> list:
    hist = self._s.get("vix_history") or []
    valid = [
      [str(h[0]), float(h[1])]
      for h in hist
      if date.fromisoformat(str(h[0])) <= as_of
    ]
    return valid[-n:]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. python3 -m pytest tabfm/trading/tests/test_snapshot_adapter_events.py -q`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add tabfm/trading/adapters/base.py tabfm/trading/adapters/historical.py tabfm/trading/adapters/snapshot.py tabfm/trading/tests/test_snapshot_adapter_events.py
git commit -m "feat(trading): adapters expose events and VIX history for the event gate"
```

---

### Task 4: Event-context features on every row

**Files:**
- Modify: `tabfm/trading/pipeline/feature_engineer.py` (signature at line 31, row dict around lines 115-121)
- Modify: `tabfm/trading/pipeline/tabfm_scorer.py:4-12` (`FEATURE_COLS`)
- Test: modify `tabfm/trading/tests/test_feature_engineer.py`

**Interfaces:**
- Consumes: nothing (extra_features values come from Task 5's integration).
- Produces: `engineer_features(chain_data, as_of, iv_rank, extra_features: dict | None = None)` — every returned row now contains `days_to_next_megacap_earnings` (default 99.0), `days_to_next_macro_event` (default 99.0), `iv_spike_score` (default 0.0), and `vix_5d_change` sourced from `extra_features` (default 0.0, replacing the hardcoded 0.0). `FEATURE_COLS` gains the three new column names.

- [ ] **Step 1: Add failing tests to the existing test file**

Append to `tabfm/trading/tests/test_feature_engineer.py`:

```python
def test_extra_features_merged_into_rows():
  rows = engineer_features(_chain_data(), AS_OF, 50.0, extra_features={
    "days_to_next_megacap_earnings": 2.0,
    "days_to_next_macro_event": 5.0,
    "vix_5d_change": 0.04,
    "iv_spike_score": 1.33,
  })
  assert rows, "fixture must produce at least one candidate"
  assert rows[0]["days_to_next_megacap_earnings"] == 2.0
  assert rows[0]["days_to_next_macro_event"] == 5.0
  assert rows[0]["vix_5d_change"] == 0.04
  assert rows[0]["iv_spike_score"] == 1.33


def test_event_feature_defaults_without_extra():
  rows = engineer_features(_chain_data(), AS_OF, 50.0)
  assert rows[0]["days_to_next_megacap_earnings"] == 99.0
  assert rows[0]["days_to_next_macro_event"] == 99.0
  assert rows[0]["iv_spike_score"] == 0.0
  assert rows[0]["vix_5d_change"] == 0.0
```

Note: reuse the existing chain-data fixture in that file. If it is a plain
dict literal rather than a `_chain_data()` helper, adapt the two tests to
call `engineer_features` the same way the existing tests do — the fixture
must produce at least one candidate row (the existing required-columns test
already relies on that). Also extend the existing required-columns test's
expected column list with the three new names.

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `PYTHONPATH=. python3 -m pytest tabfm/trading/tests/test_feature_engineer.py -q`
Expected: the two new tests FAIL (`TypeError: engineer_features() got an unexpected keyword argument 'extra_features'`); existing tests still pass.

- [ ] **Step 3: Implement**

In `tabfm/trading/pipeline/feature_engineer.py` change the signature:

```python
def engineer_features(
  chain_data: dict, as_of: date, iv_rank: float, extra_features: dict | None = None
) -> list[dict]:
```

and immediately inside the function body add:

```python
  extra = {
    "days_to_next_megacap_earnings": 99.0,
    "days_to_next_macro_event": 99.0,
    "vix_5d_change": 0.0,
    "iv_spike_score": 0.0,
    **(extra_features or {}),
  }
```

In the row dict, replace the line

```python
        "vix_5d_change": 0.0,
```

with

```python
        "vix_5d_change": extra["vix_5d_change"],
        "days_to_next_megacap_earnings": extra["days_to_next_megacap_earnings"],
        "days_to_next_macro_event": extra["days_to_next_macro_event"],
        "iv_spike_score": extra["iv_spike_score"],
```

In `tabfm/trading/pipeline/tabfm_scorer.py` replace the `FEATURE_COLS` list:

```python
FEATURE_COLS = [
  "price_close", "momentum_5d", "momentum_20d", "atr_14", "volume_zscore",
  "price_vs_sma20", "vix_level", "vix_5d_change", "iv_rank", "hv20",
  "hv_iv_ratio", "rsi_14", "macd_line", "macd_signal", "macd_histogram",
  "days_to_next_megacap_earnings", "days_to_next_macro_event", "iv_spike_score",
  "dte", "short_delta", "strike_distance_pct",
  "spread_width_dollars", "bid_ask_pct",
  "vix_bucket", "trend_direction", "iv_regime", "earnings_flag",
  "direction", "expiry_type", "sector",
]
```

- [ ] **Step 4: Run the full suite**

Run: `PYTHONPATH=. python3 -m pytest tabfm/trading/tests/ -q --ignore=tabfm/trading/tests/test_hist_adapter.py --ignore=tabfm/trading/tests/test_live_adapter.py --ignore=tabfm/trading/tests/test_run_nightly.py`
Expected: all pass (63 pre-existing + Tasks 1-3 additions + 2 new).

- [ ] **Step 5: Commit**

```bash
git add tabfm/trading/pipeline/feature_engineer.py tabfm/trading/pipeline/tabfm_scorer.py tabfm/trading/tests/test_feature_engineer.py
git commit -m "feat(trading): record event-context features on every row"
```

---

### Task 5: Pipeline integration, gated-day logging, docs

**Files:**
- Modify: `tabfm/trading/run_nightly.py`
- Modify: `docs/NIGHTLY_CLOUD_RUN.md`
- Test: `tabfm/trading/tests/test_event_gate_integration.py`

**Interfaces:**
- Consumes: `evaluate_event_gate`, `load_macro_calendar` (Task 1); `record_market_day`, `load_market_history` (Task 2); `adapter.get_events` / `adapter.get_vix_history` (Task 3); `engineer_features(..., extra_features=...)` (Task 4).
- Produces: gated nights print `[EventGate] NO NEW ENTRIES — <reasons>`, log a gated entry to `RECOMMENDATIONS.md`, and return `None` without touching the journal; `data/market_history.csv` gains one row per run.

- [ ] **Step 1: Write the failing integration test**

```python
# tabfm/trading/tests/test_event_gate_integration.py
import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd

from tabfm.trading.adapters.base import DataAdapter
from tabfm.trading.run_nightly import run


class _GatedStubAdapter(DataAdapter):
  """Minimal adapter: one viable put-spread pair, GOOGL reporting today."""

  def get_underlying(self, ticker, as_of):
    return {
      "close": 700.0, "sma20": 700.0, "sma50": 690.0, "atr14": 9.0,
      "hv20": 0.15, "volume": 1e6, "volume_zscore": 0.0,
      "momentum_5d": 0.01, "momentum_20d": 0.02, "rsi_14": 55.0,
      "macd_line": 1.0, "macd_signal": 0.8, "macd_histogram": 0.2,
    }

  def get_options_chain(self, ticker, as_of):
    return pd.DataFrame([
      {"strike": 680.0, "expiry": pd.Timestamp("2026-08-21"), "option_type": "put",
       "bid": 2.20, "ask": 2.30, "mid": 2.25, "open_interest": 500,
       "delta": 0.25, "iv": 0.20, "dte": 28},
      {"strike": 675.0, "expiry": pd.Timestamp("2026-08-21"), "option_type": "put",
       "bid": 1.60, "ask": 1.70, "mid": 1.65, "open_interest": 500,
       "delta": 0.20, "iv": 0.20, "dte": 28},
    ])

  def get_vix(self, as_of):
    return 18.8

  def get_events(self, as_of):
    return {"earnings": [{"symbol": "GOOGL", "date": str(as_of), "when": "amc"}]}

  def get_vix_history(self, as_of, n=6):
    return [["2026-07-17", 18.0], ["2026-07-20", 18.2], ["2026-07-21", 18.1],
            ["2026-07-22", 18.4], ["2026-07-23", 18.7], ["2026-07-24", 18.8]]


def _patch_watchlist(monkeypatch):
  import tabfm.trading.watchlist as wl
  import tabfm.trading.pipeline.chain_fetcher as cf
  from tabfm.trading.watchlist import Ticker
  lone = [Ticker("SPY", "index_etf")]
  monkeypatch.setattr(wl, "WATCHLIST", lone)
  monkeypatch.setattr(cf, "WATCHLIST", lone)


def test_gated_night_places_no_trade_but_persists_rows(tmp_path, monkeypatch, capsys):
  _patch_watchlist(monkeypatch)
  db = tmp_path / "journal.db"
  store = tmp_path / "store.parquet"

  # Models are never touched on a gated night — pass sentinels that would
  # crash if scoring ran.
  result = run(_GatedStubAdapter(), clf_model=object(), reg_model=object(),
               as_of=date(2026, 7, 24), db_path=db, store_path=store)

  assert result is None
  out = capsys.readouterr().out
  assert "[EventGate] NO NEW ENTRIES" in out
  assert "GOOGL" in out
  assert "PORTFOLIO SUMMARY" in out

  # No journal entry
  conn = sqlite3.connect(db)
  assert conn.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0] == 0

  # Feature rows still appended, with event features populated
  df = pd.read_parquet(store)
  assert len(df) > 0
  assert float(df["days_to_next_megacap_earnings"].iloc[0]) == 0.0

  # Gated day logged
  md = (tmp_path / "RECOMMENDATIONS.md").read_text()
  assert "GATED" in md

  # Market history recorded
  assert (tmp_path / "market_history.csv").exists()


def test_ungated_night_reaches_selection(tmp_path, monkeypatch, capsys):
  _patch_watchlist(monkeypatch)

  class _CalmStub(_GatedStubAdapter):
    def get_events(self, as_of):
      return {"earnings": []}

  db = tmp_path / "journal.db"
  store = tmp_path / "store.parquet"
  result = run(_CalmStub(), clf_model=None, reg_model=None,
               as_of=date(2026, 7, 24), db_path=db, store_path=store)
  # Cold start on an empty store: fallback scoring, credit-yield pick.
  # Model loading is skipped only when models are provided; None triggers
  # loading — so pass sentinels here too and rely on the ≥20-row context
  # guard keeping TabFM unused.
  out = capsys.readouterr().out
  assert "[EventGate] NO NEW ENTRIES" not in out
```

Note for the implementer: in `test_ungated_night_reaches_selection`, passing
`clf_model=None` would trigger the real model download. Pass
`clf_model=object(), reg_model=object()` exactly as the gated test does —
cold-start (empty store) guarantees `score_candidates_batch` falls back
before touching the model. Fix the test accordingly when writing it; the
assertion stays the same.

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python3 -m pytest tabfm/trading/tests/test_event_gate_integration.py -q`
Expected: FAIL — `[EventGate] NO NEW ENTRIES` never printed (gate not integrated yet).

- [ ] **Step 3: Integrate the gate into `run_nightly.py`**

Add imports after the existing pipeline imports (line 21, after `portfolio_summary`):

```python
from .pipeline.event_gate import evaluate_event_gate, load_macro_calendar
from .store.market_history import record_market_day, load_market_history
```

Replace the block from `chain_data_list = fetch_chains(adapter, as_of)` (line 67) through the end of the per-chain loop (line 88, `all_feature_rows.extend(feature_rows)`) with:

```python
  chain_data_list = fetch_chains(adapter, as_of)

  # --- Event risk gate -------------------------------------------------
  data_dir = Path(db_path).parent
  vix_now = adapter.get_vix(as_of)
  spy_chain = next(
    (c for c in chain_data_list if c["ticker"] == "SPY"),
    chain_data_list[0] if chain_data_list else None,
  )
  chain_stats = {}
  if spy_chain is not None and len(spy_chain["chain"]):
    prior = load_market_history(data_dir, n=1)
    prior_iv = prior[-1]["median_iv"] if prior and prior[-1]["date"] != str(as_of) else None
    chain_stats = {
      "median_iv": float(spy_chain["chain"]["iv"].median()),
      "hv20": float(spy_chain["underlying"]["hv20"]),
      "prev_median_iv": prior_iv,
    }
  vix_history = adapter.get_vix_history(as_of)
  if not vix_history:
    vix_history = [[h["date"], h["vix"]] for h in load_market_history(data_dir, n=10)]
  if not any(str(as_of) == str(d) for d, _ in vix_history):
    vix_history = vix_history + [[str(as_of), vix_now]]
  record_market_day(data_dir, as_of, vix_now, chain_stats.get("median_iv"))

  gate = evaluate_event_gate(
    events=adapter.get_events(as_of),
    macro_calendar=load_macro_calendar(),
    vix_history=vix_history,
    chain_stats=chain_stats,
    as_of=as_of,
  )
  if gate.degraded:
    print("[EventGate] DEGRADED — earnings calendar unavailable")

  # --- Feature engineering (always runs, gated or not) -----------------
  all_candidates = []
  all_feature_rows = []
  scoring_groups: dict[tuple, list[dict]] = {}

  for chain_data in chain_data_list:
    iv_rank = compute_iv_rank(vix_now, store_path)
    hv20 = float(chain_data["underlying"]["hv20"]) or 0.0
    chain_df = chain_data["chain"]
    iv_spike = (
      float(chain_df["iv"].median()) / hv20
      if len(chain_df) and hv20 > 0 else 0.0
    )
    extra = {**gate.features, "iv_spike_score": round(iv_spike, 4)}
    feature_rows = engineer_features(chain_data, as_of, iv_rank, extra_features=extra)
    all_feature_rows.extend(feature_rows)
    if gate.gated:
      continue
    # Pre-filter: only passing rows go to TabFM; failing rows get fallback.
    for row in feature_rows:
      if not _passes_filters(row):
        all_candidates.append({**row, "pop_predicted": 0.5, "exp_return": 0.0})
        continue
      key = (row["vix_bucket"], row["trend_direction"], row["iv_regime"])
      scoring_groups.setdefault(key, []).append(row)

  # Batch score: fit TabFM once per regime group.
  for group_rows in scoring_groups.values():
    context = build_context(group_rows[0], str(as_of), path=store_path)
    scored = score_candidates_batch(group_rows, context, clf_model, reg_model)
    all_candidates.extend(scored)
```

Then, between the labeling block (`if n_labeled: ...`, currently lines 101-102) and `best = select_trade(...)` (line 104), insert:

```python
  if gate.gated:
    print(f"[EventGate] NO NEW ENTRIES — {'; '.join(gate.reasons)}")
    _log_gated_day(gate.reasons, as_of, db_path)
    print(portfolio_summary(db_path, as_of))
    return None
```

And add next to `_log_recommendation` at the bottom of the file:

```python
def _log_gated_day(reasons: list, as_of: date, db_path: Path) -> None:
  md = Path(db_path).parent / "RECOMMENDATIONS.md"
  header = "# Nightly Recommendations\n\n"
  existing = ""
  if md.exists():
    existing = md.read_text()
    if existing.startswith(header):
      existing = existing[len(header):]
  bullets = "\n".join(f"- {r}" for r in reasons)
  entry = f"## {as_of}\n\nGATED — no new entries.\n{bullets}\n\n"
  md.write_text(header + entry + existing)
```

- [ ] **Step 4: Run the integration test, then the full suite**

Run: `PYTHONPATH=. python3 -m pytest tabfm/trading/tests/test_event_gate_integration.py -q`
Expected: 2 passed

Run: `PYTHONPATH=. python3 -m pytest tabfm/trading/tests/ -q --ignore=tabfm/trading/tests/test_hist_adapter.py --ignore=tabfm/trading/tests/test_live_adapter.py --ignore=tabfm/trading/tests/test_run_nightly.py`
Expected: all pass.

- [ ] **Step 5: Update `docs/NIGHTLY_CLOUD_RUN.md`**

In the fetch step (step 2 of the doc), append a new bullet:

```markdown
   - `get_earnings_calendar` (read-only) for the next 7 days; keep entries
     whose symbol is in MEGA_CAPS (AAPL MSFT NVDA GOOGL AMZN META TSLA AVGO)
     and write them into the snapshot as
     `events: {"earnings": [{"symbol", "date" (YYYY-MM-DD), "when" (bmo|amc|unknown)}]}`.
     Also copy the last ~10 rows of `data/market_history.csv` into the
     snapshot as `vix_history: [[date, vix], ...]` plus today's VIX reading.
     If the earnings fetch fails, omit `events` entirely — the pipeline
     degrades gracefully and reports it.
```

After the pipeline-run step, append:

```markdown
5b. Event gate: when the run prints `[EventGate] NO NEW ENTRIES — ...`, that
   is a correct outcome, not an error. Commit the snapshot/labels as usual
   with message "nightly: <date> — GATED (<first reason>)".

5c. Quarterly (first run of Jan/Apr/Jul/Oct): verify `data/macro_calendar.json`
   against the published FOMC meeting schedule, BLS CPI release schedule, and
   first-Friday jobs report dates for the next two quarters; correct any
   drifted dates in the same commit.
```

- [ ] **Step 6: Commit**

```bash
git add tabfm/trading/run_nightly.py tabfm/trading/tests/test_event_gate_integration.py docs/NIGHTLY_CLOUD_RUN.md
git commit -m "feat(trading): wire event risk gate into nightly run with gated-day logging"
```

---

## Self-Review

- **Spec coverage:** Layer A calendar (Task 1 + seed in Task 2 + snapshot carry in Task 3/5) ✓; Layer B VIX/IV with env thresholds (Task 1) ✓; Layer C features + FEATURE_COLS (Task 4) ✓; gate blocks entries only, always audits/labels/appends/summarizes (Task 5 integration + test) ✓; RECOMMENDATIONS.md gated entries (Task 5) ✓; market history persistence (Task 2/5) ✓; snapshot schema events/vix_history (Task 3) ✓; docs + quarterly refresh (Task 5) ✓; degraded fail-open (Tasks 1, 5) ✓; master switch (Task 1) ✓; backtest limits documented (HistAdapter returns empty earnings — layer A inactive, Task 3) ✓.
- **Placeholders:** none; every step carries code or exact file content. The one intentional adaptation note (feature_engineer fixture naming) tells the implementer exactly what to check and keep invariant.
- **Type consistency:** `GateResult.features` keys match `engineer_features` defaults and `FEATURE_COLS` additions; `market_history` row dicts match Task 5 usage; adapter method names match Task 5 call sites; `vix_history` shape `[[date_str, float]]` consistent across Tasks 1, 3, 5.
