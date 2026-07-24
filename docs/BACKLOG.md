# Backlog

User-approved improvements queued for implementation ("I want them all"),
from the 2026-07-24 trader-analyst review. Ordered roughly by value.

## Risk

1. **Correlation-aware exposure** — SPY/QQQ/IWM are ~0.9 correlated; bucket
   correlated underlyings as one for the 45% exposure cap, cap same-direction
   slices (e.g., max 2 of 3 bullish), and report net beta-weighted delta of
   the book in the portfolio summary.
2. **Slippage + commissions in paper fills** — fill at mid − ~30% of the
   bid/ask spread, minus ~$0.65/contract/leg commissions, configurable.
   Material for $1-wide recovery spreads (5–10% of edge). The journal drives
   the bankroll, so realized P&L should compound on realistic numbers.
3. **Assignment & pin risk** (pre-real-trading gate) — model early assignment
   on short ITM legs near ex-dividend and pin risk at expiry before any
   real-order pathway is built.

## Edge / profit

4. **VIX term structure** (VIX vs VIX3M contango/backwardation) as an event
   gate input and stored feature — strong premium-selling regime signal.
5. **Kelly/half-Kelly sizing** from calibrated POP (spec'd as bankroll v2;
   gate on ≥50 closed trades and stable calibration).
6. **Roll management** — roll untested side / roll out in time for credit
   when a position is challenged, instead of binary close.

## Accuracy

7. **Calibrate exp_return** — journal-based regression of realized vs
   predicted return; EV currently multiplies calibrated POP by an
   uncalibrated return estimate.
8. **Naive baseline arm** — paper-log "always sell 30-delta SPY put spread,
   ~30 DTE" in parallel; report model-vs-baseline P&L and hit rate in the
   accuracy tracker. If the ML stack doesn't beat it, the intelligence isn't
   paying for itself.

## Hygiene

9. **LiveAdapter parity** — real VIX (not VIXY×10) and `get_events` for the
   direct `run_nightly` live path, so it matches the snapshot path.
10. **Market holiday calendar** for the event gate's next-session logic
    (currently weekday approximation).
11. Deferred review minors: duplicate gated-day entries in RECOMMENDATIONS.md
    on same-night re-runs; `market_history.csv` write not atomic.
