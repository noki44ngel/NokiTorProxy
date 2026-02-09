#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
PYTHON_BIN="${PYTHON_BIN:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  PYTHON_BIN=python
fi
SOURCE_FILE="${SOURCE_FILE:-nokiTOR.py}"
if [[ ! -f "$SOURCE_FILE" ]]; then
  echo "Source file not found: $SOURCE_FILE" >&2
  exit 1
fi
mkdir -p build
"$PYTHON_BIN" -m nuitka \
  --standalone \
  --onefile \
  --remove-output \
  --output-dir=build \
  --output-filename=torproxy \
  "$SOURCE_FILE"

echo "Built: build/torproxy"
