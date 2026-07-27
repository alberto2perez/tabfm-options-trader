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
