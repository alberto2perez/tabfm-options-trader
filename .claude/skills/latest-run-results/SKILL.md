---
name: latest-run-results
description: Full current briefing on the advisor — last run outcome, latest recommendation, current book, and a live read-only Robinhood reconciliation with pending actions. Use when the user asks for the latest run results, run status, or where the book stands.
allowed-tools: Bash(bash scripts/latest_results.sh), Read, mcp__robinhood-trading__get_accounts, mcp__robinhood-trading__get_portfolio, mcp__robinhood-trading__get_equity_positions, mcp__robinhood-trading__get_option_positions
---

Produce ONE read-only briefing on the latest advisor run and current state.
This is advisory only — NEVER place orders and NEVER mutate `data/journal.db`
or `data/RECOMMENDATIONS.md`.

## Steps

1. **Gather local (deterministic).** Run `bash scripts/latest_results.sh`. It
   prints four sections — `== LAST RUN ==` (mode/timestamp/outcome of the
   newest `data/run-logs/*.log`), `== LATEST RECOMMENDATION ==` (the newest
   `RECOMMENDATIONS.md` card), `== CURRENT BOOK ==` (bankroll, drawdown, mode,
   open positions, exposure via `portfolio_summary`), and `== ACCURACY ==`
   (closed-trade scorecard via `accuracy_tracker.report` — win rate, POP
   calibration error, Brier vs market, cumulative P&L, max drawdown,
   model-vs-baseline; prints "No closed trades yet." on a fresh book). Treat
   this as the source of truth for the advisor's tracked state.

2. **Gather live (Robinhood, read-only).** First call `get_accounts` to get the
   `account_number` (use the default individual account — the one with an option
   level, where credit spreads live); the other calls require it. Then call
   `get_option_positions` (nonzero=true) and `get_portfolio` for that account
   (`get_equity_positions` if relevant). If ANY MCP call fails or times out
   (offline / after-hours / auth), do NOT retry endlessly and do NOT block —
   mark live data unavailable and continue with local-only sections.

3. **Reconcile.** Compare the advisor's open trades (from the book section)
   against live positions, in both directions:
   - a recommended/tracked trade with no matching live position → **not placed**;
   - a live position not in the advisor book → **untracked**.
   A credit spread matches when ticker, both strikes, and expiry line up
   (live shows a short leg and a long leg).

4. **Print the briefing** in this exact card shape:

    ═══════════════════════════════════════════════
      LATEST RUN RESULTS  ·  <now>
    ═══════════════════════════════════════════════
      Last run     <mode> · <status>   (<ago>)
                   <path to run log>
      ─────────────────────────────────────────────
      Recommendation (<date>)
        <ticker direction strikes> · <expiry> (<DTE>)
        credit $<c> · <n> contract(s) · POP <p>% · [<state>]
        <TREND ALERT / closures from this run, if any>
      ─────────────────────────────────────────────
      BANKROLL     equity $<e> · drawdown <d>% · <MODE>
      BOOK         <k> open · exposure $<x>
      ACCURACY     <win% · N closed · P&L · vs baseline>, or "no closed trades yet"
      ─────────────────────────────────────────────
      RECONCILE (advisor book ↔ live Robinhood)
        ✅ <trade> — matched
        ⚠️ <position> — live position, NOT in advisor book
      ─────────────────────────────────────────────
      PENDING ACTIONS
        • Place: <recommended trades not in live account, or none>
        • Review: <untracked live positions, or none>
    ═══════════════════════════════════════════════

## Rules

- Any section with nothing to report collapses to a one-line `none`.
- Surface a `HALTED` bankroll prominently on the BANKROLL line (circuit breaker).
- If the book is flat, BOOK shows `flat — no open positions` and RECONCILE
  lists only untracked live positions.
- If live data is unavailable, RECONCILE and the live figures show
  `— live data unavailable (offline/after-hours)`; still print LAST RUN,
  Recommendation, and BOOK from the local helper.
- If the newest run log is a selftest/bogus (no real run), Last run shows
  `⚠️ no real run found — latest is a selftest`.
