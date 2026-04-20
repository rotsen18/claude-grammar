#!/usr/bin/env bash
# claude-grammar bootstrapper.
#
# Downloads the latest GitHub Release and runs install.sh from it. Intended
# to be fetched + executed in one step:
#
#   curl -fsSL https://raw.githubusercontent.com/rotsen18/claude-grammar/main/get.sh -o get.sh && bash get.sh
#
# Or piped directly (less auditable — use the two-step form if you want to
# read the script first):
#
#   curl -fsSL https://raw.githubusercontent.com/rotsen18/claude-grammar/main/get.sh | bash
#
# Environment overrides:
#   CLAUDE_GRAMMAR_REPO  — "owner/repo" to install from. Default: rotsen18/claude-grammar
#   CLAUDE_GRAMMAR_TAG   — specific tag to install (e.g. v0.3.0). Default: latest.

set -euo pipefail

REPO="${CLAUDE_GRAMMAR_REPO:-rotsen18/claude-grammar}"
PINNED_TAG="${CLAUDE_GRAMMAR_TAG:-}"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
DIM='\033[2m'
NC='\033[0m'

ok()   { printf "${GREEN}✓${NC} %s\n" "$1"; }
warn() { printf "${YELLOW}!${NC} %s\n" "$1"; }
err()  { printf "${RED}✗${NC} %s\n" "$1" >&2; }
info() { printf "${DIM}»${NC} %s\n" "$1"; }

# 1. Platform + tooling sanity checks up front — install.sh will re-check,
# but failing fast here gives a clearer error message.
if [[ "$(uname)" != "Darwin" ]]; then
  err "This installer is macOS-only."
  exit 1
fi
for tool in curl unzip python3; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    err "'$tool' is required but not found on PATH."
    exit 1
  fi
done

# 2. Resolve which tag to fetch. Pinned tag skips the API call entirely.
if [[ -n "$PINNED_TAG" ]]; then
  TAG="$PINNED_TAG"
  ok "Using pinned tag: $TAG"
else
  info "Fetching latest release for $REPO"
  RELEASE_JSON="$(curl -fsSL -H 'Accept: application/vnd.github+json' "https://api.github.com/repos/$REPO/releases/latest" 2>/dev/null || true)"
  if [[ -z "$RELEASE_JSON" ]]; then
    err "Could not reach api.github.com. Check your network, and that $REPO exists and is public."
    exit 1
  fi
  TAG="$(printf '%s' "$RELEASE_JSON" | python3 -c 'import json,sys;print((json.load(sys.stdin) or {}).get("tag_name",""))')"
  if [[ -z "$TAG" ]]; then
    err "No releases published for $REPO yet. Set CLAUDE_GRAMMAR_TAG=v<version> to pin, or ask the maintainer to cut a release."
    exit 1
  fi
  ok "Latest release: $TAG"
fi

ZIP_URL="https://github.com/$REPO/archive/refs/tags/$TAG.zip"

# 3. Download + unpack into a throwaway temp dir.
TMP="$(mktemp -d -t claude-grammar-install.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT

info "Downloading $ZIP_URL"
if ! curl -fsSL "$ZIP_URL" -o "$TMP/src.zip"; then
  err "Download failed. If the tag exists but the archive is missing, GitHub may still be generating it — try again in a moment."
  exit 1
fi

info "Extracting"
(cd "$TMP" && unzip -q src.zip)

# GitHub tag archives contain a single top-level directory, e.g. claude-grammar-0.3.0.
SRC_DIR="$(find "$TMP" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
if [[ -z "$SRC_DIR" || ! -f "$SRC_DIR/install.sh" ]]; then
  err "install.sh not found in the downloaded archive (expected inside $SRC_DIR)."
  exit 1
fi
ok "Unpacked to $SRC_DIR"

# 4. Hand off to the real installer. install.sh handles everything else —
# copying files into ~/.claude/hooks/grammar/, running uv sync, registering
# hooks in ~/.claude/settings.json, etc.
info "Running installer"
bash "$SRC_DIR/install.sh"
