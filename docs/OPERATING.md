# Operating Runbook

**The system is an ADVISOR, not an auto-trader.** There is no order-placement
code anywhere. Each run produces a recommendation and manages open positions;
YOU place/close the actual orders (on paper now, in Robinhood when real).

## Mental model

- **Nightly run** (after the 4pm ET close): audit open positions (stop /
  profit-target / 21-DTE / expiry), evaluate the event gate, and — if a
  positive-EV trade clears the gauntlet and fits the bankroll — recommend
  ONE new credit spread. Prints the recommendation + bankroll + portfolio +
  any TREND ALERT, and appends them to `data/RECOMMENDATIONS.md`.
- **Midday run** (~12:30pm ET, optional): audit-only. Catches a stop breach
  hours before the close on a fast day. Never enters new trades.
- **You**: read the output, place any new trade, close anything the auditor
  or trend guard flagged.

## Current automation state (be honest)

Not yet scheduled. A run happens inside a Claude session: the assistant
fetches the market snapshot via the read-only Robinhood MCP, runs the
pipeline, and reports. To make it hands-off, do the one-time Cloud Setup
below. Until then: start a session and say "run tonight's nightly."

## Daily cycle

1. **Evening (after close):** run the nightly. Read three things:
   - **BANKROLL line** — equity, drawdown %, mode (NORMAL / RECOVERY /
     HALTED). If **HALTED**, place no new trades.
   - **Any position closures** the auditor made (stop / target / DTE /
     expiry) — in real mode, place those closing orders.
   - **TREND ALERT** (if any) — CLOSE NOW / CONSIDER CLOSING advisories on
     open positions the trend turned against.
   - **The RECOMMENDATION** (if any) — tonight's new trade.
2. **Place the trade** (real mode) — see "Reading a recommendation" below.
3. **Midday next day (optional):** run the midday audit; act on any stop.

## Reading a recommendation → a Robinhood order

The card gives: ticker, direction, short/long strikes, expiry, credit,
contracts. Translate:
- **PUT CREDIT SPREAD** (bullish/neutral): SELL the short (higher) put, BUY
  the long (lower) put, same expiry → as a single spread order, LIMIT at the
  recommended credit (or better). Defined risk = width − credit per contract.
- **CALL CREDIT SPREAD** (bearish/neutral): SELL the short (lower) call, BUY
  the long (higher) call, same expiry, LIMIT at the credit.
- Use the recommended contract count (bankroll-sized; 1 at $2k). Never chase
  a worse-than-recommended fill — if you can't get near the credit, skip it
  (that IS the fill-fidelity check).

## Phase 1 — Paper (start here, ~2 weeks)

The book is already fresh: $2,000, zero trades, default config. Just start.
TabFM scores from night one — the history store is pre-seeded with 582 real
labeled outcomes, so POP% / expected-return are real from the first run (no
weeks-long cold-start). Your journal and the P&L calibrator still start
empty and learn only from your actual closed trades.

**What 2 weeks of paper actually validates** (be clear): NOT win-rate/P&L —
trades are 28–45 DTE so almost none close in 2 weeks, and the P&L evidence
already came from the real-marks replay (+72% return, 24% max drawdown). The
2 weeks validate:
- **Execution/fill fidelity** — when a trade is recommended, check the real
  Robinhood bid/ask and confirm you could get ~the modeled credit. Log gaps.
- **Operational reliability** — the run works daily, produces sane trades.
- **Safety systems on live events** — an FOMC or mega-cap-earnings cluster
  should show the event gate standing down (`GATED — no new entries`).

## Phase 2 — Real money (small)

Go real at the size the bankroll already uses: $2k, 1 contract, max ~$335
risk/trade, ~$500 portfolio (25% bucket cap). Treat month 1 as the final
validation with real skin.

- Each evening's recommendation → place that exact spread as a limit order.
- Each closure/alert → place the closing order.
- Record the ACTUAL fills (they become the journal that drives bankroll,
  sizing, and calibration).

**Tripwires — pause and reassess if:**
- Live fills are consistently worse than modeled (edge is thinner than the
  replay assumed).
- Realized drawdown exceeds the replay's ~24% (regime worse than tested).
- The bankroll shows HALTED (the 35% drawdown circuit breaker tripped).

## Config dials (env vars, all have safe defaults)

| Var | Default | Meaning |
|---|---|---|
| `TABFM_STARTING_CAPITAL` | 2000 | bankroll base |
| `TABFM_RISK_PER_TRADE` | 0.18 | per-trade slice |
| `TABFM_MAX_EXPOSURE` | 0.45 | total open-risk cap |
| `TABFM_MAX_BUCKET_RISK` | 0.25 | per correlated-bucket cap (the drawdown lever) |
| `TABFM_DRAWDOWN_BRAKE` | 0.25 | halve slice beyond this drawdown |
| `TABFM_DRAWDOWN_HALT` | 0.35 | STOP new trades beyond this drawdown |
| `TABFM_STOP_LOSS_MULT` | 2.0 | close at mark ≥ this × credit |
| `TABFM_MANAGE_DTE` | 21 | close open positions at/under this DTE |
| `TABFM_TREND_GUARD` | on | trend-reversal advisory |
| `TABFM_EVENT_GATE` | on | earnings/macro/VIX gate |
| `TABFM_MIN_IV_RANK` | 30.0 | min IV rank (percentile) to sell |
| `TABFM_MIN_CREDIT_ABS` | 0.25 | min absolute credit ($) to bother |
| `TABFM_EV_LOSS_MULT` | 2.0 | EV-gate loss multiple (admits short_delta < 1/(1+this)) |

Loosening `TABFM_MAX_BUCKET_RISK` to 0.30–0.35 recovers return at higher
drawdown; keep it at 0.25 for first real money.

## Hands-off scheduling — local (macOS)

Cloud routines were evaluated and dropped: the cloud sandbox blocks HuggingFace
egress and is ephemeral, so the 12 GB TabFM model can't run there. Instead the
advisor runs **locally on a schedule** — entry 10:00 / audit 12:00 weekdays —
via `launchd` + a headless Claude session. See `docs/LOCAL_SCHEDULING.md` for
setup. Until installed, runs are session-assisted ("run tonight's nightly").

To watch a scheduled run, use `scripts/advisor_watch.sh` (process/status + live
log tail) or `claude --resume` (the saved session transcript). If you want to
watch the pipeline itself stream live, just run `python -m tabfm.trading.run_nightly`
in a terminal — that bypasses the headless session and prints everything in real
time.
