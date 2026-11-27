#!/bin/bash
set -euo pipefail
MACSCOPE_SOURCE="$(cd -- "$(dirname -- "$0")" && pwd)"
exec /bin/bash "$MACSCOPE_SOURCE/scripts/install-macos.sh" "$@"
