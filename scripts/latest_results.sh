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
  # Completion is marked either by the scheduled wrapper ("... run finished —
  # exit N ==", run_advisor.sh) or a session-assisted/manual run whose footer
  # is a bare "== exit N ==". Both end in "exit <N> ==", so match on that.
  if printf '%s' "$TAIL" | grep -q '\[selftest\]'; then
    STATUS="🧪 selftest — no real run"
  elif printf '%s' "$TAIL" | grep -qE 'TIMEOUT|exit (143|137) =='; then
    STATUS="⏱ timeout"
  elif printf '%s' "$TAIL" | grep -qE 'exit 0 =='; then
    if printf '%s' "$TAIL" | grep -q 'Outcome: FAILURE\|pipeline did not run'; then
      STATUS="⚠️ exit 0 but run reported failure"
    else
      STATUS="✅ success"
    fi
  elif printf '%s' "$TAIL" | grep -qE 'exit [1-9][0-9]* =='; then
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
  # New entries are PREPENDED, so the newest card is the FIRST "## " block:
  # print from the first "## " header up to (but not including) the next one.
  CARD="$(awk '/^## /{if (seen) exit; seen=1} seen' "$RECS")"
  if [ -n "$CARD" ]; then
    printf '%s\n' "$CARD" | sed 's/^/  /'
  else
    echo "  none on record"
  fi
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
