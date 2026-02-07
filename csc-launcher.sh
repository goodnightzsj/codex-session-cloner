#!/usr/bin/env sh
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
TARGET_SCRIPT="$SCRIPT_DIR/codex-session-cloner.py"

if [ ! -f "$TARGET_SCRIPT" ]; then
  echo "Error: cannot find $TARGET_SCRIPT" >&2
  exit 1
fi

PYTHON_BIN="${PYTHON_BIN:-}"
if [ -z "$PYTHON_BIN" ]; then
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
  elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
  else
    echo "Error: python3/python not found in PATH." >&2
    exit 127
  fi
fi

echo "============================================="
echo " Codex Session Cloner - Launcher (Unix)"
echo "============================================="
echo ">> $PYTHON_BIN $TARGET_SCRIPT $*"

exec "$PYTHON_BIN" "$TARGET_SCRIPT" "$@"
