# IV-Rank Trailing Window — Design Spec

**Date:** 2026-07-24
**Status:** Approved for planning

## Problem

`compute_iv_rank(current_vix, path)` ranks today's VIX against the *history
store's accumulated `vix_level` rows* — which start empty and grow only
during a run. In backtests it is near-zero for the whole window (short,
self-referential history); even live it drifts as ancient regimes pile up.
Standard IV rank uses a **fixed 252-trading-day trailing window**. The
current behavior makes the IV-rank ≥ 30 entry gate far more restrictive than
intended: the 2026-04→07 backtest window (VIX flat at 20, then falling to 16)
produced iv_rank ≈ 0 every day, so the strategy placed 1 trade in 64 days —
correct discipline reading a broken input.

Confirmed: with a proper trailing series, VIX 16 against a year spanning
12–25 yields a real mid-range rank (~30–50), not 0.

## Solution

### 1. `adapter.get_vix_series(as_of, days=252) -> list[float]`

Trailing daily VIX closes on/before `as_of`, oldest→newest, at most `days`.

- `DataAdapter` (base): default `return []`.
- `HistAdapter`: `_history("^VIX", lookback=400)`, filter `index <= as_of`,
  return the last `days` `Close` values as floats. (400 calendar days covers
  ≥ 252 trading days.)
- `SnapshotAdapter`: return `self._s.get("vix_series") or []` (a plain
  `list[float]` the fetch step provides; already built ≤ as_of).

### 2. `compute_iv_rank(current_vix, vix_series) -> float`

Rework in `history_store.py`:

```python
def compute_iv_rank(current_vix: float, vix_series: list[float]) -> float:
  clean = [float(v) for v in vix_series if v is not None]
  if len(clean) < 30:
    return 50.0  # not enough history → neutral (cold-start friendly, still trades)
  below = sum(1 for v in clean if v < current_vix)
  return round(below / len(clean) * 100, 2)
```

Signature CHANGES from `(current_vix, path)` to `(current_vix, vix_series)`.
The store's `vix_level` column stays as a stored feature but no longer drives
IV rank. `load_store` import in history_store is retained for other helpers.

### 3. `run_nightly` wiring

Replace `iv_rank = compute_iv_rank(vix_now, store_path)` with
`iv_rank = compute_iv_rank(vix_now, adapter.get_vix_series(as_of))`. Computed
once per run (VIX is market-wide), reused for all tickers — same as today.

### 4. Snapshot schema + fetch

- Snapshot gains top-level `"vix_series": [float, ...]` — ~252 trailing daily
  VIX closes on/before the snapshot date.
- Manual snapshot builder and `docs/NIGHTLY_CLOUD_RUN.md` fetch step: populate
  `vix_series` from **yfinance `^VIX`** (offline, no MCP, no VIXY proxy) — real
  VIX history. Documented as the accurate source (partial credit toward the
  backlog's "remove VIXY×10 hack").

## Config

No new env vars. (Gate threshold `TABFM_MIN_IV_RANK=30` unchanged.)

## Testing

- `compute_iv_rank`: percentile math (e.g. current above 40% of a 100-point
  series → 40.0); `< 30` points → 50.0; None values skipped; a mid-range VIX
  (16) in a wide series (12–25) yields ≥ 30 (unblocks the gate).
- `get_vix_series`: base default `[]`; `SnapshotAdapter` passthrough of
  `vix_series` (and `[]` when absent). (HistAdapter's yfinance path is
  network-bound → exercised by the live backtest, not unit-tested, consistent
  with existing HistAdapter test exclusion.)
- Integration: `run_nightly` path uses the adapter series (a `SnapshotAdapter`
  with a seeded `vix_series` yields a non-neutral iv_rank feeding the gate).
- Full suite (161) stays green; update any test calling
  `compute_iv_rank(..., path)` to the new series signature.

## Post-build (not part of the build)

Discover the vol-event window: fetch `^VIX` for the past year, locate the
largest spike, re-run the backtest over that period, and report the model
arm's trade count, model-vs-baseline, and the turning-point section.

## Out of scope

Per-ticker IV rank (VIX is a market proxy — documented approximation kept);
removing the `vix_level` store column; VIXY-proxy cleanup in the direct
LiveAdapter path (snapshot path uses real ^VIX).
