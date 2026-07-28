# Latest Run Results skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an on-demand read-only briefing — a `scripts/latest_results.sh` local gather helper plus a `/latest-run-results` Claude Code skill — that reports the last advisor run's outcome, the latest recommendation, the current book, and a live-Robinhood reconciliation with pending actions.

**Architecture:** A shell helper (`scripts/latest_results.sh`) deterministically prints everything knowable from local files — last-run outcome parsed from `data/run-logs/`, the latest `data/RECOMMENDATIONS.md` card, and the current book via `portfolio_summary()`. A Claude Code skill (`.claude/skills/latest-run-results/SKILL.md`) runs that helper, then layers read-only Robinhood MCP calls and drift reconciliation on top, printing one formatted briefing. Advisory only — never places orders, never mutates the journal.

**Tech Stack:** Bash, Python 3 (existing `tabfm.trading` package via the repo venv), pytest for the shell-helper smoke test, Claude Code skill markdown with `allowed-tools`.

## Global Constraints

- **Read-only advisory:** the skill and helper MUST NOT place orders or mutate `data/journal.db` / `data/RECOMMENDATIONS.md`. Report and flag only.
- **Never block on the brokerage:** if Robinhood MCP is unavailable (offline / after-hours / auth failure), degrade the live + reconcile sections to `— live data unavailable (offline/after-hours)` and still print local sections; exit cleanly.
- **Reuse existing functions:** the current book comes from `tabfm.trading.pipeline.portfolio.portfolio_summary` — do not duplicate bankroll/portfolio math.
- **Follow `scripts/` conventions:** resolve repo root from `$0` (`REPO="$(cd "$(dirname "$0")/.." && pwd)"`), `set -uo pipefail`, activate `venv/bin/activate` before Python, as in `run_advisor.sh` / `advisor_watch.sh`.
- **RH MCP tools are read-only only:** `get_portfolio`, `get_equity_positions`, `get_option_positions`, `get_accounts`, quotes — the set already allow-listed in `.claude/settings.local.json`.

---

## File Structure

- `scripts/latest_results.sh` (new) — local-only briefing helper; three sections: `== LAST RUN ==`, `== LATEST RECOMMENDATION ==`, `== CURRENT BOOK ==`.
- `tabfm/trading/tests/test_latest_results.py` (new) — pytest smoke test that shells out to the helper and asserts exit 0 + the three headers.
- `.claude/skills/latest-run-results/SKILL.md` (new) — orchestration skill.

---

### Task 1: Local gather helper + smoke test

**Files:**
- Create: `scripts/latest_results.sh`
- Test: `tabfm/trading/tests/test_latest_results.py`

**Interfaces:**
- Consumes: `tabfm.trading.pipeline.portfolio.portfolio_summary(db_path=_DEFAULT_DB, as_of: date | None = None) -> str` (already exists; default `db_path` resolves to `data/journal.db`).
- Produces: an executable `scripts/latest_results.sh` that, run with no args from anywhere, exits 0 and prints exactly these three section headers on their own lines: `== LAST RUN ==`, `== LATEST RECOMMENDATION ==`, `== CURRENT BOOK ==`. The skill (Task 2) depends on these exact header strings.

- [ ] **Step 1: Write the failing smoke test**

Create `tabfm/trading/tests/test_latest_results.py`:

```python
import pathlib
import subprocess

REPO = pathlib.Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "latest_results.sh"


def test_latest_results_smoke():
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        cwd=str(REPO),
    )
    assert result.returncode == 0, f"nonzero exit: {result.stderr}"
    for header in ("== LAST RUN ==", "== LATEST RECOMMENDATION ==", "== CURRENT BOOK =="):
        assert header in result.stdout, f"missing {header!r}\n---stdout---\n{result.stdout}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tabfm/trading/tests/test_latest_results.py -v`
Expected: FAIL — the script does not exist yet (nonzero exit / assert on returncode).

- [ ] **Step 3: Write the helper script**

Create `scripts/latest_results.sh`:

```bash
#!/bin/bash
# Local-only "latest run results" briefing. Prints last-run outcome, the latest
# recommendation card, and the current book — everything knowable without the
# brokerage. The /latest-run-results skill runs this, then layers live Robinhood
# reconciliation on top. Usable standalone as an offline fallback.
#
# Usage: latest_results.sh
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO" || { echo "cannot cd to repo"; exit 1; }

LOGDIR="$REPO/data/run-logs"
RECS="$REPO/data/RECOMMENDATIONS.md"

# ---- Last run ---------------------------------------------------------------
echo "== LAST RUN =="
LATEST_LOG="$(ls -t "$LOGDIR"/*.log 2>/dev/null | head -1)"
if [ -z "$LATEST_LOG" ]; then
  echo "  (no run logs yet)"
else
  TAIL="$(tail -20 "$LATEST_LOG")"
  if printf '%s' "$TAIL" | grep -q '\[selftest\]'; then
    STATUS="🧪 selftest — no real run"
  elif printf '%s' "$TAIL" | grep -qE 'TIMEOUT|exit (143|137)'; then
    STATUS="⏱ timeout"
  elif printf '%s' "$TAIL" | grep -qE 'run finished — exit 0'; then
    STATUS="✅ success"
  elif printf '%s' "$TAIL" | grep -qE 'run finished — exit [1-9]'; then
    STATUS="❌ failed"
  else
    STATUS="⚠️ no completion marker (incomplete or in progress)"
  fi
  echo "  $(basename "$LATEST_LOG")"
  echo "  $STATUS"
fi

# ---- Latest recommendation --------------------------------------------------
echo
echo "== LATEST RECOMMENDATION =="
if [ -f "$RECS" ]; then
  # Print the last "## " section block of the file (header to EOF).
  awk '/^## /{buf=""} {buf=buf $0 "\n"} END{printf "%s", buf}' "$RECS" | sed 's/^/  /'
else
  echo "  none on record"
fi

# ---- Current book -----------------------------------------------------------
echo
echo "== CURRENT BOOK =="
# shellcheck disable=SC1091
if source "$REPO/venv/bin/activate" 2>/dev/null; then
  if ! python -c "from datetime import date; from tabfm.trading.pipeline.portfolio import portfolio_summary; print(portfolio_summary(as_of=date.today()))" 2>/dev/null | sed 's/^/  /'; then
    echo "  (book unavailable — could not compute portfolio summary)"
  fi
else
  echo "  (venv unavailable — cannot compute book)"
fi
```

Note on the awk: each `^## ` header resets `buf`, so after EOF `buf` holds the last header through end of file — the newest card. The top-level `# Nightly Recommendations` line (single `#`) does not match `^## ` and is correctly skipped.

- [ ] **Step 4: Make it executable**

Run: `chmod +x scripts/latest_results.sh`

- [ ] **Step 5: Run test to verify it passes**

Run: `venv/bin/python -m pytest tabfm/trading/tests/test_latest_results.py -v`
Expected: PASS.

- [ ] **Step 6: Eyeball the real output**

Run: `bash scripts/latest_results.sh`
Expected: three sections print. With the current repo state, LAST RUN shows a `🧪 selftest` line, LATEST RECOMMENDATION shows the `## 2026-07-24` card, CURRENT BOOK shows the portfolio summary with `(none)` open positions. Confirm no stderr/tracebacks leak through.

- [ ] **Step 7: Commit**

```bash
git add scripts/latest_results.sh tabfm/trading/tests/test_latest_results.py
git commit -m "feat: local-only latest-run-results gather helper + smoke test"
```

---

### Task 2: /latest-run-results orchestration skill

**Files:**
- Create: `.claude/skills/latest-run-results/SKILL.md`

**Interfaces:**
- Consumes: `scripts/latest_results.sh` (Task 1) via Bash — its three section headers and content. Read-only Robinhood MCP tools listed in Global Constraints.
- Produces: a skill invocable as `/latest-run-results` that prints one formatted briefing. No code consumers.

- [ ] **Step 1: Write the skill file**

Create `.claude/skills/latest-run-results/SKILL.md`:

```markdown
---
name: latest-run-results
description: Full current briefing on the advisor — last run outcome, latest recommendation, current book, and a live read-only Robinhood reconciliation with pending actions. Use when the user asks for the latest run results, run status, or where the book stands.
allowed-tools: Bash(bash scripts/latest_results.sh), Bash(cat data/RECOMMENDATIONS.md), Bash(ls data/run-logs/*), Read, mcp__robinhood-trading__get_portfolio, mcp__robinhood-trading__get_accounts, mcp__robinhood-trading__get_equity_positions, mcp__robinhood-trading__get_option_positions
---

Produce ONE read-only briefing on the latest advisor run and current state.
This is advisory only — NEVER place orders and NEVER mutate `data/journal.db`
or `data/RECOMMENDATIONS.md`.

## Steps

1. **Gather local (deterministic).** Run `bash scripts/latest_results.sh`. It
   prints three sections — `== LAST RUN ==` (mode/timestamp/outcome of the
   newest `data/run-logs/*.log`), `== LATEST RECOMMENDATION ==` (the newest
   `RECOMMENDATIONS.md` card), and `== CURRENT BOOK ==` (bankroll, drawdown,
   mode, open positions, exposure via `portfolio_summary`). Treat this as the
   source of truth for the advisor's tracked state.

2. **Gather live (Robinhood, read-only).** Call `get_portfolio` and
   `get_option_positions` (and `get_equity_positions` if relevant) for current
   brokerage state. If ANY MCP call fails or times out (offline / after-hours /
   auth), do NOT retry endlessly and do NOT block — mark live data unavailable
   and continue with local-only sections.

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
```

- [ ] **Step 2: Verify the skill is registered**

Run: `ls .claude/skills/latest-run-results/SKILL.md`
Then start a Claude session in the repo and confirm `/latest-run-results` appears in the skill list (the skill loader reads `.claude/skills/*/SKILL.md`).

- [ ] **Step 3: Invoke and eyeball the briefing**

Invoke `/latest-run-results` in a live session. Expected with the current repo
state: Last run shows the selftest warning, Recommendation shows the 2026-07-24
card, BANKROLL/BOOK show equity $2,000 / flat, and — since the paper book is
flat — RECONCILE lists any live Robinhood positions as untracked (or `none`),
with a clean PENDING ACTIONS block. Confirm no orders are placed and no files
are modified (`git status` clean afterward).

- [ ] **Step 4: Commit**

```bash
git add .claude/skills/latest-run-results/SKILL.md
git commit -m "feat: /latest-run-results briefing skill"
```

---

## Self-Review

**Spec coverage:**
- Full current briefing (last run + book + live) → Tasks 1 (local) + 2 (live/format). ✓
- Local + live Robinhood data source → Task 1 local gather, Task 2 steps 2–3. ✓
- Report + flag drift (no auto-fix) → Task 2 step 3 + PENDING ACTIONS; Global Constraint read-only. ✓
- Output format / card → Task 2 step 4 matches spec. ✓
- Edge cases (no run, empty book, MCP unavailable, malformed recs) → Task 1 script branches + Task 2 rules. ✓
- Testing (shell smoke test; LLM layer eyeballed) → Task 1 steps 1–6, Task 2 step 3. ✓

**Placeholder scan:** none — full script, test, and skill content inline.

**Type consistency:** the three header strings (`== LAST RUN ==`, `== LATEST RECOMMENDATION ==`, `== CURRENT BOOK ==`) are identical in the script, the smoke test, and the skill's step 1 description. `portfolio_summary(as_of=date.today())` matches the real signature. ✓
