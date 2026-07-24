# Backtest Realism + Midday Audit — Design Spec

**Date:** 2026-07-24
**Status:** Approved for planning

Two independent features decided together. (A) Make the backtest's synthetic
option chain match the shape of the real market the entry filters were tuned
for, and add a turning-point report — because the backtest's real value is
behavior at direction changes, not P&L. (B) A midday audit-only pass so a
fast morning move doesn't overshoot the 2× stop before the nightly close.

Context: the 2026-07-24 full-stack backtest placed 1 model trade in 64 days
(credit floor rejected ~29/32 DTE-valid candidates because synthetic $7-wide
spreads price just under the 0.30 credit/width floor) while the dumb baseline
"won" 100% (Black-Scholes with pure realized vol has no risk premium). Both
numbers describe the fake market's mismatch, not the strategy.

## Feature A — Synthetic chain realism (`adapters/historical.py`)

- **Fixed dollar strike grid** replacing `_STRIKE_RANGE` (1%-of-spot):
  ```python
  def _strike_grid(S: float) -> list[float]:
    step = 5.0 if S >= 50.0 else 1.0
    lo = step * round(S * 0.85 / step)
    hi = step * round(S * 1.15 / step)
    n = int(round((hi - lo) / step)) + 1
    return [round(lo + i * step, 2) for i in range(n)]
  ```
  `$5` grid for large underlyings (SPY/QQQ), `$1` for small (IWM). In
  `get_options_chain`, iterate this grid instead of `_STRIKE_RANGE`.
- **IV carries the variance-risk premium.** Replace
  `sigma = max(u["hv20"], 0.05)` with
  `sigma = max(u["hv20"] * _iv_premium(), 0.05)` where
  `_iv_premium()` reads `TABFM_BACKTEST_IV_PREMIUM` (default `1.25`).
  The `iv` field written on each row uses the same `sigma`.
- Acceptance: a 30-delta (±0.03) $5-wide SPY put spread built from the
  generated chain has `entry_credit / spread_width` in `[0.30, 0.55]` and
  passes `_passes_filters` (given `iv_rank >= 30`). Tested at a representative
  spot (e.g. S≈740, hv20≈0.15).
- Honest limit (documented): this raises credits toward realism but the model
  is still Black-Scholes with flat vol across strikes (no skew); good enough
  for behavior/turning-point analysis, not for absolute-P&L claims.

## Feature A2 — Turning-point report (`pipeline/turning_points.py`)

Read-only analytics over the history store (per-day `trend_direction` per
ticker) and the journal (closed trades, equity walk). Printed after the
backtest's accuracy tracker.

```python
def turning_point_report(
  store_path: Path, db_path: Path, ticker: str = "SPY", verbose: bool = True
) -> dict
```

- **Flip detection:** dedup store rows to one `trend_direction` per (date)
  for `ticker` (ordered by date); a flip is any date where trend differs from
  the previous distinct trend (e.g. uptrend→sideways, sideways→downtrend).
- **Per flip, report:** flip date + from→to; model trades entered in the 5
  trading days BEFORE the flip and their realized status (were we positioned
  into the reversal?); equity drawdown over the 10 days AROUND the flip
  (from the closed-trade equity walk, reusing `get_bankroll`'s walk logic via
  a shared helper or recomputation); whether recovery mode was active in that
  window.
- **Returns / prints:** `{"flips": [...], "n_flips": int,
  "trades_into_reversals": int, "worst_flip_drawdown": float}` and a boxed
  verbose section. Silent/empty section when the store has < 2 distinct
  trends. No new columns; no writes.

## Feature B — Midday audit-only pass

- `run_nightly.run_audit_only(adapter, as_of, db_path=_DEFAULT_DB, store_path=_DEFAULT_STORE) -> list[dict]`:
  `init_db` → `closed = audit_positions(adapter, as_of, db_path)` (both books,
  all four exit rules — the stop-loss is the point) → print closed count →
  print `portfolio_summary`. NO `fetch_chains` scoring, NO event gate, NO
  baseline entry, NO `select_trade`. Returns the closed list.
- Entry point `tabfm/trading/run_audit.py` (`python -m tabfm.trading.run_audit`):
  builds `LiveAdapter` the same way `run()` does (dotenv + rh.login), calls
  `run_audit_only`. A `--snapshot PATH` path uses `SnapshotAdapter` instead
  (how it runs in-session / cloud).
- `docs/NIGHTLY_CLOUD_RUN.md` gains a "Midday audit (~12:30pm ET)" section:
  query open positions (`get_open_trades(db, strategy=None)`), fetch CURRENT
  option marks + underlying only for their distinct tickers, build a
  light snapshot (chains + underlyings + closes; no events/vix_history
  needed), and run `run_audit_only`. Commit only if something closed.
- Honest limit (documented): at midday the DTE, profit-target, and expiry
  rules give the same verdict as the nightly close (same date/thresholds);
  the midday pass's only added value is catching a stop-loss breach hours
  earlier on a fast day.

## Config summary

| Env var | Default | Meaning |
|---|---|---|
| `TABFM_BACKTEST_IV_PREMIUM` | 1.25 | synthetic IV = hv20 × this (variance-risk premium) |

## Testing

- Chain: `_strike_grid` spacing ($5 large / $1 small, covers 0.85–1.15×);
  `TABFM_BACKTEST_IV_PREMIUM` scales the `iv`/pricing; the acceptance test
  (30-delta $5 SPY put spread credit/width in [0.30,0.55], passes gauntlet);
  env override.
- Turning points: seeded store with a known uptrend→downtrend flip and a
  trade entered just before → report flags 1 flip and 1 trade-into-reversal;
  empty/degraded when < 2 distinct trends.
- Midday audit: `run_audit_only` closes a stop-breached open position, places
  NO new trade (journal model/baseline counts unchanged except the close),
  prints the summary; returns the closed list.
- Full suite (153) stays green.

## Out of scope

Vol skew in the synthetic chain; event-triggered (vs fixed) intraday checks;
capital-normalized baseline; risk pack and ops pack.
