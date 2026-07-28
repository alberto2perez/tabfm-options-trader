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
    if printf '%s' "$TAIL" | grep -q 'Outcome: FAILURE\|pipeline did not run'; then
      STATUS="⚠️ exit 0 but run reported failure"
    else
      STATUS="✅ success"
    fi
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
