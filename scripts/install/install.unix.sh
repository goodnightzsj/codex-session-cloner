#!/usr/bin/env sh
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)"
APP_NAME="codex-session-toolkit"
VENV_DIR="${VENV_DIR:-$PROJECT_ROOT/.venv}"
EDITABLE=0
FORCE=0
PYTHON_BIN="${PYTHON_BIN:-}"

usage() {
  cat <<'EOF'
Usage: ./install.sh [--editable] [--force] [--python <python-bin>]

Options:
  --editable         Install in editable mode for local development
  --force            Recreate the local .venv before installing
  --python <bin>     Use a specific Python executable
  --help             Show this help text
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --editable)
      EDITABLE=1
      shift
      ;;
    --force)
      FORCE=1
      shift
      ;;
    --python)
      if [ "$#" -lt 2 ]; then
        echo "Error: --python requires a value." >&2
        exit 2
      fi
      PYTHON_BIN="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Error: unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

resolve_python() {
  if [ -n "$PYTHON_BIN" ]; then
    return
  fi
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
  elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
  else
    echo "Error: python3/python not found in PATH." >&2
    exit 127
  fi
}

install_package() {
  if [ "$EDITABLE" -eq 1 ]; then
    "$VENV_PYTHON" -m pip install --no-deps --no-build-isolation -e "$PROJECT_ROOT"
  else
    "$VENV_PYTHON" -m pip install --no-deps --no-build-isolation "$PROJECT_ROOT"
  fi
}

resolve_python

if [ "$FORCE" -eq 1 ] && [ -d "$VENV_DIR" ]; then
  rm -rf "$VENV_DIR"
fi

echo "============================================="
echo " Codex Session Toolkit - Installer (Unix)"
echo "============================================="
echo "Project:   $PROJECT_ROOT"
echo "Python:    $PYTHON_BIN"
echo "Venv:      $VENV_DIR"
if [ "$EDITABLE" -eq 1 ]; then
  echo "Mode:      editable"
else
  echo "Mode:      standard"
fi

"$PYTHON_BIN" -m venv "$VENV_DIR" --system-site-packages
VENV_PYTHON="$VENV_DIR/bin/python"

if ! "$VENV_PYTHON" -c "import setuptools" >/dev/null 2>&1; then
  echo "Error: setuptools is not available for the local installer environment." >&2
  echo "Tip: install setuptools for your base Python, then rerun ./install.sh ." >&2
  exit 1
fi

if ! install_package; then
  echo "Local no-build-isolation install failed; retrying with build isolation..." >&2
  if [ "$EDITABLE" -eq 1 ]; then
    "$VENV_PYTHON" -m pip install --no-deps -e "$PROJECT_ROOT"
  else
    "$VENV_PYTHON" -m pip install --no-deps "$PROJECT_ROOT"
  fi
fi

chmod +x \
  "$PROJECT_ROOT/codex-session-toolkit" \
  "$PROJECT_ROOT/codex-session-toolkit.command" \
  "$PROJECT_ROOT/install.sh" \
  "$PROJECT_ROOT/install.command" \
  "$PROJECT_ROOT/release.sh"

echo ""
echo "Install complete."
echo "Run now:"
echo "  ./codex-session-toolkit"
echo "Version:"
echo "  $VENV_DIR/bin/$APP_NAME --version"
