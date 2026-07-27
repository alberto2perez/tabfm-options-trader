#!/bin/bash
# See advisor runs: whether one is active right now, and tail the latest run
# log live. Usage: advisor_watch.sh [entry|audit]  (optional mode filter)
#
# NOTE: a scheduled `claude -p` run does NOT appear in the interactive Claude
# TUI or on claude.ai. Its record is (1) this run log, (2) the process list
# below while it runs, and (3) a saved Claude session you can reopen with
# `claude --resume` (pick the scheduled run from the list).
cd "$(dirname "$0")/.." || exit 1
MODE="${1:-}"

echo "== active advisor processes =="
pgrep -fl "run_advisor.sh|claude -p|run_nightly|run_audit" || echo "(none running right now)"

echo
echo "== launchd jobs (a numeric PID column = running now) =="
launchctl list | grep -E "^PID|tabfm" || launchctl list | grep tabfm || echo "(agents not loaded)"

echo
echo "== recent run logs =="
ls -t data/run-logs/*"${MODE}"*.log 2>/dev/null | head -5 || echo "(no run logs yet)"

LATEST="$(ls -t data/run-logs/*"${MODE}"*.log 2>/dev/null | head -1)"
if [ -n "$LATEST" ]; then
  echo
  echo "== tailing $LATEST  (Ctrl-C to stop) =="
  tail -f -n +1 "$LATEST"
fi
