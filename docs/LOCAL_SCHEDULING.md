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

## Watching a run

A scheduled `claude -p` run does NOT show up in the interactive Claude TUI or on
claude.ai — it's headless. You can see it three ways:

- **`scripts/advisor_watch.sh [entry|audit]`** — shows whether a run is active,
  the launchd job status (a numeric PID = running now), and tails the latest run
  log live. This is the quickest "what's happening now" view.
- **The run log** — each run appends to `data/run-logs/<stamp>-<mode>.log` with
  `starting…` / `run finished — exit N` markers. `tail -f` the newest to follow.
- **`claude --resume`** — every run is saved as a Claude session; pick the
  scheduled run from the list to read its full transcript.

To watch the *pipeline itself* stream live (bypassing the headless session
entirely), just run `python -m tabfm.trading.run_nightly` in a terminal.

Each run is bounded by a hard timeout (default 30 min, `ADVISOR_TIMEOUT` env to
change); on overrun the whole process tree is killed and you get a TIMED OUT
banner.

## Notifications

macOS banners on completion (`Advisor ✓/❌ …`) via `terminal-notifier` — clicking
the banner opens the results (`data/RECOMMENDATIONS.md`) or the run log. Lid-closed
banners queue in Notification Center and appear on next unlock.

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
