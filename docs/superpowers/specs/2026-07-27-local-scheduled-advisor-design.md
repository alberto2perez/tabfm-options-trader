# Local Scheduled Advisor — Design

**Date:** 2026-07-27
**Status:** Approved (pending spec review)

## Problem & context

The advisor should run hands-off on a recurring weekday schedule without an
interactive Claude session. Cloud routines (claude.ai) were attempted and
**abandoned**: the cloud sandbox blocks raw egress to HuggingFace and Yahoo, and
is ephemeral with no persistent model cache — so TabFM (a 12 GB HuggingFace
model) cannot practically run there, collapsing recommendations to neutral
fallbacks. Locally, TabFM is already cached (~/.cache/huggingface) and yfinance
works, so the full pipeline runs correctly.

Key enabling fact: **the advisor has a pure-Python entrypoint** that needs no
Claude session or MCP. `python -m tabfm.trading.run_nightly` (and
`run_audit`) auto-create a `LiveAdapter` that fetches quotes/chains/historicals
directly from Robinhood via `robin_stocks`, authenticating from a cached login
or `RH_USER`/`RH_PASS`. The MCP-fetch dance in `docs/NIGHTLY_CLOUD_RUN.md` was
only needed because the cloud couldn't do a native Robinhood login.

## Goals

- Run the **entry** pipeline weekdays ~10:00 local (America/New_York) and the
  **midday audit** ~12:00 local, hands-off, on the user's Mac.
- Each run is driven by a **headless Claude Code session** that orchestrates,
  interprets output, notifies, and commits — but the trade math stays on the
  tested Python entrypoints (chosen approach #1: "Claude orchestrates, Python
  computes").
- Notify via a **macOS banner** each run; full detail lands in
  `data/RECOMMENDATIONS.md`.
- **Commit + push `data/`** to GitHub each run for an off-device audit trail.
- Work with the **lid closed** when the Mac is on AC power (optional wake layer).

## Non-goals

- No true "laptop off" operation — that was the cloud goal and is infeasible for
  a 12 GB local model. The Mac must be powered on (awake, or asleep-on-AC with
  the wake layer) at run times.
- No order placement. The system remains an advisor (paper trading). Runs never
  modify source code — only `data/`.
- No email/Slack notifications in this iteration (macOS banner only).

## Approach (selected)

**Claude orchestrates, tested Python runs the pipeline.** The scheduled Claude
session runs the proven entrypoint, reads its output, and handles
reporting/notification/commit + intelligent error handling. It does NOT
re-improvise the fetch/snapshot/scoring steps. No Robinhood MCP is attached to
the headless session — Python does the fetch.

## Architecture

```
launchd LaunchAgent (per mode, weekday HH:MM local)
   └─> scripts/run_advisor.sh {entry|audit}
          ├─ cd repo, activate venv, load .env (RH creds)
          ├─ caffeinate -i  (hold awake for the run)
          ├─ claude -p "<mode prompt>"  --model sonnet
          │     --allowedTools "Bash Read Write Edit Glob Grep"
          │        └─ python -m tabfm.trading.run_nightly | run_audit
          │        └─ interpret output
          │        └─ osascript display notification (headline)
          │        └─ git add data/ && commit && push
          ├─ tee -> data/run-logs/<date>-<mode>.log
          └─ on non-zero claude exit: fallback failure banner
   (optional) pmset repeat wakeorpoweron MTWRF 09:58 / 11:58
```

### Components

**1. LaunchAgents (2 plists in `~/Library/LaunchAgents/`)**
- `com.tabfm.advisor.entry.plist` — `StartCalendarInterval` array of 5 dicts
  (Weekday 1–5) at Hour 10, Minute 0; runs `run_advisor.sh entry`.
- `com.tabfm.advisor.audit.plist` — same, Hour 12; runs `run_advisor.sh audit`.
- `launchd` uses local time → DST is automatic. LaunchAgents run in the
  logged-in GUI session, so `osascript` notifications work. Missed fires (Mac
  briefly asleep, lid open) run on wake. `StandardOutPath`/`StandardErrorPath`
  point at a bootstrap log.

**2. Wrapper — `scripts/run_advisor.sh {entry|audit}`**
- Resolves repo dir, `source venv/bin/activate`, loads `.env`.
- Wraps the Claude invocation in `caffeinate -i` so idle-sleep can't interrupt a
  run in progress.
- Invokes `claude -p` with the mode-specific prompt, `--model claude-sonnet-4-6`,
  and a tight `--allowedTools` list (no MCP). Uses a non-interactive permission
  posture (allowlist + bypass) suitable for unattended runs.
- `tee`s combined output to `data/run-logs/<YYYY-MM-DD>-<mode>.log`.
- If `claude` exits non-zero, fires a fallback banner ("❌ advisor <mode> failed
  to launch") so silence never masks a crash.

**3. Claude prompts (per mode, self-contained)**
- entry: run `python -m tabfm.trading.run_nightly`; audit: run
  `python -m tabfm.trading.run_audit`.
- On data/auth failure (e.g. Robinhood token expired): send a failure banner
  naming the reason, commit nothing, stop.
- On success: send a macOS banner with a one-line headline (e.g.
  "Advisor ✓ SPY PUT 545/540 $0.62" or "Advisor — GATED (FOMC)"); confirm the
  full report is in `data/RECOMMENDATIONS.md`; `git add data/`, commit with the
  runbook's message convention, and `git push`.
- Guardrail: only ever touch `data/`; never modify source.

**4. Notifications**
- macOS banner via `osascript -e 'display notification "…" with title "…"'`.
- Lid-closed/locked: banner queues in Notification Center and appears on next
  unlock. Full detail always in `data/RECOMMENDATIONS.md`.

**5. Git**
- Each successful run: `git add data/ && git commit -m "<convention>" && git push`.
- entry commit messages follow `docs/NIGHTLY_CLOUD_RUN.md` conventions
  (`nightly: <date> — …`, `… — no qualifying trade`, `… — GATED (<reason>)`);
  audit uses `midday-audit: <date> — closed N` and commits only if a position
  closed.

**6. Optional wake layer (lid-closed on AC power)**
- `pmset repeat wakeorpoweron MTWRF 09:58:00` and a second entry at 11:58:00 to
  wake the Mac ~2 min before each run.
- Reliable only on AC power; macOS suppresses scheduled wakes on battery. On
  battery with lid closed, runs may be skipped until next wake. Documented as a
  requirement, not a guarantee.
- The plain `launchd` jobs work lid-open with none of this; the wake layer is
  additive.

## Data flow

1. Scheduler fires → wrapper → headless Claude session.
2. Claude runs the Python entrypoint → `LiveAdapter`/`robin_stocks` fetch live
   Robinhood data (10am/12pm = live option marks), TabFM scores from local
   cache, pipeline audits positions, logs paper trade to `data/journal.db`,
   prepends to `data/RECOMMENDATIONS.md`, prints portfolio summary.
3. Claude parses stdout → banner headline + git commit/push of `data/`.
4. Wrapper logs everything; fallback banner on launch failure.

## Error handling

- **Robinhood auth expired** (cached token lapsed / MFA needed): Python errors →
  Claude sends "❌ auth expired — re-login needed" banner, commits nothing. User
  re-runs an interactive `rh.login()` to refresh the cached session.
- **Weekend / holiday / fetch failure:** entrypoint self-aborts; Claude reports
  and commits nothing (matches existing pipeline behavior).
- **Claude session fails to launch:** wrapper's non-zero-exit fallback banner.
- **Push failure** (offline/creds): Claude reports it in the log/banner; local
  `data/` state is still updated (commit succeeds locally, push retries next
  run).

## Setup prerequisites (one-time)

- `claude` CLI installed and authenticated.
- `venv` present with pipeline deps; TabFM cached (already true).
- Robinhood cached login established once via interactive `rh.login()`, or
  `RH_USER`/`RH_PASS` in `.env`.
- LaunchAgents loaded (`launchctl bootstrap`), wrapper marked executable.
- (Optional) `pmset repeat` wake schedule installed for lid-closed use.

## Testing

- **Wrapper dry run:** invoke `run_advisor.sh entry` manually mid-session,
  confirm log written, banner shown, `data/` committed.
- **Failure path:** temporarily break auth (bad `.env`) → confirm failure banner
  and no commit.
- **Schedule:** load the LaunchAgent, use a near-future one-off time, confirm it
  fires from `launchd` (not just manual).
- **Idempotency/weekend:** run on a non-trading day → confirm graceful "no
  trade" and no spurious commit.
- **Wake layer (if used):** set a scheduled `pmset` wake, close lid on AC,
  confirm the run happens and the banner queues.

## Risks / open caveats

- Robinhood token expiry is the main recurring fragility; mitigated by the
  failure banner + one-command re-login.
- Lid-closed-on-battery wakes are unreliable (Apple behavior) — documented.
- Headless Claude permission posture must be scoped tightly (data/ + specific
  Bash commands) to keep unattended runs safe.
</content>
