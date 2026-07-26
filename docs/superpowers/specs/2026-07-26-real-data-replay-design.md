# Real-Data Replay Backtest — Design Spec

**Date:** 2026-07-26
**Status:** Approved (user chose this single track to make a 2-week
pre-real-money validation sufficient). Autonomous-leaning; assumptions marked **[A]**.

## Problem

Prior backtests price options with synthetic flat/skewed Black-Scholes, so
their P&L and win rate are untrustworthy (the March run showed 98% win /
$106k fantasy). Live paper can't fix this in 2 weeks — 28–45 DTE trades don't
close in the window. We need trustworthy closed-trade statistics NOW.

**Unlock (feasibility proven):** Robinhood MCP exposes
`get_option_instruments(state='expired')` (past contracts) and
`get_option_historicals` (real daily OHLC per contract). A probe pulled the
real daily price path of an expired SPY 660P (2026-05-15 expiry) cleanly.
Option bars carry OHLC only — no greeks/bid-ask/volume — so mids are real and
greeks are back-solved from real prices.

## Solution: replay the identical pipeline on REAL option marks

### Component 1: Data builder (one-time, in-session via MCP)

Produces a committed cache `data/replay/spy_real_chains.parquet` with one row
per (date, strike, expiry, option_type):
`{date, ticker, strike, expiry, option_type, bid, ask, mid, delta, iv, open_interest, dte}`.

Build steps (SPY only in v1 **[A]** — cleanest, plenty of trades; QQQ/IWM are
a documented extension):
1. **Window [A]:** entries 2026-02-15 → 2026-05-01 targeting the three past
   monthly expiries **2026-03-20, 2026-04-17, 2026-05-15** — deliberately spans
   the March VIX-31 selloff so the replay tests a real vol event.
2. For each expiry, `get_option_instruments(chain_symbol='SPY',
   expiration_dates=<exp>, state='expired', type=put|call)` → instrument
   UUIDs + strikes; keep strikes within ~0.82–1.12 × the expiry-period spot
   (covers 30-delta both sides), $5 grid.
3. For those instruments, `get_option_historicals(instrument_ids=[≤10],
   start_time, end_time, interval='day')` over the entry→expiry span → daily
   `close_price` = the real **mid** per date.
4. Per (date, strike, expiry, type): `mid = close_price`;
   **back-solve IV** = sigma such that `_bs_price(S, K, T, sigma, type) ≈ mid`
   (bisection on the existing `_bs_price`; S = real SPY close that date from
   yfinance/Robinhood, T = dte/365); `delta = abs(_bs_delta(S,K,T,iv,type))`.
   **[A] bid/ask model:** `half = max(0.02, mid*0.02)`; `bid = max(mid-half, 0.01)`,
   `ask = mid+half` — the only synthetic piece; real SPY near-money spreads run
   ~$0.02–0.05. **[A] open_interest = 500** constant (historical OI unavailable;
   the ≥100 filter is not the thing under test).
5. Drop rows where mid ≤ 0 or IV back-solve fails (deep OTM near expiry).
   Write the parquet.

The builder is orchestrated via subagents making the MCP calls (bounded ~40–60
calls) and a local reduce step computing greeks; it writes the cache and
returns row counts. Committed so the replay is reproducible.

### Component 2: `ReplayAdapter` (`adapters/replay.py`)

Reads the cache; a `DataAdapter` like the others:
- `get_options_chain(ticker, as_of)`: rows for `date == as_of` whose expiry is
  28–45 DTE from as_of (the live nearest-monthly rule); real bid/ask/mid/delta/iv.
- `get_underlying(ticker, as_of)`: real SPY OHLC-derived indicators (reuse the
  same sma/atr/hv/rsi/macd computation as HistAdapter, from real closes ≤ as_of).
- `get_close`, `get_vix_series`, `get_vix`, `get_vix_history`: from real ^VIX /
  SPY closes (yfinance, filtered ≤ as_of — no lookahead). `get_events` → `{}`
  (no earnings gate in replay, documented). No-lookahead asserted throughout.

### Component 3: Replay runner (`backtest/replay_runner.py`)

`run_replay(cache_path, tickers=["SPY"]) -> dict`: derive the date range from
the cache, load TabFM models (MPS), build one `ReplayAdapter`, iterate
`trading_days` calling the SAME `run(...)` (gate, sizing, scoring, auditor,
both arms, trend guard), then print `report()` + `turning_point_report()`.
Isolated temp journal/store (never touches live `data/`), same as `run_backtest`.

## What this validates (the payoff)

Trustworthy on REAL marks: model win rate, POP + return calibration error,
model-vs-baseline, drawdown, and — critically — **the real losses** when spreads
were tested during the March selloff (real closing costs, not flat-vol
fantasy). This is the statistical evidence a 2-week live window cannot produce.

## Honest limits (documented)

- Greeks (delta/IV) are back-solved from real mids — real-implied, not
  exchange-published, but far better than synthetic.
- Bid/ask spread + OI are modeled (mids are real); entry credit = real mids −
  modeled spread, so execution realism is bounded by the spread model.
- SPY only, one vol regime, v1. Not a guarantee of forward edge — it removes
  the synthetic-pricing lie, it doesn't remove market uncertainty.

## Testing

- Greeks back-solve: `_implied_vol(price, S, K, T, type)` recovers the sigma
  used to generate a known BS price (round-trip within tolerance); monotonic.
- `ReplayAdapter`: reads a small seeded cache parquet → `get_options_chain`
  returns the right date's rows filtered to the 28–45 DTE expiry; no-lookahead
  raises for future dates; `get_underlying` indicators computed from real closes.
- Replay runner: runs end-to-end over a tiny seeded cache without touching
  `data/`; produces a metrics dict with the baseline + turning-point sections.
- Full suite stays green; new tests avoid live network (seeded cache + a stub
  VIX/underlying source, or mark the network-bound builder test excluded like
  `test_hist_adapter`).

## Out of scope (v1)

QQQ/IWM replay; exchange greeks; historical OI/real bid-ask; intraday marks;
the other validation tracks (live execution audit, short-DTE sprint, formal
go/no-go doc) — deferred, the user chose replay only.
