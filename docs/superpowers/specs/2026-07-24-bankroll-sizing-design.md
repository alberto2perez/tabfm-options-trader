# Bankroll-Managed Position Sizing — Design Spec

**Date:** 2026-07-24
**Status:** Approved for planning

## Problem

The pipeline's risk limits are static dollar caps (`_MAX_RISK = $1,000` per
trade, `_MAX_PORTFOLIO_RISK = $1,500` open) with no notion of the user's
actual capital ($1,500–2,000). A single position could commit >50% of the
bankroll; after losses the system kept sizing as if nothing happened. The
user wants capital sliced so the account survives losing streaks and always
retains margin to recover.

Decisions made during brainstorming:
- Bankroll = **tracked equity** (starting capital + realized P&L from the journal)
- **15%** of current equity max risk per trade
- **45%** of current equity max total open risk (≈ 3 concurrent slices)
- Fixed-fractional (anti-martingale) sizing with a **drawdown circuit breaker**
- Kelly-derived sizing deferred to v2, gated on calibrator maturity (~50+ closed trades)
- The prior paper book was reset (commit `8dff93b`) so the strategy starts clean

## Component 1: `tabfm/trading/pipeline/bankroll.py`

```python
@dataclass
class Bankroll:
  starting: float        # configured starting capital
  realized: float        # sum of closed trades' actual_pnl
  equity: float          # starting + realized (floored at 0)
  peak_equity: float     # max of the running equity walk (see below)
  drawdown_pct: float    # (peak_equity - equity) / peak_equity, 0 when peak == 0
  recovery_mode: bool    # drawdown_pct > brake threshold
  slice_limit: float     # equity * risk_per_trade * (0.5 if recovery_mode else 1.0)
  exposure_limit: float  # equity * max_exposure

def get_bankroll(db_path: Path = _DEFAULT_DB) -> Bankroll
```

Rules:
- **Equity walk / peak:** order closed trades by `date_closed` (tie-break by
  `trade_id`), walk `starting + cumsum(actual_pnl)`, `peak_equity` = max of
  the walk including the starting point. Recomputed from the journal every
  call — no separate state file. Open positions are NOT marked to market;
  equity moves only on realized outcomes.
- **Recovery mode:** `drawdown_pct > TABFM_DRAWDOWN_BRAKE` → `slice_limit`
  uses half the normal fraction. Exit only when equity sets a new all-time
  high (drawdown returns to 0).
- **Floor:** `equity <= 0` → `slice_limit = 0`, `exposure_limit = 0` (no
  trade can ever size).
- Env config, read at call time: `TABFM_STARTING_CAPITAL` (default `2000`),
  `TABFM_RISK_PER_TRADE` (`0.15`), `TABFM_MAX_EXPOSURE` (`0.45`),
  `TABFM_DRAWDOWN_BRAKE` (`0.25`).

## Component 2: sizing in `trade_recommender.py`

- **Remove** `_MAX_RISK`, `_MAX_PORTFOLIO_RISK`, and the
  `TABFM_MAX_PORTFOLIO_RISK` env var.
- `select_trade(scored_candidates, open_trades=None, bankroll: Bankroll | None = None)`.
  With `bankroll=None` (legacy callers/tests), behave as if equity were the
  default starting capital with no closed trades.
- `open_risk` = sum of open trades' `max_loss` (journal dollars, as today).
- `budget = min(bankroll.slice_limit, bankroll.exposure_limit - open_risk)`.
- Per candidate: `per_contract_loss = (spread_width_dollars - entry_credit) * 100`;
  `contracts = min(floor(budget / per_contract_loss), _MAX_CONTRACTS)`;
  candidates with `contracts < 1` are skipped. `_MAX_CONTRACTS = 10` stays.
- Filter gauntlet change: the old `spread_width_dollars * 100 > 1000` check
  is removed from `_passes_filters` (its job — "one contract must fit the
  budget" — is now done exactly by the sizing step). All other gauntlet
  checks unchanged. Dedup rule unchanged.
- `run_nightly.run()` calls `get_bankroll(db_path)` once per run and passes
  it to `select_trade`.

## Component 3: visibility

`portfolio_summary` gains a bankroll block sourced from `get_bankroll`:

```
  BANKROLL: equity $1,850  (start $2,000 · realized −$150)
  Peak $2,100 · drawdown 11.9% · mode NORMAL
  Slice $277 · exposure cap $832 ($550 open · $282 free)
```

`mode RECOVERY (slice halved)` when the breaker is tripped. The block appears
in every run output (trade, no-trade, and gated nights).

## Behavior consequences (accepted)

- **Backtests compound**: sizing follows the equity path, so results are
  path-dependent and not comparable to prior fixed-cap backtests.
- **$5-wide spreads** typically size to 1 contract at $2k equity ($275
  per-contract loss vs $300 slice); expected premium income drops roughly by
  half versus the old 2-contract sizing — this is the survival trade-off the
  user chose.
- ~3 concurrent positions max at 45% exposure; entries pause when the book
  is full until a position closes.

## Testing

- Unit — bankroll math: empty journal (equity = starting, peak = starting,
  no recovery); winning walk raises peak; losing walk triggers recovery at
  >25% drawdown; recovery exits only on new all-time high; equity floor at 0;
  env overrides.
- Unit — sizing: slice caps contracts; exposure budget caps contracts;
  `contracts < 1` skips; recovery mode halves the slice; `bankroll=None`
  legacy default; `_MAX_CONTRACTS` still binds.
- Update existing `trade_recommender` tests that reference the removed
  static caps.
- Integration — seeded journal with losses: next run sizes smaller;
  portfolio summary contains the bankroll block.
- Full suite stays green.

## Out of scope (v1)

- Kelly/half-Kelly sizing from calibrated POP (v2, needs calibrator maturity)
- Marking open positions to market for equity purposes
- Broker-synced buying power
- Per-regime or per-ticker slice differentiation
