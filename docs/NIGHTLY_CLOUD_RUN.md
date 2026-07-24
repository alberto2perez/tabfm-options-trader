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
     `get_option_chains`. Fetch `get_option_instruments`
     (chain_symbol, expiration_dates, type) — jump to the near-the-money band
     with cursor = base64("p=<strike>.0000"); keep strikes 0.90–1.10 × spot at
     roughly $5 spacing (large underlyings) or $3 (IWM-sized). Then
     `get_option_quotes` for those instrument ids (batches ≤40). Skip rows
     with zero bid or null greeks.

3. Build the snapshot JSON (schema documented in
   `tabfm/trading/adapters/snapshot.py`) and save to
   `data/snapshots/<YYYY-MM-DD>.json`. Underlying indicators (sma20/50, atr14,
   hv20, volume_zscore, momentum, RSI, MACD) are computed from the historicals
   with the helpers in `tabfm/trading/adapters/historical.py` (`_rsi`, `_macd`),
   appending today's live price as the final bar.

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

6. Final message MUST include, in this order:
   - the recommendation block (or the no-trade reason),
   - one line on positions closed by the auditor tonight, if any,
   - the PORTFOLIO SUMMARY block the pipeline prints at the end of every run
     (open contracts, closed count and win rate, realized P&L, total $ at
     risk, open max profit). Never omit the summary.
