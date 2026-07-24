# Nightly Cloud Run — Agent Instructions

Self-contained instructions for the scheduled cloud agent (claude.ai routine).
The agent starts with a fresh clone of this repo and must push state back.

## Hard rules

- PAPER TRADING ONLY. Use only read-only Robinhood MCP tools (get_equity_quotes,
  get_equity_historicals, get_option_chains, get_option_instruments,
  get_option_quotes, get_indexes, get_index_quotes). NEVER call place_equity_order,
  place_option_order, review_*_order, cancel_*_order, or any watchlist mutation.
- All state lives in `data/` and MUST be committed and pushed at the end.
- If it is a weekend or US market holiday, or the snapshot fetch fails, commit
  nothing, report why, and stop.

## Steps

1. `pip install pandas numpy scipy scikit-learn pyarrow yfinance torch` (skip
   any already present). Do NOT install robin_stocks — not needed.

2. Fetch market data via the Robinhood MCP for tickers SPY, QQQ, IWM
   (expand later per data/WATCHLIST_OVERRIDE if present):
   - `get_equity_quotes` for spot prices; `get_indexes symbols=VIX` +
     `get_index_quotes` for VIX.
   - `get_equity_historicals` (interval=day, start ~5 months back) for each
     ticker — used for indicators.
   - For each ticker, pick the monthly expiry nearest 30 DTE from
     `get_option_chains`. The expiry must be at least 28 DTE (entry floor =
     TABFM_MANAGE_DTE + 7); when the front monthly is closer than that, use
     the next monthly out. Fetch `get_option_instruments`
     (chain_symbol, expiration_dates, type) — jump to the near-the-money band
     with cursor = base64("p=<strike>.0000"); keep strikes 0.90–1.10 × spot at
     roughly $5 spacing (large underlyings) or $3 (IWM-sized). Then
     `get_option_quotes` for those instrument ids (batches ≤40). Skip rows
     with zero bid or null greeks. Chain rows must also carry
     `"pop_market": <float chance_of_profit_short>` from the SHORT-capable
     quote fields (`quote.chance_of_profit_short`); omit the key when the
     field is null.
   - RECOVERY-MODE SPACING: before fetching, run
     `python3 -c "import sys; sys.path.insert(0,'.'); from tabfm.trading.pipeline.bankroll import get_bankroll; bk=get_bankroll(); print(bk.recovery_mode, bk.slice_limit)"`.
     If recovery_mode is True (or slice_limit < 200), fetch $1-spaced strikes
     across the short-delta band (0.92–1.00 × spot for puts, 1.00–1.08 for
     calls) instead of $5 spacing — the pipeline builds spreads from adjacent
     strikes, so $1-spaced chains yield $1–2-wide spreads whose per-contract
     risk fits the halved slice. Do NOT relax any quality filter: recovery
     nights may legitimately end with no qualifying trade.
   - `get_earnings_calendar` (read-only) for the next 7 days; keep entries
     whose symbol is in MEGA_CAPS (AAPL MSFT NVDA GOOGL AMZN META TSLA AVGO)
     and write them into the snapshot as
     `events: {"earnings": [{"symbol", "date" (YYYY-MM-DD), "when" (bmo|amc|unknown)}]}`.
     Also copy the last ~10 rows of `data/market_history.csv` into the
     snapshot as `vix_history: [[date, vix], ...]` plus today's VIX reading.
     If the earnings fetch fails, omit `events` entirely — the pipeline
     degrades gracefully and reports it.

3. Build the snapshot JSON (schema documented in
   `tabfm/trading/adapters/snapshot.py`) and save to
   `data/snapshots/<YYYY-MM-DD>.json`. Underlying indicators (sma20/50, atr14,
   hv20, volume_zscore, momentum, RSI, MACD) are computed from the historicals
   with the helpers in `tabfm/trading/adapters/historical.py` (`_rsi`, `_macd`),
   appending today's live price as the final bar.
   - The snapshot must include `vix_series` — ~252 trailing daily `^VIX` closes
     on/before the run date, fetched from yfinance
     (`yfinance.download("^VIX", ...)`), NOT from a VIXY proxy. It feeds the
     IV-rank entry gate; without it iv_rank falls back to a neutral 50.

4. Run the pipeline:
   ```python
   import sys; sys.path.insert(0, ".")
   from datetime import date
   import tabfm.trading.watchlist as wl
   import tabfm.trading.pipeline.chain_fetcher as cf
   from tabfm.trading.watchlist import Ticker
   live = [Ticker(s, "index_etf") for s in ["SPY", "QQQ", "IWM"]]
   wl.WATCHLIST = live; cf.WATCHLIST = live
   from tabfm.trading.adapters.snapshot import SnapshotAdapter
   from tabfm.trading.run_nightly import run
   run(SnapshotAdapter("data/snapshots/<today>.json"), as_of=date.today())
   ```
   This audits open positions, labels expired rows, scores candidates (TabFM
   downloads from Hugging Face on first use — CPU is fine; cold-start skips
   TabFM entirely), applies the Platt calibrator, logs the paper trade to
   `data/journal.db`, and prepends the recommendation to
   `data/RECOMMENDATIONS.md`.

5. Commit and push:
   ```
   git add data/
   git commit -m "nightly: <date> — <ticker> <direction> <short>/<long> exp <expiry> ($<credit> credit)"
   git push
   ```
   If no qualifying trade: commit the snapshot + any labeling updates with
   message "nightly: <date> — no qualifying trade".

5b. Event gate: when the run prints `[EventGate] NO NEW ENTRIES — ...`, that
   is a correct outcome, not an error. Commit the snapshot/labels as usual
   with message "nightly: <date> — GATED (<first reason>)".

5c. Quarterly (first run of Jan/Apr/Jul/Oct): verify `data/macro_calendar.json`
   against the published FOMC meeting schedule, BLS CPI release schedule, and
   first-Friday jobs report dates for the next two quarters; correct any
   drifted dates in the same commit.

## Midday audit (~12:30pm ET, trading days only)

A lighter pass that manages OPEN positions without entering new trades —
value is catching a stop-loss breach hours before the close on a fast day.

1. Query open positions: `get_open_trades(db, strategy=None)` — collect their
   distinct tickers. If none, stop (nothing to audit).
2. Fetch CURRENT option marks + underlying for those tickers only (no
   events/vix_history needed). Build a light snapshot with `tickers`
   (underlying + chain) and `closes`.
3. Run: `python -m tabfm.trading.run_audit --snapshot data/snapshots/<date>-midday.json`
4. Commit `data/` only if a position closed:
   `git commit -m "midday-audit: <date> — closed N"`. Report the summary.

6. Final message MUST include, in this order:
   - the recommendation block (or the no-trade reason),
   - one line on positions closed by the auditor tonight, if any,
   - the PORTFOLIO SUMMARY block the pipeline prints at the end of every run
     (open contracts, closed count and win rate, realized P&L, total $ at
     risk, open max profit). Never omit the summary.
