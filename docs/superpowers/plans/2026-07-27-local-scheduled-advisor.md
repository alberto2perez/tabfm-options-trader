# Local Scheduled Advisor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the advisor hands-off on the user's Mac — entry pipeline weekdays 10:00 local, midday audit weekdays 12:00 local — each run driven by a headless Claude session that executes the tested Python entrypoints, notifies via a macOS banner, and commits `data/`.

**Architecture:** `launchd` LaunchAgents fire per-mode at local time → a bash wrapper (`scripts/run_advisor.sh`) holds the Mac awake with `caffeinate` and runs `claude -p` (Sonnet, no MCP) → Claude runs `python -m tabfm.trading.run_nightly`/`run_audit`, interprets output, sends the banner, and commits+pushes `data/`. An optional wake layer (one `pmset` daily wake + a long `caffeinate` LaunchAgent) covers lid-closed-on-AC operation.

**Tech Stack:** macOS `launchd` (LaunchAgents), `pmset`, `caffeinate`, `osascript`, bash, Claude Code CLI (`claude -p`), existing Python pipeline (`venv`, `robin_stocks`, cached TabFM).

## Global Constraints

- Approach is **#1 — Claude orchestrates, tested Python computes.** The headless session runs the Python entrypoints; it does NOT re-improvise fetch/snapshot/scoring. No Robinhood MCP is attached.
- **PAPER TRADING ONLY.** No order placement. Runs modify **`data/` only** — never source code.
- Times are **local (America/New_York)**; `launchd`/`pmset` use local time so DST is automatic. Do NOT hardcode UTC.
- Model for the headless session: **`claude-sonnet-4-6`**.
- Notifications: **macOS banner only** (no email). Full detail lives in `data/RECOMMENDATIONS.md`.
- **Always start from fresh state:** every real run does `git pull --rebase --autostash` before running, so the local repo tracks the latest. Pull failure is non-fatal (warn + continue on local state).
- Each successful run: **commit + push `data/`**. Push failure is non-fatal (local commit stands).
- Repo root: the git repo containing this plan. Scripts derive it as `$(cd "$(dirname "$0")/.." && pwd)` — never hardcode an absolute user path in committed files; the installer substitutes `__REPO__` when writing to `~/Library/LaunchAgents`.
- venv Python: `venv/bin/python`; activate via `venv/bin/activate`.
- Wake layer is **AC-power-only** (macOS suppresses scheduled wakes and `caffeinate -s` on battery). Document, don't promise battery operation.

---

### Task 1: Repo hygiene & secrets guard

**Files:**
- Modify: `.gitignore`
- Create: `.env.example`
- Create: `scripts/.gitkeep`

**Interfaces:**
- Produces: `scripts/` directory (all later scripts live here); a gitignored `.env` convention holding `RH_USER`/`RH_PASS`; `data/run-logs/` ignored.

- [ ] **Step 1: Add ignores.** Append to `.gitignore`:

```gitignore
# Local scheduled-advisor secrets and run logs
.env
data/run-logs/
```

- [ ] **Step 2: Create `.env.example`** (committed template; real `.env` stays untracked):

```dotenv
# Robinhood credentials for the pure-Python LiveAdapter (robin_stocks).
# Copy to .env and fill in. Alternatively, leave blank and establish a cached
# session once with an interactive `rh.login()` (see docs/LOCAL_SCHEDULING.md).
RH_USER=
RH_PASS=
```

- [ ] **Step 3: Create the scripts dir placeholder.** Create `scripts/.gitkeep` (empty file) so the directory is tracked.

- [ ] **Step 4: Verify `.env` is ignored.**

Run: `touch .env && git check-ignore .env && git status --porcelain .env`
Expected: `.env` is printed by `check-ignore`; `git status` shows nothing (ignored). Then `rm .env`.

- [ ] **Step 5: Commit.**

```bash
git add .gitignore .env.example scripts/.gitkeep
git commit -m "chore: gitignore .env + run-logs, add .env.example and scripts/ dir"
```

---

### Task 2: Claude prompt files (entry + audit)

**Files:**
- Create: `scripts/advisor_prompt_entry.md`
- Create: `scripts/advisor_prompt_audit.md`

**Interfaces:**
- Consumes: the Python entrypoints `python -m tabfm.trading.run_nightly` and `python -m tabfm.trading.run_audit`.
- Produces: two prompt files the wrapper passes to `claude -p`. Referenced by Task 3 as `$REPO/scripts/advisor_prompt_entry.md` / `_audit.md`.

- [ ] **Step 1: Write `scripts/advisor_prompt_entry.md`** (verbatim):

```markdown
You are the local scheduled options advisor for this repository (PAPER TRADING).
Run fully non-interactively — never ask questions, just act.

1. Run the tested entry pipeline from the repo root:
   `python -m tabfm.trading.run_nightly`
   It fetches live Robinhood data via robin_stocks, scores candidates with the
   locally-cached TabFM model, audits open positions, logs the paper trade,
   prepends the recommendation to data/RECOMMENDATIONS.md, and prints a
   PORTFOLIO SUMMARY.

2. Read stdout and stderr.
   - FAILURE (data fetch failed, Robinhood auth/token expired, weekend/holiday,
     or any traceback): send a macOS banner and STOP without committing:
     `osascript -e 'display notification "<short reason>" with title "Advisor ❌ entry"'`
   - SUCCESS: craft a ONE-LINE headline of the outcome, e.g.
     "SPY PUT 545/540 $0.62", "GATED — FOMC", or "No qualifying trade", then:
     `osascript -e 'display notification "<headline>" with title "Advisor ✓ entry"'`

3. On SUCCESS only, commit and push state — data/ ONLY, never source:
   `git add data/`
   `git commit -m "nightly: <YYYY-MM-DD> — <headline>"`
   `git push`
   If the push fails (offline/creds), keep the local commit and note the push
   error in your final report.

4. Final report (plain text): the recommendation / no-trade / GATED reason, any
   positions the auditor closed, and the PORTFOLIO SUMMARY block.

Hard rules: PAPER ONLY; never place/cancel/review orders; never modify source
files; only ever touch data/.
```

- [ ] **Step 2: Write `scripts/advisor_prompt_audit.md`** (verbatim):

```markdown
You are the local scheduled options advisor — MIDDAY AUDIT pass (PAPER TRADING).
Run fully non-interactively — never ask questions, just act. This pass manages
OPEN positions and NEVER enters a new trade.

1. Run the tested audit pipeline from the repo root:
   `python -m tabfm.trading.run_audit`
   It fetches current marks via robin_stocks and audits open positions
   (stop / profit-target / DTE), printing a summary and any [TrendGuard] alert.

2. Read stdout and stderr.
   - FAILURE (data fetch failed, auth/token expired, weekend/holiday, or any
     traceback): send a macOS banner and STOP without committing:
     `osascript -e 'display notification "<short reason>" with title "Advisor ❌ audit"'`
   - SUCCESS: craft a ONE-LINE headline, e.g. "closed 1 (stop) SPY" or
     "no changes", then:
     `osascript -e 'display notification "<headline>" with title "Advisor ✓ audit"'`

3. Commit ONLY if a position closed (data/ ONLY, never source):
   `git add data/`
   `git commit -m "midday-audit: <YYYY-MM-DD> — closed N"`
   `git push`
   If nothing closed, commit nothing. If push fails, keep the local commit and
   note it.

4. Final report (plain text): positions checked, any closed and why, and any
   TREND ALERT.

Hard rules: PAPER ONLY; never enter a new trade; never place/cancel/review
orders; never modify source files; only ever touch data/.
```

- [ ] **Step 3: Verify both files exist and are non-empty.**

Run: `wc -l scripts/advisor_prompt_entry.md scripts/advisor_prompt_audit.md`
Expected: both files listed with a non-zero line count.

- [ ] **Step 4: Commit.**

```bash
git add scripts/advisor_prompt_entry.md scripts/advisor_prompt_audit.md
git commit -m "feat: headless Claude prompts for entry + audit runs"
```

---

### Task 3: Wrapper script `scripts/run_advisor.sh`

**Files:**
- Create: `scripts/run_advisor.sh`

**Interfaces:**
- Consumes: `scripts/advisor_prompt_entry.md` / `_audit.md` (Task 2); `venv/bin/activate`; optional `.env`.
- Produces: executable `scripts/run_advisor.sh {entry|audit}`. Behavior: activates venv, loads `.env`, `git pull --rebase --autostash` (warn+continue on failure), runs `caffeinate -i claude -p <prompt>` (Sonnet, bypassPermissions, scoped tools), tees to `data/run-logs/<stamp>-<mode>.log`, and fires a fallback banner if `claude` exits non-zero. Honors `ADVISOR_SELFTEST=1` to validate plumbing without a real run. Invoked by the LaunchAgents in Task 4.

- [ ] **Step 1: Write `scripts/run_advisor.sh`** (verbatim):

```bash
#!/bin/bash
# Local scheduled advisor wrapper.
# Usage: run_advisor.sh {entry|audit}
# ADVISOR_SELFTEST=1 validates wrapper plumbing without invoking Claude/pipeline.
set -uo pipefail

MODE="${1:-}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO" || { echo "cannot cd to repo"; exit 1; }

LOGDIR="$REPO/data/run-logs"
mkdir -p "$LOGDIR"
STAMP="$(date +%Y-%m-%d-%H%M%S)"
LOG="$LOGDIR/${STAMP}-${MODE:-none}.log"

notify() { # $1=title $2=message
  /usr/bin/osascript -e "display notification \"$2\" with title \"$1\"" >/dev/null 2>&1 || true
}

case "$MODE" in
  entry) PROMPT_FILE="$REPO/scripts/advisor_prompt_entry.md" ;;
  audit) PROMPT_FILE="$REPO/scripts/advisor_prompt_audit.md" ;;
  *)
    echo "usage: run_advisor.sh {entry|audit}" | tee "$LOG"
    notify "Advisor ❌" "bad mode: '${MODE}'"
    exit 2
    ;;
esac

# Activate venv and load optional .env (RH_USER/RH_PASS).
# shellcheck disable=SC1091
source "$REPO/venv/bin/activate"
if [ -f "$REPO/.env" ]; then set -a; . "$REPO/.env"; set +a; fi

# Plumbing self-test: no Claude, no trades, no commits.
if [ "${ADVISOR_SELFTEST:-0}" = "1" ]; then
  echo "[selftest] mode=$MODE repo=$REPO python=$(command -v python)" | tee "$LOG"
  notify "Advisor 🧪" "selftest $MODE OK"
  exit 0
fi

# Always start from the latest committed state (warn but continue on failure).
echo "== git pull --rebase --autostash ==" | tee -a "$LOG"
if ! git pull --rebase --autostash 2>&1 | tee -a "$LOG"; then
  notify "Advisor ⚠️" "$MODE: git pull failed — running on local state"
fi

# Real run: hold the Mac awake for the duration, drive via headless Claude.
caffeinate -i claude -p "$(cat "$PROMPT_FILE")" \
  --model claude-sonnet-4-6 \
  --permission-mode bypassPermissions \
  --allowedTools "Bash Read Write Edit Glob Grep" \
  2>&1 | tee "$LOG"
STATUS="${PIPESTATUS[0]}"

if [ "$STATUS" -ne 0 ]; then
  notify "Advisor ❌" "$MODE run failed to launch (exit $STATUS) — see log"
fi
exit "$STATUS"
```

- [ ] **Step 2: Make it executable.**

Run: `chmod +x scripts/run_advisor.sh`

- [ ] **Step 3: Syntax-check the script.**

Run: `bash -n scripts/run_advisor.sh && echo OK`
Expected: `OK` (no syntax errors).

- [ ] **Step 4: Verify the self-test path (safe — no Claude, no trades).**

Run: `ADVISOR_SELFTEST=1 scripts/run_advisor.sh entry`
Expected: prints `[selftest] mode=entry repo=… python=…`, exit 0, a "Advisor 🧪 selftest entry OK" banner appears, and a log file exists under `data/run-logs/`.

- [ ] **Step 5: Verify the bad-mode path.**

Run: `scripts/run_advisor.sh bogus; echo "exit=$?"`
Expected: usage message, a "Advisor ❌ bad mode" banner, `exit=2`.

- [ ] **Step 6: Commit.**

```bash
git add scripts/run_advisor.sh
git commit -m "feat: run_advisor.sh wrapper (caffeinate + headless Claude + fallback banner)"
```

---

### Task 4: LaunchAgent plist templates (entry + audit)

**Files:**
- Create: `scripts/com.tabfm.advisor.entry.plist`
- Create: `scripts/com.tabfm.advisor.audit.plist`

**Interfaces:**
- Consumes: `scripts/run_advisor.sh` (Task 3).
- Produces: two plist templates containing the `__REPO__` placeholder. Installed (with `__REPO__` substituted) by Task 5. Labels: `com.tabfm.advisor.entry`, `com.tabfm.advisor.audit`. Fire weekdays (Weekday 1–5) at 10:00 / 12:00 local. `RunAtLoad` is false.

- [ ] **Step 1: Write `scripts/com.tabfm.advisor.entry.plist`** (verbatim):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.tabfm.advisor.entry</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>__REPO__/scripts/run_advisor.sh</string>
    <string>entry</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>10</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>10</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>10</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>10</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>10</integer><key>Minute</key><integer>0</integer></dict>
  </array>
  <key>RunAtLoad</key>
  <false/>
  <key>StandardOutPath</key>
  <string>__REPO__/data/run-logs/launchd-entry.out</string>
  <key>StandardErrorPath</key>
  <string>__REPO__/data/run-logs/launchd-entry.err</string>
</dict>
</plist>
```

- [ ] **Step 2: Write `scripts/com.tabfm.advisor.audit.plist`** (verbatim — identical except Label, mode arg, Hour 12, log names):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.tabfm.advisor.audit</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>__REPO__/scripts/run_advisor.sh</string>
    <string>audit</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>12</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>12</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>12</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>12</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>12</integer><key>Minute</key><integer>0</integer></dict>
  </array>
  <key>RunAtLoad</key>
  <false/>
  <key>StandardOutPath</key>
  <string>__REPO__/data/run-logs/launchd-audit.out</string>
  <key>StandardErrorPath</key>
  <string>__REPO__/data/run-logs/launchd-audit.err</string>
</dict>
</plist>
```

- [ ] **Step 3: Lint both plists** (substituting a dummy repo path so the template is valid XML).

Run: `for f in scripts/com.tabfm.advisor.*.plist; do sed 's#__REPO__#/tmp/x#g' "$f" | plutil -lint -; done`
Expected: `OK` for each (reads from stdin).

- [ ] **Step 4: Commit.**

```bash
git add scripts/com.tabfm.advisor.entry.plist scripts/com.tabfm.advisor.audit.plist
git commit -m "feat: LaunchAgent plists for entry (10:00) + audit (12:00) weekdays"
```

---

### Task 5: Installer `scripts/install_advisor.sh`

**Files:**
- Create: `scripts/install_advisor.sh`

**Interfaces:**
- Consumes: the two plist templates (Task 4) and `run_advisor.sh` (Task 3).
- Produces: `scripts/install_advisor.sh {install|uninstall|status}`. `install` substitutes `__REPO__`, writes both plists to `~/Library/LaunchAgents/`, and bootstraps+enables them under `gui/<uid>`. Task 6 extends this file with the `--with-wake` path.

- [ ] **Step 1: Write `scripts/install_advisor.sh`** (verbatim):

```bash
#!/bin/bash
# Install / uninstall the advisor LaunchAgents.
# Usage: install_advisor.sh {install|uninstall|status}
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
LA="$HOME/Library/LaunchAgents"
UID_NUM="$(id -u)"
AGENTS=(com.tabfm.advisor.entry com.tabfm.advisor.audit)

install_one() {
  local name="$1"
  sed "s#__REPO__#$REPO#g" "$REPO/scripts/$name.plist" > "$LA/$name.plist"
  launchctl bootout "gui/$UID_NUM/$name" 2>/dev/null || true
  launchctl bootstrap "gui/$UID_NUM" "$LA/$name.plist"
  launchctl enable "gui/$UID_NUM/$name"
  echo "installed: $name"
}

case "${1:-install}" in
  install)
    chmod +x "$REPO/scripts/run_advisor.sh"
    mkdir -p "$LA" "$REPO/data/run-logs"
    for a in "${AGENTS[@]}"; do install_one "$a"; done
    echo "done. schedule: entry 10:00 / audit 12:00 weekdays (local time)."
    ;;
  uninstall)
    for a in "${AGENTS[@]}"; do
      launchctl bootout "gui/$UID_NUM/$a" 2>/dev/null || true
      rm -f "$LA/$a.plist"
      echo "removed: $a"
    done
    ;;
  status)
    for a in "${AGENTS[@]}"; do
      printf '%s: ' "$a"
      launchctl print "gui/$UID_NUM/$a" >/dev/null 2>&1 && echo "loaded" || echo "not loaded"
    done
    ;;
  *)
    echo "usage: install_advisor.sh {install|uninstall|status}"; exit 2 ;;
esac
```

- [ ] **Step 2: Make executable + syntax-check.**

Run: `chmod +x scripts/install_advisor.sh && bash -n scripts/install_advisor.sh && echo OK`
Expected: `OK`.

- [ ] **Step 3: Install and confirm both agents load.**

Run: `scripts/install_advisor.sh install && scripts/install_advisor.sh status`
Expected: "installed: …" for both, then status shows both `loaded`. Confirm files exist: `ls ~/Library/LaunchAgents/com.tabfm.advisor.*.plist`.

- [ ] **Step 4: Commit.**

```bash
git add scripts/install_advisor.sh
git commit -m "feat: install_advisor.sh (install/uninstall/status for LaunchAgents)"
```

---

### Task 6: Optional wake layer (lid-closed on AC)

**Files:**
- Create: `scripts/com.tabfm.advisor.stayawake.plist`
- Modify: `scripts/install_advisor.sh`

**Interfaces:**
- Consumes: nothing new.
- Produces: a single daily `pmset` wake + a `caffeinate -s` LaunchAgent that holds the Mac awake across BOTH the 10:00 and 12:00 runs, then releases. Installed via `install_advisor.sh install-wake` and removed via `uninstall`. AC-power-only.

Rationale: `pmset repeat` supports only ONE repeating wake, so instead of two wakes we wake once at 09:56 and hold the system awake through ~12:16 with one long `caffeinate -s`, covering both runs.

- [ ] **Step 1: Write `scripts/com.tabfm.advisor.stayawake.plist`** (verbatim — fires 09:57 weekdays, holds awake 8340s ≈ 2h19m, to ~12:16):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.tabfm.advisor.stayawake</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/caffeinate</string>
    <string>-s</string>
    <string>-t</string>
    <string>8340</string>
  </array>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>9</integer><key>Minute</key><integer>57</integer></dict>
    <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>9</integer><key>Minute</key><integer>57</integer></dict>
    <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>9</integer><key>Minute</key><integer>57</integer></dict>
    <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>9</integer><key>Minute</key><integer>57</integer></dict>
    <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>9</integer><key>Minute</key><integer>57</integer></dict>
  </array>
  <key>RunAtLoad</key>
  <false/>
</dict>
</plist>
```

- [ ] **Step 2: Lint the plist.**

Run: `plutil -lint scripts/com.tabfm.advisor.stayawake.plist`
Expected: `OK`.

- [ ] **Step 3: Extend `install_advisor.sh`** — add a wake install/uninstall path. Change the `AGENTS` line and the `case` to add `install-wake`, and include the stayawake agent in `uninstall`/`status`. Replace the `AGENTS=(...)` line with:

```bash
AGENTS=(com.tabfm.advisor.entry com.tabfm.advisor.audit)
WAKE_AGENT=com.tabfm.advisor.stayawake
ALL_AGENTS=("${AGENTS[@]}" "$WAKE_AGENT")
```

Then add a new `install-wake)` branch to the `case` (before the `*)` default):

```bash
  install-wake)
    mkdir -p "$LA"
    install_one "$WAKE_AGENT"
    echo "scheduling daily wake at 09:56 weekdays (requires sudo; AC power only)..."
    sudo pmset repeat wakeorpoweron MTWRF 09:56:00
    echo "wake layer installed. NOTE: only reliable on AC power."
    ;;
```

And change the `uninstall)` and `status)` loops to iterate `"${ALL_AGENTS[@]}"` instead of `"${AGENTS[@]}"`, and in `uninstall)` add after the loop:

```bash
    sudo pmset repeat cancel 2>/dev/null || true
```

- [ ] **Step 4: Syntax-check the modified installer.**

Run: `bash -n scripts/install_advisor.sh && echo OK`
Expected: `OK`.

- [ ] **Step 5: (Optional, user-run) install the wake layer and verify the schedule.**

Run: `scripts/install_advisor.sh install-wake && pmset -g sched`
Expected: `stayawake` loads; `pmset -g sched` shows a repeating `wakeorpoweron … MTWRF 09:56:00`. (Skip if the user only runs lid-open.)

- [ ] **Step 6: Commit.**

```bash
git add scripts/com.tabfm.advisor.stayawake.plist scripts/install_advisor.sh
git commit -m "feat: optional AC-only wake layer (single pmset wake + long caffeinate)"
```

---

### Task 7: Documentation

**Files:**
- Create: `docs/LOCAL_SCHEDULING.md`
- Modify: `docs/OPERATING.md` (replace the stale "One-time Cloud Setup" pointer)

**Interfaces:**
- Consumes: all prior tasks.
- Produces: setup/runbook docs so the user can install, test, and maintain the schedule.

- [ ] **Step 1: Write `docs/LOCAL_SCHEDULING.md`** (verbatim):

```markdown
# Local Scheduling (macOS)

Runs the advisor hands-off on this Mac: entry weekdays 10:00 local, midday
audit weekdays 12:00 local. Each run pulls the latest (`git pull --rebase
--autostash`), then a headless Claude session executes the tested Python
pipeline, sends a macOS banner, and commits + pushes `data/`.

## One-time setup

1. Ensure the venv + deps exist and TabFM is cached (already true if you run
   locally). The `claude` CLI must be installed and logged in.
2. Robinhood auth — either:
   - `cp .env.example .env` and fill `RH_USER` / `RH_PASS`, or
   - establish a cached session once:
     `python -c "import robin_stocks.robinhood as rh; rh.login('USER','PASS')"`
3. Non-interactive git: the remote is SSH (`git@github-personal:…`), so ensure
   the SSH key is loaded in the keychain and passphrase-free for headless runs
   (`ssh-add --apple-use-keychain ~/.ssh/<key>`; test with `ssh -T
   git@github-personal`). Otherwise the run's `git pull`/`push` will hang or fail.
4. Install the schedule: `scripts/install_advisor.sh install`
5. (Optional, lid-closed on AC power) add the wake layer:
   `scripts/install_advisor.sh install-wake`  (prompts for sudo)

## Verify

- Plumbing only (no trades): `ADVISOR_SELFTEST=1 scripts/run_advisor.sh entry`
- Loaded agents: `scripts/install_advisor.sh status`
- A real manual run (during market hours): `scripts/run_advisor.sh entry`
- Logs: `data/run-logs/` (`<stamp>-<mode>.log`, plus `launchd-*.out/.err`).

## Notifications

macOS banners on completion (`Advisor ✓/❌ …`). Lid-closed banners queue in
Notification Center and appear on next unlock. Full detail is in
`data/RECOMMENDATIONS.md`.

## Maintenance

- **Robinhood token expired** → a run fails with an `Advisor ❌` banner; refresh
  by re-running the `rh.login(...)` command above, then let the next run go.
- **Change times** → edit the `StartCalendarInterval` in
  `scripts/com.tabfm.advisor.*.plist`, then `scripts/install_advisor.sh install`.
- **Remove everything** → `scripts/install_advisor.sh uninstall`.

## Caveats

- The Mac must be powered on at run time. Lid-closed operation needs the wake
  layer AND **AC power** — macOS suppresses scheduled wakes / `caffeinate -s` on
  battery.
- Runs touch `data/` only; source is never modified by a scheduled run.
```

- [ ] **Step 2: Update `docs/OPERATING.md`.** Replace the `## One-time Cloud Setup (to make it hands-off) — optional` section (lines beginning at that header through the end of its list) with:

```markdown
## Hands-off scheduling — local (macOS)

Cloud routines were evaluated and dropped: the cloud sandbox blocks HuggingFace
egress and is ephemeral, so the 12 GB TabFM model can't run there. Instead the
advisor runs **locally on a schedule** — entry 10:00 / audit 12:00 weekdays —
via `launchd` + a headless Claude session. See `docs/LOCAL_SCHEDULING.md` for
setup. Until installed, runs are session-assisted ("run tonight's nightly").
```

- [ ] **Step 3: Verify the docs render / links resolve.**

Run: `ls docs/LOCAL_SCHEDULING.md && grep -n "LOCAL_SCHEDULING" docs/OPERATING.md`
Expected: file exists; the OPERATING.md pointer matches.

- [ ] **Step 4: Commit.**

```bash
git add docs/LOCAL_SCHEDULING.md docs/OPERATING.md
git commit -m "docs: local scheduling setup + retire cloud-setup pointer"
```

---

### Task 8: End-to-end validation & activation

**Files:** none (verification only).

**Interfaces:**
- Consumes: everything above.
- Produces: a confirmed-working schedule.

- [ ] **Step 1: Confirm agents are loaded.**

Run: `scripts/install_advisor.sh status`
Expected: entry + audit `loaded` (and stayawake if the wake layer was installed).

- [ ] **Step 2: Dispatch a real manual entry run during market hours** (9:30–16:00 ET on a trading day).

Run: `scripts/run_advisor.sh entry`
Expected: the log under `data/run-logs/` shows the pipeline running; an `Advisor ✓ entry` (or `❌` with a clear reason) banner appears; on success a new `nightly: <date> — …` commit exists (`git log --oneline -1`) and `data/RECOMMENDATIONS.md` has today's entry on top.

- [ ] **Step 3: Failure-path check.** Temporarily point `.env` at bad creds (`RH_USER=nope RH_PASS=nope`), run `scripts/run_advisor.sh audit`, confirm an `Advisor ❌ audit` banner and NO new commit. Restore `.env` afterward.

- [ ] **Step 4: Schedule-fire check (optional but recommended).** Temporarily add a near-future `StartCalendarInterval` (e.g. 2 minutes ahead) to the entry plist, `install`, confirm it fires from `launchd` (new log appears without manual invocation), then remove the temporary interval and re-`install`.

- [ ] **Step 5: Final state.** Confirm `scripts/install_advisor.sh status` shows the real schedule loaded and the temporary test interval removed. Done — the advisor now runs weekdays at 10:00 and 12:00 local.
```
