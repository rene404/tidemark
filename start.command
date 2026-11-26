#!/bin/bash
set -euo pipefail
MACSCOPE_ROOT="$(cd -- "$(dirname -- "$0")" && pwd)"
if [ ! -x "$MACSCOPE_ROOT/.venv/bin/python" ]; then
  echo "Bitte zuerst install.command ausführen."
  exit 1
fi
cd "$MACSCOPE_ROOT"
exec "$MACSCOPE_ROOT/.venv/bin/python" -m macscope run
