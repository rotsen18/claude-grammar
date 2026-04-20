#!/usr/bin/env bash
# Build a releaseable zip + a manifest.json that the dashboard update checker
# can consume. Output:
#   grammar-hook-<version>.zip   — the archive clients will download
#   manifest.json                — upload this next to the zip
#
# Usage:
#   bash publish.sh                              # auto-reads VERSION
#   bash publish.sh --download-url https://…/    # base URL for the zip
#   bash publish.sh --changelog-url https://…/CHANGELOG.md
#
# Typical flow:
#   1. Bump VERSION + pyproject.toml `version`, update CHANGELOG.md.
#   2. bash publish.sh --download-url https://example.com/grammar-hook/
#   3. Upload grammar-hook-<version>.zip + manifest.json to that URL.
#   4. Set update.manifest_url in the dashboard settings (or edit
#      data/corrections.db) to https://example.com/grammar-hook/manifest.json.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="$(pwd)"

DOWNLOAD_URL=""
CHANGELOG_URL=""
RELEASE_NOTES=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --download-url)   DOWNLOAD_URL="$2"; shift 2 ;;
    --changelog-url)  CHANGELOG_URL="$2"; shift 2 ;;
    --release-notes)  RELEASE_NOTES="$2"; shift 2 ;;
    --help|-h)
      grep '^#' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "Unknown flag: $1" >&2; exit 2 ;;
  esac
done

VERSION="$(cat "$SCRIPT_DIR/VERSION" | tr -d '[:space:]')"
if [[ -z "$VERSION" ]]; then
  echo "VERSION file is empty or missing" >&2
  exit 1
fi

ZIP_NAME="grammar-hook-$VERSION.zip"
ZIP_PATH="$OUT_DIR/$ZIP_NAME"
MANIFEST_PATH="$OUT_DIR/manifest.json"

STAGING="$(mktemp -d)"
trap 'rm -rf "$STAGING"' EXIT

rsync -a \
  --exclude='data' \
  --exclude='.env' --exclude='.env.*' \
  --exclude='__pycache__' --exclude='*.pyc' \
  --exclude='.venv' \
  --exclude='.git' \
  --exclude='grammar-hook*.zip' \
  --exclude='manifest.json' \
  "$SCRIPT_DIR/" "$STAGING/grammar-hook/"

rm -f "$ZIP_PATH"
(cd "$STAGING" && zip -qr "$ZIP_PATH" grammar-hook)

SHA256=""
if command -v shasum >/dev/null 2>&1; then
  SHA256="$(shasum -a 256 "$ZIP_PATH" | awk '{print $1}')"
elif command -v sha256sum >/dev/null 2>&1; then
  SHA256="$(sha256sum "$ZIP_PATH" | awk '{print $1}')"
fi

RELEASED_AT="$(date -u +%Y-%m-%d)"

# Trim trailing slash on download URL so we control joining.
DOWNLOAD_URL="${DOWNLOAD_URL%/}"
FULL_DOWNLOAD="${DOWNLOAD_URL:+$DOWNLOAD_URL/$ZIP_NAME}"

python3 - "$VERSION" "$FULL_DOWNLOAD" "$CHANGELOG_URL" "$RELEASE_NOTES" "$SHA256" "$RELEASED_AT" > "$MANIFEST_PATH" <<'PYEOF'
import json, sys
version, download, changelog, notes, sha256, released = sys.argv[1:7]
manifest = {
    "version": version,
    "released_at": released,
    "minimum_python": "3.11",
}
if download:  manifest["download_url"]  = download
if changelog: manifest["changelog_url"] = changelog
if notes:     manifest["release_notes"] = notes
if sha256:    manifest["sha256"]        = sha256
print(json.dumps(manifest, indent=2))
PYEOF

echo "Wrote $ZIP_PATH"
echo "Wrote $MANIFEST_PATH"
echo ""
echo "Next steps:"
echo "  1. Upload $ZIP_NAME and manifest.json to your host (e.g. GitHub release or S3)."
echo "  2. Share the manifest URL with teammates and have them set it in the dashboard:"
echo "     Settings → update.manifest_url"
