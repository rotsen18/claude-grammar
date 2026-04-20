#!/usr/bin/env bash
# One-shot upgrader. Reads update.manifest_url from the live dashboard
# (http://127.0.0.1:3333/api/update/check), downloads the advertised zip,
# unpacks it into a temp dir, and runs install.sh from there. User data
# (data/, .env) is preserved because install.sh's rsync excludes them.
#
# Usage:
#   bash upgrade.sh                     # download + install latest
#   bash upgrade.sh --manifest <url>    # override the manifest URL
#   bash upgrade.sh --zip <path>        # skip download, install a local zip

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'
ok()   { printf "${GREEN}✓${NC} %s\n" "$1"; }
warn() { printf "${YELLOW}!${NC} %s\n" "$1"; }
err()  { printf "${RED}✗${NC} %s\n" "$1"; }

MANIFEST_URL=""
LOCAL_ZIP=""
DASHBOARD_URL="${GRAMMAR_DASHBOARD_URL:-http://127.0.0.1:3333}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --manifest) MANIFEST_URL="$2"; shift 2 ;;
    --zip)      LOCAL_ZIP="$2";   shift 2 ;;
    --help|-h)
      grep '^#' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "Unknown flag: $1" >&2; exit 2 ;;
  esac
done

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

ZIP_PATH=""
if [[ -n "$LOCAL_ZIP" ]]; then
  ZIP_PATH="$LOCAL_ZIP"
  ok "Using local zip: $ZIP_PATH"
else
  # 1. Discover the manifest URL. Prefer explicit flag, then ask the dashboard.
  if [[ -z "$MANIFEST_URL" ]]; then
    if ! curl -fsS "$DASHBOARD_URL/api/update/check?force=1" > "$TMP/update.json"; then
      err "Could not reach dashboard at $DASHBOARD_URL."
      err "Start the dashboard, or re-run with --manifest <url>."
      exit 1
    fi
    MANIFEST_URL="$(python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));print(d.get("manifest",{}).get("download_url",""))' "$TMP/update.json")"
    if [[ -z "$MANIFEST_URL" ]]; then
      err "No download_url in manifest. Check your update.manifest_url setting."
      exit 1
    fi
  fi

  # 2. Download.
  ok "Downloading $MANIFEST_URL"
  if ! curl -fsSL "$MANIFEST_URL" -o "$TMP/release.zip"; then
    err "Download failed."
    exit 1
  fi
  ZIP_PATH="$TMP/release.zip"
fi

# 3. Verify it's a zip before unpacking.
if ! unzip -tq "$ZIP_PATH" >/dev/null 2>&1; then
  err "Downloaded file is not a valid zip. Aborting."
  exit 1
fi

# 4. Unpack and invoke install.sh from the new tree.
(cd "$TMP" && unzip -q "$ZIP_PATH")
NEW_ROOT="$(find "$TMP" -maxdepth 2 -type d -name 'grammar-hook*' | head -n 1)"
if [[ -z "$NEW_ROOT" || ! -f "$NEW_ROOT/install.sh" ]]; then
  err "Couldn't find install.sh inside the downloaded zip."
  exit 1
fi

ok "Running installer from $NEW_ROOT"
bash "$NEW_ROOT/install.sh"
ok "Upgrade complete."

# 5. Ask the dashboard to restart so the new code loads.
if curl -fsS -X POST "$DASHBOARD_URL/api/server/restart" >/dev/null 2>&1; then
  ok "Dashboard restarting…"
else
  warn "Could not reach dashboard to restart. Start Claude Code or run server_check.py."
fi
