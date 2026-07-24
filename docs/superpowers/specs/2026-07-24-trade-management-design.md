# Trade Management v1 — Design Spec

**Date:** 2026-07-24
**Status:** Approved for planning

Three features chosen from the trader-analyst review (the rest are logged in
`docs/BACKLOG.md`): loss-side exits, entry-quality conditions, and a
market-POP benchmark column.

## Feature A — Loss-side exits (position_auditor)

Today the auditor takes profits at 50% of max but lets losers ride to expiry
(full max loss). Add, in this order of evaluation per open trade each night:

1. **Expiry settlement** (existing, unchanged) — `as_of >= expiry`.
2. **Stop-loss:** when the spread's real mark (`_spread_mark`) costs
   `>= stop_mult × entry_credit` to close, close it:
   `pnl = (credit − current_val) × contracts × 100`, status `"stopped"`.
   `stop_mult` from env `TABFM_STOP_LOSS_MULT` (default `2.0`).
3. **Profit target** (existing, unchanged) — unrealized ≥ 50% of max profit
   → `"partial"`.
4. **21-DTE management:** when `(expiry − as_of).days <= manage_dte` and the
   position is still open, close at mark: status `"partial"` when pnl ≥ 0
   else `"stopped"`. `manage_dte` from env `TABFM_MANAGE_DTE` (default `21`).

Rules 2 and 4 only fire when a real mark is available (`_spread_mark` not
None); the intrinsic fallback cannot price time value, so it must never
trigger a stop. Honest limit: in backtests the synthetic chain's expiries
rarely align with an open trade's expiry, so marks are usually unavailable
and stop/21-DTE rules seldom fire there — they are live-path features.

**MFE/MAE tracking:** every audit visit computes unrealized P&L from the
mark and persists per-trade extremes: journal columns `mfe REAL` (max
favorable excursion, dollars) and `mae REAL` (max adverse, ≤ 0). New helper
`update_excursions(trade_id, mfe, mae, path)` in journal.py; migration via
the existing `PRAGMA table_info` pattern in `init_db`. Purpose: after ~50
trades, tune the 50% target and 2× stop empirically.

**Status vocabulary:** `"stopped"` joins `won/partial/lost`. Consumers:
- calibrator: outcome 0 (already generic: `status in ("won","partial")` = 1)
- accuracy_tracker and portfolio_summary: count `stopped` as a loss
  (`losses = status in ("lost", "stopped")`); win rate denominators unchanged

## Feature B — Entry-quality conditions (filter gauntlet)

Two new checks in `_passes_filters` (env read at call time):

- **Credit floor:** reject when
  `entry_credit / spread_width_dollars < TABFM_MIN_CREDIT_RATIO`
  (default `0.30` — slightly relaxed from the classical ⅓ so 30-delta
  $1-wides (~0.30–0.35 ratio) remain viable in recovery mode).
  Skip the check when `entry_credit` is absent (synthetic test rows).
- **IV-rank gate:** reject when `iv_rank < TABFM_MIN_IV_RANK` (default
  `30.0`). Cold-start neutrality preserved: `compute_iv_rank` returns 50
  when the store lacks history, so fresh systems still trade. Selling
  premium only when premium is rich is the core structural edge; the
  90-day backtest's worst regime ("cheap IV") already showed this.

## Feature C — Market-POP benchmark (`pop_market`)

Robinhood option quotes carry `chance_of_profit_short` — the market's own
POP estimate. Log it beside TabFM's prediction and let the accuracy tracker
compare Brier scores; after ~50 closed trades this answers "does TabFM beat
the free market number?".

- Snapshot chain rows gain optional `pop_market` (fetch step maps the SHORT
  leg's `chance_of_profit_short`; documented in NIGHTLY_CLOUD_RUN.md).
- `engineer_features` copies the short leg's `pop_market` into the row when
  the chain column exists (else `None`). NOT added to `FEATURE_COLS` — it
  is a benchmark, not an input; feeding it to the model would let it copy
  the market instead of beating it.
- Journal column `pop_market REAL` (migration); paper_executor records it.
- accuracy_tracker: when ≥1 closed trade has `pop_market`, report
  `brier_tabfm = mean((pop_predicted − outcome)²)` vs
  `brier_market = mean((pop_market − outcome)²)` over trades that have both
  (outcome = 1 for won/partial, 0 for lost/stopped), plus the counts.
- Synthetic backtest chains have no `pop_market` → None → comparison
  silently absent there.

## Config summary

| Env var | Default | Meaning |
|---|---|---|
| `TABFM_STOP_LOSS_MULT` | 2.0 | close when mark ≥ mult × credit |
| `TABFM_MANAGE_DTE` | 21 | close open positions at/under this DTE |
| `TABFM_MIN_CREDIT_RATIO` | 0.30 | min entry_credit / width |
| `TABFM_MIN_IV_RANK` | 30.0 | min IV rank to sell premium |

## Testing

- Auditor: stop fires at 2× credit mark (status stopped, correct pnl);
  profit target unchanged; 21-DTE closes with partial (profit) and stopped
  (loss); no stop from intrinsic fallback (mark unavailable); MFE/MAE
  persist and only widen; env overrides.
- Gauntlet: ratio 0.29 rejected / 0.31 passes; iv_rank 29 rejected / 31
  passes; missing entry_credit skips the ratio check; env overrides.
- pop_market: engineer copies from chain when present, None when absent;
  executor persists; tracker reports both Briers with a seeded journal and
  omits the section when no pop_market exists.
- Existing suite (114) stays green; fixtures updated where the new gauntlet
  checks apply (fixture iv_rank values must be ≥ 30 or set explicitly).

## Out of scope (v1) — see docs/BACKLOG.md

Correlation-aware exposure, slippage/commissions, exp_return calibration,
naive baseline arm, VIX term structure, LiveAdapter parity, assignment/pin
risk, Kelly sizing, roll management.
