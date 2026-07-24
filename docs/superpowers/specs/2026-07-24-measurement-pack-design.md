# Measurement Pack — Design Spec

**Date:** 2026-07-24
**Status:** Approved for planning

Purpose: make every number the system reports and compounds on verifiable —
realistic fills, a dumb-baseline benchmark, and calibrated return
predictions. User context: this pack is the precondition for transitioning
to real money; the paper book must be trustworthy first. User accepted the
approximate friction model over exact exit-time repricing.

## Feature 1 — Friction model (realistic fills)

Applied inside `execute_paper_trade` so every arm gets it:

```
combined_spread = bid_ask_pct × mid_credit          # per-share dollars
adjusted_credit = round(max(
    mid_credit − slip_frac × combined_spread − fees_rt / 100.0,
    0.01), 2)
```

- `slip_frac` = `TABFM_SLIPPAGE_FRAC` (default `0.50`) — half the combined
  two-leg spread across the round trip (~25% each way).
- `fees_rt` = `TABFM_FEES_RT` (default `0.20` dollars per contract round
  trip; Robinhood has no commission but regulatory fees are real). Divided
  by 100 to convert to per-share credit units.
- Journal `entry_credit` stores the ADJUSTED credit; `max_loss`/`max_profit`
  derive from it, so P&L, the bankroll walk, the calibrators, and Brier all
  compound on realistic numbers automatically.
- New journal column `entry_credit_mid REAL` (migration) preserves the
  pre-friction mid for later slippage-impact analysis.
- The recommendation card shows both: `Entry Credit $2.14 est. fill (mid $2.25)`.
- Known approximations (accepted): sizing in `select_trade` uses the
  unadjusted mid credit (~2% conservative skew); held-to-expiry positions
  pay the exit half-spread they wouldn't in reality (conservative bias).
- `bid_ask_pct` may be absent in legacy/synthetic rows → treat combined
  spread as 0 (fees still apply).

## Feature 2 — Dumb baseline arm (shadow book)

- Journal column `strategy TEXT` (migration; backfill existing NULL rows to
  `'model'`; inserts default to `'model'`).
- Journal helpers gain a filter: `get_open_trades(path, strategy="model")`
  and `get_all_closed_trades(path, strategy="model")`; passing
  `strategy=None` returns all rows. Consequences (the isolation matrix):
  - bankroll walk, dedup, exposure, Platt + return calibrators, Brier:
    default `"model"` — baseline can NEVER touch sizing or learning.
  - position_auditor: audits `strategy=None` (both books get stop/target/
    DTE/expiry management). Closed baseline trades flow to the tracker only.
- Baseline entry, EVERY run (even gated nights — it is deliberately dumb),
  in `run_nightly` after the audit and chain fetch: from the SPY chain data
  (skip silently if SPY absent), choose the PUT with delta closest to 0.30
  (within 0.15–0.40), long = adjacent strike below, 1 contract, same
  friction model, `strategy='baseline'`. No dedup — the dumb strategy
  stacks positions; that is its nature. Uses `execute_paper_trade` with a
  `strategy` passthrough; no RECOMMENDATIONS.md entry (shadow book), just a
  one-line print `[Baseline] sold SPY <short>/<long> exp <expiry> ($X fill)`.
- accuracy_tracker `report()`: when baseline closed trades exist, add
  `baseline_trades`, `baseline_pnl`, `baseline_win_rate`,
  `model_vs_baseline_pnl` (model realized − baseline realized) to metrics
  and a verbose section. NOTE: arms are compared on realized P&L; baseline
  is fixed 1-contract so this is a per-occurrence comparison, not
  capital-normalized — documented.
- portfolio_summary: one line under the closed stats:
  `BASELINE (shadow): N open · M closed · P&L $X  (model −  baseline: $Y)`.

## Feature 3 — exp_return calibration

- Journal column `exp_return_raw REAL` (migration); `paper_executor` stores
  it from `trade.get("exp_return_raw", trade["exp_return"])`.
- `calibrator.py` gains:
  ```python
  def fit_return_calibration(db_path=_DEFAULT_DB, min_trades=25) -> tuple[float, float] | None
  def calibrate_return(exp_return: float, params: tuple[float, float]) -> float
  ```
  Fit: closed MODEL trades with `max_loss > 0`; x = predicted return
  (`exp_return_raw` when present else `exp_return`), y = realized fraction
  `actual_pnl / max_loss`; least-squares line (`numpy.polyfit(x, y, 1)`).
  Returns None below `min_trades` or when x has no variance.
- `run_nightly`: after the Platt block, fit once per run; for non-fallback
  candidates set `exp_return_raw` = model value and `exp_return` =
  `round(calibrate_return(raw, params), 4)`. Identity (no change) when
  params is None. EV ranking then uses calibrated POP × calibrated return.

## Config summary

| Env var | Default | Meaning |
|---|---|---|
| `TABFM_SLIPPAGE_FRAC` | 0.50 | fraction of combined bid/ask spread lost round-trip |
| `TABFM_FEES_RT` | 0.20 | $ per contract round-trip regulatory fees |

## Testing

- Friction: exact adjusted-credit arithmetic (incl. floor at 0.01 and
  missing bid_ask_pct), env overrides, journal stores adjusted +
  entry_credit_mid, card shows both numbers.
- Baseline: entered on normal AND gated nights; 1 contract; strategy
  column set; stacking allowed; bankroll/exposure/dedup unaffected by open
  baseline positions (isolation matrix tests); auditor closes baseline
  trades; tracker and summary report the comparison.
- Return calibration: identity below min_trades; slope/intercept recovered
  from a seeded journal with known linear bias; applied only to
  non-fallback candidates; exp_return_raw persisted.
- Full suite (135) stays green.

## Out of scope

Exact exit-time slippage repricing; capital-normalized baseline comparison;
gated-baseline arm; risk pack and ops pack (next sub-projects).
