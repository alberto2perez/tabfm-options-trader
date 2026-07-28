# Latest Run Results skill — design

**Date:** 2026-07-28
**Status:** Approved design, ready for implementation plan

## Problem

There is no clean readback of what the *last completed* advisor run produced.
`scripts/advisor_watch.sh` watches a run *while it is live* (process status +
log tail), but after a scheduled entry/audit run finishes there is no single
command that answers: *did the last run succeed, what did it recommend, where
does my book stand now, and does that match my real Robinhood account?*

Today the operator reconstructs this by hand — tailing the newest file in
`data/run-logs/`, scrolling `data/RECOMMENDATIONS.md`, and mentally comparing
against the brokerage. This skill collapses that into one invocation.

## Goal

A read-only **full current briefing**, on demand, that combines:

- the outcome of the most recent run,
- the latest recommendation card,
- the advisor's tracked book (bankroll + open positions), and
- a reconciliation against **live read-only Robinhood** state,

ending with a **pending-actions** section. It never places orders and never
mutates the journal — advisory only.

## Non-goals

- No order placement or journal mutation (read-only advisory).
- No auto-fix of drift — drift is reported and flagged, not corrected.
- No new machine-readable report schema / production module (a thin shell
  helper reusing existing functions is enough).

## Approach

Approach A — an **orchestration skill** that runs inside a Claude session.
Chosen because the two hard requirements — live Robinhood MCP access and
intelligent drift reconciliation — both require a live session and LLM
reasoning, which a plain script cannot provide. A pure shell script (rejected
option C) cannot reach the MCP; a dedicated reporter CLI (rejected option B)
adds production surface and a schema for no benefit here.

### Components

1. **`scripts/latest_results.sh`** — deterministic local-only gather helper.
   Prints a plain-text briefing of everything knowable without the brokerage:
   - **Last run:** newest `data/run-logs/*.log` → mode, timestamp, and outcome
     parsed from the tail. Classify as ✅ success (`run finished — exit 0`),
     ❌ failed (nonzero exit), ⏱ timeout (`TIMEOUT` / exit 143/137),
     🧪 selftest (`[selftest]` marker), or ⚠️ no real completed run found.
   - **Latest recommendation:** the last `## <date>` card block from
     `data/RECOMMENDATIONS.md`; `none on record` if missing/malformed.
   - **Current book:** a `python -c` calling `get_bankroll(db_path)` from
     `tabfm.trading.pipeline.bankroll` and `portfolio_summary(db_path, as_of)`
     from `tabfm.trading.pipeline.portfolio` — equity, drawdown, mode
     (NORMAL / RECOVERY / HALTED), open positions, exposure. The 0-row
     empty-book case prints `flat — no open positions`.

   The helper reuses existing functions (no book-math duplication) and is
   usable standalone outside a session as an offline fallback. It follows the
   `scripts/` conventions of `advisor_watch.sh` / `run_advisor.sh` (activate
   venv, resolve repo root from `$0`).

2. **`.claude/skills/latest-run-results/SKILL.md`** — the orchestration prose.
   Invoked as `/latest-run-results`. `allowed-tools` scoped to `Bash`, `Read`,
   and the read-only Robinhood MCP tools already allow-listed in
   `.claude/settings.local.json` (`get_portfolio`, `get_equity_positions`,
   `get_option_positions`, `get_accounts`, quotes).

### Flow when invoked

1. **Gather local** — run `scripts/latest_results.sh` once.
2. **Gather live** — read-only Robinhood MCP calls for current portfolio and
   option/equity positions.
3. **Reconcile** — compare the advisor's tracked open trades against live
   positions; identify drift in both directions (recommended-but-not-placed;
   live-but-untracked).
4. **Print one briefing** — formatted card (below), ending with pending actions.

### Output format

```
═══════════════════════════════════════════════
  LATEST RUN RESULTS  ·  <now>
═══════════════════════════════════════════════
  Last run     <mode> · <status> · exit <N>   (<ago>)
               <path to run log>
  ─────────────────────────────────────────────
  Recommendation (<date>)
    <ticker direction strikes> · <expiry> (<DTE>)
    credit $<c> · <n> contract(s) · POP <p>% · [<state>]
    <TREND ALERT / closures from this run, if any>
  ─────────────────────────────────────────────
  BANKROLL     equity $<e> · drawdown <d>% · <MODE>
  BOOK         <k> open · exposure $<x> (<pct> of cap)
  ─────────────────────────────────────────────
  RECONCILE (advisor book ↔ live Robinhood)
    ✅ <trade> — matched
    ⚠️ <position> — live position, NOT in advisor book
  ─────────────────────────────────────────────
  PENDING ACTIONS
    • Place: <recommended trades not yet in live account, or none>
    • Review: <untracked live positions, or none>
═══════════════════════════════════════════════
```

Formatting rules:
- Sections with nothing to report collapse to a one-line `none`.
- Alerts/closures from the last run (TREND ALERT, stop / profit-target /
  21-DTE / expiry closures) surface under the Recommendation block when present.
- `HALTED` bankroll is surfaced prominently on the BANKROLL line (circuit
  breaker).

### Edge cases

- **No completed run yet** (only selftest/bogus logs — current state) →
  `⚠️ no real run found — latest is a selftest`; briefing still prints book +
  live state.
- **Empty book** (0 `paper_trades` rows — current state) → BOOK shows
  `flat — no open positions`; RECONCILE lists only untracked live positions.
- **RH MCP unavailable** (offline / after-hours / auth failure) → live and
  RECONCILE sections degrade to `— live data unavailable (offline/after-hours)`;
  the rest prints from local and the skill exits cleanly. Never blocks on MCP.
- **Malformed / missing `RECOMMENDATIONS.md`** → `none on record`.

## Testing

- `scripts/latest_results.sh` gets a shell-level smoke test (mirrors the
  `ADVISOR_SELFTEST` pattern): run against the repo, assert exit 0 and that the
  three section headers are emitted. The current fixtures (selftest-only logs,
  empty book) already exercise those degraded paths.
- The reconciliation/formatting layer is LLM prose in `SKILL.md`; validated by
  invoking `/latest-run-results` once and eyeballing the briefing. No unit test
  for the LLM layer.

Deliberately light: the helper is the only testable code and it reuses existing
functions.

## Files

- `scripts/latest_results.sh` (new)
- `.claude/skills/latest-run-results/SKILL.md` (new)
- smoke test for the helper (location per existing test conventions)
