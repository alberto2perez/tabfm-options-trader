#!/bin/bash
# Advisor notification helper.
# Usage: notify.sh "<title>" "<message>" ["<open-target>"]
#
# When terminal-notifier is available, the banner's click/"Show" action opens
# <open-target> (a file or folder — e.g. data/RECOMMENDATIONS.md or the run
# log) instead of Script Editor. Falls back to a plain osascript banner (no
# click target) when terminal-notifier is not installed.
TITLE="${1:-Advisor}"
MSG="${2:-}"
TARGET="${3:-}"

TN="$(command -v terminal-notifier || true)"
if [ -n "$TN" ]; then
  if [ -n "$TARGET" ]; then
    "$TN" -title "$TITLE" -message "$MSG" -execute "open \"$TARGET\"" >/dev/null 2>&1 && exit 0
  else
    "$TN" -title "$TITLE" -message "$MSG" >/dev/null 2>&1 && exit 0
  fi
fi

/usr/bin/osascript -e "display notification \"$MSG\" with title \"$TITLE\"" >/dev/null 2>&1 || true
