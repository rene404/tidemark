#!/bin/bash
# Baut CaptureProbe.app (ad-hoc signiert) nach ~/Applications oder in das angegebene Verzeichnis.
#   bash tools/capture-probe/build.sh [ZIELORDNER]
#   SIGN_IDENTITY="Name des Zertifikats" bash tools/capture-probe/build.sh   (statt ad hoc)
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
# Eigene Build-Nummer je Build: ändert die Signatur wie bei einem echten Update
# (sonst ergibt derselbe Quellcode denselben cdhash).
/usr/bin/plutil -replace CFBundleVersion -string "$(date +%Y%m%d%H%M%S)" "$APP/Contents/Info.plist"
# macOS bindet Berechtigungen an die Signatur. Ad hoc ändert sie bei jedem Neubau,
# ein eigenes Zertifikat (SIGN_IDENTITY) bleibt stabil. Beides ist Teil des Tests.
codesign --force --sign "${SIGN_IDENTITY:--}" --identifier local.tidemark.capture-probe "$APP"
echo "Signatur: ${SIGN_IDENTITY:-ad hoc}"
codesign -d -r- "$APP" 2>&1 | sed -n 's/^#* *designated => /Designated Requirement: /p'
codesign --verify --verbose=1 "$APP"
echo "Gebaut: $APP"
echo "cdhash: $(codesign -dvvv "$APP" 2>&1 | sed -n 's/^CDHash=//p')"
