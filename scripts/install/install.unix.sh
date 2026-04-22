#!/usr/bin/env sh
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)"
APP_NAME="aik"
VENV_DIR="${VENV_DIR:-$PROJECT_ROOT/.venv}"
EDITABLE=0
FORCE=0
NO_SCRIPTS=0
PYTHON_BIN="${PYTHON_BIN:-}"

usage() {
  cat <<'EOF'
Usage: ./install.sh [--editable] [--force] [--no-scripts] [--python <python-bin>]

Options:
  --editable         Install in editable mode for local development
  --force            Recreate the local .venv before installing
  --no-scripts       Install the package but don't keep aik / cst /
                     codex-session-toolkit / cc-clean console scripts in
                     <venv>/bin (use ``python -m ai_cli_kit`` instead).
                     Useful if you don't want extra commands on PATH.
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
    --no-scripts)
      NO_SCRIPTS=1
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

resolve_python

if [ "$FORCE" -eq 1 ] && [ -d "$VENV_DIR" ]; then
  rm -rf "$VENV_DIR"
fi

echo "============================================="
echo " AI CLI Kit - Installer (Unix)"
echo "============================================="
echo "Project:   $PROJECT_ROOT"
echo "Python:    $PYTHON_BIN"
echo "Venv:      $VENV_DIR"
if [ "$EDITABLE" -eq 1 ]; then
  echo "Mode:      editable"
else
  echo "Mode:      standard"
fi

# Drop --system-site-packages: it lets Apple's bundled setuptools<61 leak in,
# which silently builds an empty UNKNOWN-0.0.0 wheel because it can't read
# the PEP 621 [project] table. Our package has zero runtime deps so an
# isolated venv is strictly safer.
"$PYTHON_BIN" -m venv "$VENV_DIR"
VENV_PYTHON="$VENV_DIR/bin/python"

# Force-upgrade pip / setuptools / wheel inside the venv before installing
# the package. This is what produces a real ``ai-cli-kit-0.2.0`` wheel with
# proper console scripts (aik / cst / cc-clean) on stock macOS Python 3.9
# where the system pip is 21.3 and setuptools is 49 — old enough that pip
# falls back to building UNKNOWN-0.0.0 from our pyproject.toml.
echo "Upgrading pip / setuptools / wheel in the local venv..."
"$VENV_PYTHON" -m pip install --quiet --upgrade pip setuptools wheel

if [ "$EDITABLE" -eq 1 ]; then
  "$VENV_PYTHON" -m pip install --no-deps -e "$PROJECT_ROOT"
else
  "$VENV_PYTHON" -m pip install --no-deps "$PROJECT_ROOT"
fi

# --no-scripts: caller asked for a clean install with no PATH-visible commands.
# We can't tell pip to skip individual entry points cleanly, so we install
# normally and then unlink the four console scripts pip just dropped into
# venv/bin. The package is still importable via ``python -m ai_cli_kit``.
if [ "$NO_SCRIPTS" -eq 1 ]; then
  for script in aik cst codex-session-toolkit cc-clean; do
    rm -f "$VENV_DIR/bin/$script"
  done
fi

# chmod the launcher scripts that exist. ``release.sh`` only ships in the
# git source tree (it's excluded from the user-facing release tarball) so
# we skip it when missing rather than failing the install.
for launcher in aik cc-clean codex-session-toolkit codex-session-toolkit.command install.sh install.command release.sh; do
  if [ -f "$PROJECT_ROOT/$launcher" ]; then
    chmod +x "$PROJECT_ROOT/$launcher"
  fi
done

echo ""
echo "============================================="
echo " Install complete."
echo "============================================="
if [ "$NO_SCRIPTS" -eq 1 ]; then
  echo "已按 --no-scripts 模式安装，未在 venv/bin 注册任何命令。"
  echo "推荐运行方式（任选一种，都不会污染系统 PATH）："
  echo "  ./aik                                  # 项目内 launcher"
  echo "  $VENV_PYTHON -m ai_cli_kit             # python -m 直跑"
  echo "  source \"$VENV_DIR/bin/activate\" && python -m ai_cli_kit"
else
  echo "推荐：在项目目录里直接运行 launcher（已自动可执行）"
  echo "  ./aik                                  # 顶层菜单（进 Codex / Claude）"
  echo "  ./codex-session-toolkit"
  echo "  ./cc-clean"
  echo ""
  echo "也可以直接用 python，无需注册 PATH 命令："
  echo "  $VENV_PYTHON -m ai_cli_kit             # 等价于 ./aik"
  echo "  $VENV_PYTHON -m ai_cli_kit.codex"
  echo "  $VENV_PYTHON -m ai_cli_kit.claude"
  echo ""
  echo "若想全局裸命令 'aik'，把 venv bin 加入 PATH 或 source venv："
  echo "  export PATH=\"$VENV_DIR/bin:\$PATH\""
  echo "  source \"$VENV_DIR/bin/activate\""
fi
echo ""
echo "查看版本：./aik --version"
