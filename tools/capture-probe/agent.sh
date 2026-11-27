#!/bin/bash
# Dauertest als LaunchAgent im angemeldeten Konto (so, wie das spätere Live-Modul liefe).
#   bash tools/capture-probe/agent.sh install [MINUTEN] [APP-PFAD]
#   bash tools/capture-probe/agent.sh remove
set -euo pipefail
LABEL="local.tidemark.capture-probe"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
case "${1:-}" in
  install)
    MINUTES="${2:-10}"
    APP="${3:-$HOME/Applications/CaptureProbe.app}"
    [ -x "$APP/Contents/MacOS/capture-probe" ] || { echo "Erst bauen: bash tools/capture-probe/build.sh"; exit 1; }
    mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
    /usr/bin/plutil -create xml1 "$PLIST"
    /usr/bin/plutil -insert Label -string "$LABEL" "$PLIST"
    /usr/bin/plutil -insert ProgramArguments -array "$PLIST"
    /usr/bin/plutil -insert ProgramArguments.0 -string "$APP/Contents/MacOS/capture-probe" "$PLIST"
    /usr/bin/plutil -insert ProgramArguments.1 -string "loop" "$PLIST"
    /usr/bin/plutil -insert ProgramArguments.2 -string "$MINUTES" "$PLIST"
    /usr/bin/plutil -insert RunAtLoad -bool true "$PLIST"
    /usr/bin/plutil -insert LimitLoadToSessionType -string "Aqua" "$PLIST"
    /usr/bin/plutil -insert StandardErrorPath -string "$HOME/Library/Logs/tidemark-capture-probe.err" "$PLIST"
    launchctl bootstrap "$DOMAIN" "$PLIST"
    echo "Dauertest läuft alle $MINUTES min. Protokoll: ~/Library/Logs/tidemark-capture-probe.log"
    ;;
  remove)
    rm -f "$PLIST"
    echo "Dauertest entfernt. Protokoll bleibt erhalten."
    ;;
  *)
    echo "Aufruf: agent.sh install [MINUTEN] [APP-PFAD] | remove"; exit 2 ;;
esac
