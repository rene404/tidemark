#!/bin/bash
# Verwalteten Modus einrichten oder entfernen. Als Administrator ausführen:
#   sudo bash install-managed.command install --user BENUTZER
#   sudo bash install-managed.command remove
set -euo pipefail
if [ "$(id -u)" != "0" ]; then
  echo "Mit sudo ausführen: sudo bash install-managed.command install --user BENUTZER"
  exit 1
fi
MACSCOPE_SOURCE="$(cd -- "$(dirname -- "$0")" && pwd)"
MACSCOPE_PYTHON="${MACSCOPE_PYTHON:-}"
if [ -z "$MACSCOPE_PYTHON" ]; then
  for MACSCOPE_CANDIDATE in \
      /Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13 \
      /Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12 \
      /Library/Frameworks/Python.framework/Versions/3.11/bin/python3.11 \
      /opt/homebrew/bin/python3.13 /usr/local/bin/python3.13; do
    if [ -x "$MACSCOPE_CANDIDATE" ]; then
      MACSCOPE_PYTHON="$MACSCOPE_CANDIDATE"
      break
    fi
  done
fi
if [ -z "$MACSCOPE_PYTHON" ]; then
  echo "Kein Python 3.11–3.13 gefunden. Python 3.13 von python.org installieren oder MACSCOPE_PYTHON setzen."
  exit 1
fi
exec "$MACSCOPE_PYTHON" -I "$MACSCOPE_SOURCE/scripts/managed_setup.py" "$@"
