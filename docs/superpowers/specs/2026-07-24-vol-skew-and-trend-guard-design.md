# Volatility Skew + Trend Guard — Design Spec

**Date:** 2026-07-24
**Status:** Autonomous build (user pre-authorized: "do as much as possible
autonomously, take your best assumptions, I won't be here to answer").
Assumptions are marked **[A]** and are my calls, documented for later review.

Two features requested together:
1. **Vol skew** in the synthetic backtest chain — so backtests better reflect
   the risk that hurt short-premium strategies (put IV richer than call IV,
   and closing costs that rise during selloffs). Goal: more useful backtests,
   not a P&L oracle.
2. **Trend guard** — fill the gap that nothing in the system responds to a
   trend flip against an open position. Deliver an explicit nightly advisory:
   "this open spread is now directionally challenged — here's what to do."

---

## Feature 1 — Volatility skew (`adapters/historical.py`)

Today `_synthetic_chain` uses one flat `sigma = hv20 × iv_premium` across all
strikes. Real index options carry a **skew**: OTM puts trade at higher IV
than ATM, which trade higher than OTM calls. This makes put-spread credits
richer and — critically — makes the modeled *cost to close* a short put rise
as spot falls toward the strike, partially capturing the selloff risk the
flat model missed.

Per-strike volatility:

```
base_sigma = max(hv20 * _iv_premium(), 0.05)   # unchanged base
moneyness  = K / S
skew_factor = max(1 + _skew_slope() * (1 - moneyness), 0.4)
sigma_K    = max(base_sigma * skew_factor, 0.05)
```

- `_skew_slope()` reads `TABFM_BACKTEST_SKEW`, **[A] default 2.5** — a 10%-OTM
  put (moneyness 0.9) gets ~1.25× ATM IV, a 10%-OTM call ~0.75×. Steep-ish but
  defensible for an equity-index smirk; env-tunable.
- `sigma_K` drives `_bs_price`, `_bs_delta`, AND the written `iv` field per row
  (each strike now has its own IV, as in reality).
- The `0.4` floor keeps far-OTM calls from going to near-zero IV.
- **[A]** Skew slope is constant (does not dynamically steepen with the vol
  level in v1). Honest limit documented; the moneyness effect already makes
  puts cost more as spot drops, which is the main benefit.

Acceptance: for S=740, `sigma(680 put) > sigma(740 ATM) > sigma(800 call)`;
a 30-delta $5 put spread's credit is **higher** than under flat vol (skew
enriches put premium); a 30-delta $5 put spread still passes `_passes_filters`.

Note: with per-strike IV, the delta of each strike shifts, so 30-delta
selection lands on slightly different strikes — realistic and expected.

---

## Feature 2 — Trend guard (`pipeline/trend_guard.py`)

Currently `trend_direction` is only an entry-side feature; **nothing acts when
the trend flips against an open position.** Trend guard adds an *advisory*
(not an auto-action) that runs each night and at midday.

`assess_trend_risk(open_trades, adapter, as_of) -> list[dict]`

For each open trade:
- Underlying trend via `adapter.get_underlying(...)` → `_trend_direction(close,
  sma20, sma50)` (reused from feature_engineer).
- **Adverse** trend: `put_spread` (bullish/neutral) challenged by `downtrend`;
  `call_spread` (bearish/neutral) challenged by `uptrend`.
- Current mark via `position_auditor._spread_mark` (reused, real marks);
  `unrealized = (credit - mark) * contracts * 100`.
- **Challenged** = adverse AND `unrealized < 0` (the market is confirming the
  adverse trend against this position). **[A]** Requiring an actual loss — not
  a trend flip alone — is deliberate: a credit spread comfortably OTM through a
  trend wiggle is not an emergency, and acting on every flip is the noise-
  trading the earlier review warned against.
- `loss_fraction = -unrealized / max_loss` where
  `max_loss = (width - credit) * contracts * 100`.

Recommendation (**[A]** thresholds):
- `loss_fraction >= 0.5`: **CLOSE NOW** — "adverse {trend}, position at
  {loss_fraction:.0%} of max loss ({dte}d left); exit rather than wait for the
  2× stop (~${stop_level})."
- `0 < loss_fraction < 0.5`: **CONSIDER CLOSING / ROLL** — "adverse {trend},
  losing ${abs(unrealized):.0f} ({dte}d left, hard stop ~${stop_level}); close
  early or roll the tested side if the trend persists."

Return per challenged position: `{trade_id, ticker, direction, trend,
unrealized, loss_fraction, dte_left, stop_level, action, message}`.

**[A] Advisory only in v1** — no automatic close (defined-risk positions +
avoiding surprise actions before real money). `TABFM_TREND_GUARD=off` disables.
Auto-tighten-stop for challenged positions is deferred to backlog.

### Wiring

- `run_nightly.run`: after `audit_positions` (on positions that survived the
  audit — reload `get_open_trades(db, strategy="model")`... **[A]** guard the
  MODEL book only; the baseline is deliberately dumb and unmanaged), call
  `assess_trend_risk`, print a `[TrendGuard]` section, and prepend a
  `## <date> — TREND ALERT` block to `RECOMMENDATIONS.md` when any position is
  challenged. Runs on gated nights too (management is independent of entry).
- `run_audit_only`: same advisory after the audit (midday early warning).
- Skipped silently when `open_trades` is empty or `TABFM_TREND_GUARD=off`.

Honest limit documented: in backtests the synthetic marks (even with skew)
under-price panic close costs, so the advisory is exercised for *logic* there;
its real value is live, on real marks.

---

## Config summary

| Env var | Default | Meaning |
|---|---|---|
| `TABFM_BACKTEST_SKEW` | 2.5 | synthetic IV skew slope (per unit moneyness) |
| `TABFM_TREND_GUARD` | on | enable the trend-reversal advisory |

## Testing

- Skew: per-strike IV ordering (put > ATM > call); credit richer than flat;
  30-delta $5 put spread still passes the gauntlet; env override changes slope.
- Trend guard: put spread + downtrend + losing → challenged (CLOSE at ≥50%,
  CONSIDER below); put spread + uptrend → not challenged; put spread +
  downtrend but winning (mark < credit) → not challenged; call spread mirror;
  `TABFM_TREND_GUARD=off` → empty; message contains the numbers.
- Integration: `run_audit_only` / `run` emit the `[TrendGuard]` advisory and
  write the RECOMMENDATIONS.md alert when a seeded challenged position exists;
  no advisory when none challenged.
- Full suite (169) stays green.

## Out of scope

Dynamic (vol-level-dependent) skew steepening; auto-close/auto-tighten on
trend flip (advisory only in v1); per-ticker skew calibration; trend guard on
the baseline book.
