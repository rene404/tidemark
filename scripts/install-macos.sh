#!/bin/bash
set -euo pipefail
if [ "$(uname -s)" != "Darwin" ]; then
  echo "Die Installation benötigt macOS."
  exit 1
fi
if [ "$(id -u)" = "0" ]; then
  echo "Im angemeldeten Benutzerkonto ausführen, ohne sudo."
  exit 1
fi
MACSCOPE_SOURCE="$(cd -- "$(dirname -- "$0")/.." && pwd)"
MACSCOPE_INSTALL="$HOME/Applications/MacScope"
MACSCOPE_AUTOSTART="no"
if [ "${1:-}" = "--autostart" ] && [ "$#" = "1" ]; then
  MACSCOPE_AUTOSTART="yes"
elif [ "$#" != "0" ]; then
  echo "Aufruf: ./install.command [--autostart]"
  exit 1
fi
MACSCOPE_PYTHON="${MACSCOPE_PYTHON:-}"
if [ -z "$MACSCOPE_PYTHON" ]; then
  for MACSCOPE_CANDIDATE in python3.13 python3.12 python3.11 python3.14 python3; do
    if command -v "$MACSCOPE_CANDIDATE" >/dev/null 2>&1 && "$MACSCOPE_CANDIDATE" -c 'import sys; raise SystemExit(not ((3,11) <= sys.version_info[:2] < (3,15)))' 2>/dev/null; then
      MACSCOPE_PYTHON="$(command -v "$MACSCOPE_CANDIDATE")"
      break
    fi
  done
fi
if [ -z "$MACSCOPE_PYTHON" ] || ! "$MACSCOPE_PYTHON" -c 'import sys; raise SystemExit(not ((3,11) <= sys.version_info[:2] < (3,15)))'; then
  echo "Bitte Python 3.11–3.14 von https://www.python.org/downloads/macos/ installieren. Empfehlung: 3.13."
  exit 1
fi
"$MACSCOPE_PYTHON" - "$MACSCOPE_SOURCE" "$MACSCOPE_INSTALL" <<'PY'
import os, pathlib, platform, shutil, sys
version = tuple(int(part) for part in platform.mac_ver()[0].split('.')[:2])
if version < (12, 0):
    raise SystemExit('Dieses erste Paket ist für macOS 12 oder neuer vorgesehen. Bitte macOS-Version für eine angepasste Variante mitteilen.')
source, destination = (pathlib.Path(arg).resolve() for arg in sys.argv[1:])
print(f'macOS {platform.mac_ver()[0]} · {platform.machine()} · Python {platform.python_version()}')
destination.mkdir(parents=True, exist_ok=True)
if destination.stat().st_uid != os.getuid():
    raise SystemExit('Das Installationsverzeichnis muss dir gehören.')
if source != destination:
    for name in ['macscope', 'scripts', 'docs']:
        shutil.copytree(source / name, destination / name, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for name in ['pyproject.toml', 'README.md', 'install.command', 'start.command']:
        shutil.copy2(source / name, destination / name)
PY
if [ ! -x "$MACSCOPE_INSTALL/.venv/bin/python" ]; then
  "$MACSCOPE_PYTHON" -m venv "$MACSCOPE_INSTALL/.venv"
fi
"$MACSCOPE_INSTALL/.venv/bin/python" -m pip install "$MACSCOPE_INSTALL"
"$MACSCOPE_INSTALL/.venv/bin/python" -m macscope diagnose
echo "Installiert: $MACSCOPE_INSTALL"
if [ "$MACSCOPE_AUTOSTART" = "yes" ]; then
  "$MACSCOPE_INSTALL/.venv/bin/python" "$MACSCOPE_INSTALL/scripts/autostart.py" install
else
  echo "Starten mit: $MACSCOPE_INSTALL/start.command"
fi
echo "Dashboard: http://127.0.0.1:8765/"
echo "Schlüssel anzeigen: \"$MACSCOPE_INSTALL/.venv/bin/macscope\" token"
