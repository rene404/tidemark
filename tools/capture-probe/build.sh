#!/bin/bash
# Baut CaptureProbe.app (ad-hoc signiert) nach ~/Applications oder in das angegebene Verzeichnis.
#   bash tools/capture-probe/build.sh [ZIELORDNER]
set -euo pipefail
HERE="$(cd -- "$(dirname -- "$0")" && pwd)"
TARGET="${1:-$HOME/Applications}"
APP="$TARGET/CaptureProbe.app"
command -v swiftc >/dev/null || { echo "swiftc fehlt: xcode-select --install"; exit 1; }
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
swiftc -O -swift-version 5 -target "$(uname -m)-apple-macos14.0" \
  -o "$WORK/capture-probe" "$HERE/main.swift"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cp "$WORK/capture-probe" "$APP/Contents/MacOS/capture-probe"
cp "$HERE/Info.plist" "$APP/Contents/Info.plist"
# Ad-hoc-Signatur: macOS bindet Berechtigungen an diese Signatur. Ein Neubau ändert sie
# (Teil des Tests: geht die Freigabe dabei verloren?).
codesign --force --sign - --identifier local.tidemark.capture-probe "$APP"
codesign --verify --verbose=1 "$APP"
echo "Gebaut: $APP"
echo "cdhash: $(codesign -dvvv "$APP" 2>&1 | sed -n 's/^CDHash=//p')"
