#!/usr/bin/env bash
# Build a shareable zip of the grammar hook with no private data.
# Output: grammar-hook.zip in the current working directory.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="$(pwd)"
OUT_FILE="$OUT_DIR/grammar-hook.zip"
STAGING="$(mktemp -d)"
trap 'rm -rf "$STAGING"' EXIT

rsync -a \
  --exclude='data' \
  --exclude='.env' --exclude='.env.*' \
  --exclude='__pycache__' --exclude='*.pyc' \
  --exclude='.venv' \
  --exclude='.git' \
  --exclude='grammar-hook.zip' \
  "$SCRIPT_DIR/" "$STAGING/grammar-hook/"

rm -f "$OUT_FILE"
(cd "$STAGING" && zip -qr "$OUT_FILE" grammar-hook)

echo "Wrote $OUT_FILE"
echo ""
echo "Share this zip. Teammate unzips it and runs:"
echo "  cd grammar-hook && bash install.sh"
