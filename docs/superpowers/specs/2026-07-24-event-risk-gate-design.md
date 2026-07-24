# Event Risk Gate — Design Spec

**Date:** 2026-07-24
**Status:** Approved for planning

## Problem

Good per-day predictions get broken by scheduled and market-wide events: a
mega-cap earnings call (GOOGL, 2026-07-22 AMC) dropped QQQ 1.9% the next
session and dragged SPY with it. The index-ETF watchlist (SPY/QQQ/IWM) never
has "its own" earnings, so the existing per-ticker `earnings_flag` (currently
hardcoded to `no_earnings`) cannot catch this. The user prefers not entering
new positions on high-event-risk days over squeezing out extra premium.

## Solution overview — three layers

| Layer | Mechanism | Catches | Active |
|---|---|---|---|
| A. Calendar gate | Mega-cap earnings (Robinhood MCP) + static macro calendar | Known scheduled events | v1, live runs |
| B. Market-priced gate | VIX spike velocity + IV-vs-HV anomaly | Surprises calendars miss | v1, live + backtest |
| C. Learned features | Event-context columns stored on every row | Graduated model response | records in v1; model uses it once history spans event cycles |

A and B are binary gates on NEW ENTRIES only. Position management (audit,
early close, labeling) always runs.

## Component 1: `tabfm/trading/pipeline/event_gate.py`

```python
@dataclass
class GateResult:
  gated: bool
  reasons: list[str]      # human-readable, e.g. "GOOGL reports today (AMC)"
  degraded: bool          # True when earnings data was unavailable
  features: dict          # strategy-C feature values for this session

def evaluate_event_gate(
  events: dict,            # snapshot["events"]: {"earnings": [{symbol, date, when}]}
  macro_calendar: list,    # loaded from data/macro_calendar.json
  vix_history: list,       # [[date, value], ...] most recent last
  chain_stats: dict,       # {"median_iv": float, "hv20": float, "prev_median_iv": float | None}
  as_of: date,
) -> GateResult
```

`chain_stats` uses a market proxy: SPY's chain when present, else the first
watchlist ticker. `GateResult.features` carries the session-global values
(`days_to_next_megacap_earnings`, `days_to_next_macro_event`,
`vix_5d_change`); the per-ticker `iv_spike_score` is computed in the
run loop (ticker's median chain IV / that ticker's hv20) and merged into
rows alongside the global features via a new
`engineer_features(..., extra_features: dict)` parameter. Day distances:
0 = event on as_of, 1 = next trading session.

### Layer A rules

- `MEGA_CAPS = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AVGO"]`
  (module constant).
- Danger window: gate when a mega-cap earnings event or macro event lands on:
  - `as_of` itself (AMC reports hit the next open — still gate), or
  - the next trading session (skip weekends; holidays approximated by
    weekday rule in v1).
- Macro calendar: `data/macro_calendar.json`, committed. Schema:
  `[{"date": "2026-07-29", "event": "FOMC rate decision"}, ...]`.
  Seeded with 2026 FOMC decision dates, CPI release dates, and NFP Fridays.
  The cloud agent refreshes it quarterly (instruction added to
  `docs/NIGHTLY_CLOUD_RUN.md`).

### Layer B rules

- VIX spike: `(vix_now - vix_5_sessions_ago) / vix_5_sessions_ago > 0.15` →
  gated, reason "VIX +X% in 5 sessions".
- IV spike: `median_iv / hv20 > 1.6` AND `median_iv > prev_median_iv * 1.2`
  → gated, reason "options pricing an event (IV/HV=X, IV +Y% d/d)".
  Level alone never gates; `prev_median_iv=None` disables this check.
- Thresholds via env: `TABFM_GATE_VIX_5D` (default 0.15),
  `TABFM_GATE_IV_HV` (1.6), `TABFM_GATE_IV_JUMP` (1.2).
- Master switch: `TABFM_EVENT_GATE=off` → always `gated=False` (still
  computes features).

### Degraded mode

events=None or a payload missing the "earnings" key → degraded=True; a well-formed {"earnings": []} is a quiet calendar, not degraded.
Layers B + macro still evaluated, and the nightly report prints
`[EventGate] DEGRADED — earnings calendar unavailable`. Fail-open by design:
data hiccups must not silently halt trading; the warning is visible in every
report the user reads.

## Component 2: pipeline integration (`run_nightly.py`)

Order of operations on every run:

1. audit positions (unchanged)
2. fetch chains, engineer features (unchanged)
3. `gate = evaluate_event_gate(...)`
4. append feature rows + label expired rows (unchanged — always happens)
5. if `gate.gated`: print `[EventGate] NO NEW ENTRIES — <reasons>`, write a
   gated-day entry to `RECOMMENDATIONS.md`
   (`## <date>\n\nGATED — no new entries.\n- <reason>...`), print portfolio
   summary, return None
6. else: scoring, calibration, selection, execution as today

VIX persistence: each live run appends `(as_of, vix, median_iv)` to
`data/market_history.csv` (deduped by date; stores date, vix, and SPY median IV). Backtests pass HistAdapter's
`^VIX` series instead. `SnapshotAdapter` gains a `vix_history` passthrough
if present in the snapshot; the run falls back to `data/market_history.csv`.

## Component 3: Strategy-C features

Added to every feature row (feature_engineer) and to `FEATURE_COLS`
(tabfm_scorer):

| Column | Definition |
|---|---|
| `days_to_next_megacap_earnings` | trading-day distance to nearest MEGA_CAPS report; 99 when none within 21 calendar days or data unavailable |
| `days_to_next_macro_event` | same for macro calendar |
| `vix_5d_change` | real value (replaces hardcoded 0.0): `(vix - vix_5_sessions_ago) / vix_5_sessions_ago` |
| `iv_spike_score` | `median_chain_iv / hv20` for the ticker |

Existing stored rows lack these columns → NaN → handled by TabFM's
SimpleImputer. No gating decision uses model output in v1.

## Component 4: snapshot + fetch changes

- Snapshot schema addition: top-level `"events": {"earnings": [{"symbol",
  "date" (YYYY-MM-DD), "when" ("bmo"|"amc"|"unknown")}]}` and optional
  `"vix_history": [[date, value], ...]`.
- Fetch step (manual agent runs and `docs/NIGHTLY_CLOUD_RUN.md`): call
  `get_earnings_calendar` (read-only MCP) for the next 7 days, filter to
  MEGA_CAPS, write into the snapshot. Also fetch/carry forward VIX history.
- `docs/NIGHTLY_CLOUD_RUN.md` additions: earnings fetch step, macro calendar
  quarterly refresh instruction, gated-day reporting requirement.

## Backtest behavior (honest limits)

- Layer A: inactive (no historical earnings calendar in v1). Documented.
- Layer B: active via HistAdapter `^VIX` (vix_5d_change) and synthetic chain
  IV (iv_spike_score); expected to gate genuinely volatile windows.
- Layer C: features computed where data exists; NaN elsewhere.

## Testing

- Unit — window logic: AMC on as_of gates; BMO next session gates; event 3+
  sessions out does not; Friday→Monday weekend skip.
- Unit — thresholds: VIX +16% gates, +14% doesn't; IV/HV 1.7 with +25% jump
  gates; 1.7 stable doesn't; `prev_median_iv=None` disables.
- Unit — degraded mode and `TABFM_EVENT_GATE=off`.
- Unit — feature computation incl. the 99 sentinel.
- Integration — gated night: no journal insert, rows still appended, expired
  rows still labeled, summary still printed, RECOMMENDATIONS.md gets the
  gated entry.
- Full suite stays green.

## Out of scope (v1)

- Pre-event de-risking of open positions (tighten early-close before events)
- Historical earnings calendar for backtesting layer A
- News/LLM sentiment scoring
- Per-ticker earnings gating for single-stock watchlist expansion
- Market holiday calendar (weekday approximation only)
