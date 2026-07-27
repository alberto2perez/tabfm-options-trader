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

notify() { # $1=title $2=message $3=optional open-target (file/folder)
  bash "$REPO/scripts/notify.sh" "$1" "$2" "${3:-}" || true
}

case "$MODE" in
  entry) PROMPT_FILE="$REPO/scripts/advisor_prompt_entry.md" ;;
  audit) PROMPT_FILE="$REPO/scripts/advisor_prompt_audit.md" ;;
  *)
    echo "usage: run_advisor.sh {entry|audit}" | tee "$LOG"
    notify "Advisor ❌" "bad mode: '${MODE}'" "$LOG"
    exit 2
    ;;
esac

# Activate venv and load optional .env (RH_USER/RH_PASS).
# shellcheck disable=SC1091
if ! source "$REPO/venv/bin/activate"; then
  echo "venv activation failed: $REPO/venv/bin/activate" | tee "$LOG"
  notify "Advisor ❌" "$MODE: venv activation failed" "$LOG"
  exit 1
fi
if [ -f "$REPO/.env" ]; then set -a; . "$REPO/.env"; set +a; fi

# Plumbing self-test: no Claude, no trades, no commits.
if [ "${ADVISOR_SELFTEST:-0}" = "1" ]; then
  echo "[selftest] mode=$MODE repo=$REPO python=$(command -v python)" | tee "$LOG"
  notify "Advisor 🧪" "selftest $MODE OK" "$LOG"
  exit 0
fi

# Always start from the latest committed state (warn but continue on failure).
echo "== git pull --rebase --autostash ==" | tee -a "$LOG"
if ! git pull --rebase --autostash 2>&1 | tee -a "$LOG"; then
  notify "Advisor ⚠️" "$MODE: git pull failed — running on local state" "$LOG"
fi

# Real run: hold the Mac awake, drive via headless Claude, bounded by a hard timeout.
TIMEOUT_SECS="${ADVISOR_TIMEOUT:-1800}"   # 30-min cap; override with ADVISOR_TIMEOUT
echo "== $(date '+%F %T') starting headless claude ($MODE) — timeout ${TIMEOUT_SECS}s ==" | tee -a "$LOG"

set -m   # give the background job its own process group so the watchdog can kill the whole tree
caffeinate -i claude -p "$(cat "$PROMPT_FILE")" \
  --model claude-sonnet-4-6 \
  --permission-mode bypassPermissions \
  --allowedTools "Bash Read Write Edit Glob Grep" \
  >> "$LOG" 2>&1 &
RUN_PID=$!
set +m

# Watchdog: SIGTERM (then SIGKILL) the whole process group if it overruns.
(
  sleep "$TIMEOUT_SECS"
  if kill -0 "$RUN_PID" 2>/dev/null; then
    echo "== $(date '+%F %T') TIMEOUT after ${TIMEOUT_SECS}s — terminating run ==" >> "$LOG"
    kill -TERM -"$RUN_PID" 2>/dev/null
    sleep 5
    kill -KILL -"$RUN_PID" 2>/dev/null
  fi
) &
WATCH_PID=$!

wait "$RUN_PID"; STATUS=$?
kill "$WATCH_PID" 2>/dev/null; wait "$WATCH_PID" 2>/dev/null   # cancel watchdog if the run finished first
echo "== $(date '+%F %T') run finished — exit $STATUS ==" | tee -a "$LOG"

if [ "$STATUS" -eq 143 ] || [ "$STATUS" -eq 137 ]; then
  notify "Advisor ❌" "$MODE TIMED OUT after ${TIMEOUT_SECS}s — killed" "$LOG"
elif [ "$STATUS" -ne 0 ]; then
  notify "Advisor ❌" "$MODE run failed (exit $STATUS) — see log" "$LOG"
fi
exit "$STATUS"
