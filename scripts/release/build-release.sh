#!/usr/bin/env sh
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)"
OUTPUT_DIR="${OUTPUT_DIR:-$PROJECT_ROOT/dist/releases}"
VERSION="${VERSION:-}"
PYTHON_BIN="${PYTHON_BIN:-}"
MANIFEST_FILE="$SCRIPT_DIR/release-manifest.txt"

usage() {
  cat <<'EOF'
Usage: ./release.sh [--output-dir <dir>] [--version <version>] [--python <python-bin>]

Creates a clean release folder plus tar.gz/zip archives that can be handed to
other users for download and local installation.

Options:
  --output-dir <dir>   Override the release output directory
  --version <value>    Override the release version label
  --python <bin>       Use a specific Python executable to read package version
  --help               Show this help text
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --output-dir)
      if [ "$#" -lt 2 ]; then
        echo "Error: --output-dir requires a value." >&2
        exit 2
      fi
      OUTPUT_DIR="$2"
      shift 2
      ;;
    --version)
      if [ "$#" -lt 2 ]; then
        echo "Error: --version requires a value." >&2
        exit 2
      fi
      VERSION="$2"
      shift 2
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

if [ -z "$VERSION" ]; then
  VERSION="$(
    PYTHONPATH="$PROJECT_ROOT/src${PYTHONPATH:+:$PYTHONPATH}" \
      "$PYTHON_BIN" -c 'from codex_session_cloner import __version__; print(__version__)'
  )"
fi

ARCHIVE_ROOT="codex-session-cloner-${VERSION}"
STAGE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/codex-session-cloner-release.XXXXXX")"
RELEASE_DIR="$STAGE_DIR/$ARCHIVE_ROOT"

cleanup() {
  rm -rf "$STAGE_DIR"
}
trap cleanup EXIT INT TERM

mkdir -p "$OUTPUT_DIR" "$RELEASE_DIR"

if [ ! -f "$MANIFEST_FILE" ]; then
  echo "Error: release manifest not found at $MANIFEST_FILE" >&2
  exit 1
fi

copy_path() {
  src_path="$1"
  dest_path="$RELEASE_DIR/$1"
  if [ ! -e "$PROJECT_ROOT/$src_path" ]; then
    echo "Error: manifest entry does not exist: $src_path" >&2
    exit 1
  fi
  if [ -d "$PROJECT_ROOT/$src_path" ]; then
    mkdir -p "$(dirname "$dest_path")"
    cp -R "$PROJECT_ROOT/$src_path" "$dest_path"
  else
    mkdir -p "$(dirname "$dest_path")"
    cp "$PROJECT_ROOT/$src_path" "$dest_path"
  fi
}

while IFS= read -r manifest_path || [ -n "$manifest_path" ]; do
  case "$manifest_path" in
    ""|\#*)
      continue
      ;;
  esac
  copy_path "$manifest_path"
done < "$MANIFEST_FILE"

find "$RELEASE_DIR" -type d \( -name "__pycache__" -o -name "*.egg-info" \) -prune -exec rm -rf {} \;
find "$RELEASE_DIR" -type f \( -name "*.pyc" -o -name "*.pyo" -o -name ".DS_Store" \) -delete

chmod +x \
  "$RELEASE_DIR/codex-session-cloner" \
  "$RELEASE_DIR/codex-session-cloner.command" \
  "$RELEASE_DIR/install.sh" \
  "$RELEASE_DIR/install.command"

tar -czf "$OUTPUT_DIR/$ARCHIVE_ROOT.tar.gz" -C "$STAGE_DIR" "$ARCHIVE_ROOT"

if command -v zip >/dev/null 2>&1; then
  (
    cd "$STAGE_DIR"
    zip -qr "$OUTPUT_DIR/$ARCHIVE_ROOT.zip" "$ARCHIVE_ROOT"
  )
fi

rm -rf "$OUTPUT_DIR/$ARCHIVE_ROOT"
cp -R "$RELEASE_DIR" "$OUTPUT_DIR/$ARCHIVE_ROOT"

echo "============================================="
echo " Codex Session Cloner - Release Builder"
echo "============================================="
echo "Version:     $VERSION"
echo "Output dir:  $OUTPUT_DIR"
echo "Manifest:    $MANIFEST_FILE"
echo "Folder:      $OUTPUT_DIR/$ARCHIVE_ROOT"
echo "Tarball:     $OUTPUT_DIR/$ARCHIVE_ROOT.tar.gz"
if [ -f "$OUTPUT_DIR/$ARCHIVE_ROOT.zip" ]; then
  echo "Zip:         $OUTPUT_DIR/$ARCHIVE_ROOT.zip"
fi
