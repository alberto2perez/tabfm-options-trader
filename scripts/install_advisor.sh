#!/bin/bash
# Install / uninstall the advisor LaunchAgents.
# Usage: install_advisor.sh {install|install-wake|uninstall|status}
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
LA="$HOME/Library/LaunchAgents"
UID_NUM="$(id -u)"
AGENTS=(com.tabfm.advisor.entry com.tabfm.advisor.audit)
WAKE_AGENT=com.tabfm.advisor.stayawake
ALL_AGENTS=("${AGENTS[@]}" "$WAKE_AGENT")

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
    for a in "${ALL_AGENTS[@]}"; do
      launchctl bootout "gui/$UID_NUM/$a" 2>/dev/null || true
      rm -f "$LA/$a.plist"
      echo "removed: $a"
    done
    sudo pmset repeat cancel 2>/dev/null || true
    ;;
  status)
    for a in "${ALL_AGENTS[@]}"; do
      printf '%s: ' "$a"
      launchctl print "gui/$UID_NUM/$a" >/dev/null 2>&1 && echo "loaded" || echo "not loaded"
    done
    ;;
  install-wake)
    mkdir -p "$LA"
    install_one "$WAKE_AGENT"
    echo "scheduling daily wake at 09:56 weekdays (requires sudo; AC power only)..."
    sudo pmset repeat wakeorpoweron MTWRF 09:56:00
    echo "wake layer installed. NOTE: only reliable on AC power."
    ;;
  *)
    echo "usage: install_advisor.sh {install|install-wake|uninstall|status}"; exit 2 ;;
esac
