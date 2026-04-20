#!/usr/bin/env bash
# Cut a GitHub Release for the version in VERSION. The update flow in the
# dashboard uses /repos/<owner>/<repo>/releases/latest — nothing else needed
# on the hosting side.
#
# Usage:
#   bash release.sh                    # auto-read version from VERSION
#   bash release.sh --dry-run          # show what would happen
#   bash release.sh --skip-push-commit # don't auto-push main before tagging
#
# Preconditions:
#   - `gh` CLI authenticated on the account that owns the repo.
#     Check with:  gh auth status
#     If it's not your personal account:  gh auth login --hostname github.com
#   - Working tree is clean and on the branch you want to tag from (usually main).
#   - VERSION file already bumped, CHANGELOG.md already updated for this version.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; DIM='\033[2m'; NC='\033[0m'
ok()   { printf "${GREEN}✓${NC} %s\n" "$1"; }
warn() { printf "${YELLOW}!${NC} %s\n" "$1"; }
err()  { printf "${RED}✗${NC} %s\n" "$1"; }
info() { printf "${DIM}»${NC} %s\n" "$1"; }

DRY_RUN=0
SKIP_PUSH_COMMIT=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)           DRY_RUN=1; shift ;;
    --skip-push-commit)  SKIP_PUSH_COMMIT=1; shift ;;
    --help|-h)
      grep '^#' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) err "Unknown flag: $1"; exit 2 ;;
  esac
done

run() {
  if [[ $DRY_RUN -eq 1 ]]; then
    printf "${DIM}[dry-run]${NC} %s\n" "$*"
  else
    "$@"
  fi
}

# 1. Preconditions
if ! command -v gh >/dev/null 2>&1; then
  err "gh CLI not installed. brew install gh"
  exit 1
fi
if ! gh auth status >/dev/null 2>&1; then
  err "gh is not authenticated. Run: gh auth login --hostname github.com"
  exit 1
fi

VERSION="$(tr -d '[:space:]' < VERSION)"
if [[ -z "$VERSION" ]]; then
  err "VERSION file is empty"
  exit 1
fi
TAG="v$VERSION"
ok "Preparing release $TAG"

# Reject pre-release formats that semver-tuple-compare unexpectedly.
if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  warn "VERSION '$VERSION' is not strict semver — proceeding anyway"
fi

# Make sure pyproject.toml matches.
PY_VERSION="$(grep -E '^version\s*=' pyproject.toml | head -1 | sed 's/.*"\(.*\)".*/\1/')"
if [[ "$PY_VERSION" != "$VERSION" ]]; then
  err "VERSION ($VERSION) and pyproject.toml ($PY_VERSION) disagree"
  exit 1
fi
ok "VERSION + pyproject.toml agree: $VERSION"

# 2. Dirty tree guard
if [[ -n "$(git status --porcelain)" ]]; then
  err "Working tree has uncommitted changes. Commit or stash first."
  git status --short
  exit 1
fi

# 3. Tag already exists?
if git rev-parse "$TAG" >/dev/null 2>&1; then
  err "Tag $TAG already exists locally. Delete it or bump VERSION."
  exit 1
fi
if gh api "repos/:owner/:repo/git/refs/tags/$TAG" >/dev/null 2>&1; then
  err "Tag $TAG already exists on the remote."
  exit 1
fi

# 4. Push main first so the tag points at something the remote knows.
if [[ $SKIP_PUSH_COMMIT -eq 0 ]]; then
  if ! git diff --quiet "@{upstream}"..HEAD 2>/dev/null; then
    info "Pushing main to origin first"
    run git push
  else
    ok "Origin is already up-to-date"
  fi
fi

# 5. Extract release notes from CHANGELOG.md (the section for this version).
NOTES_FILE="$(mktemp -t claude-grammar-release.XXXXXX)"
trap 'rm -f "$NOTES_FILE"' EXIT
awk -v ver="$VERSION" '
  /^## / {
    if (found) exit
    if ($0 ~ "\\[" ver "\\]") { found = 1; next }
  }
  found { print }
' CHANGELOG.md > "$NOTES_FILE" || true

if [[ ! -s "$NOTES_FILE" ]]; then
  warn "CHANGELOG.md has no '## [$VERSION]' section — falling back to --generate-notes"
  NOTES_ARG="--generate-notes"
else
  ok "Using CHANGELOG.md section for $VERSION as release notes"
  NOTES_ARG="--notes-file $NOTES_FILE"
fi

# 6. Tag + push tag + create release
info "Creating annotated tag $TAG"
run git tag -a "$TAG" -m "Release $TAG"
info "Pushing tag to origin"
run git push origin "$TAG"

info "Creating GitHub Release"
if [[ $DRY_RUN -eq 1 ]]; then
  printf "${DIM}[dry-run]${NC} gh release create %s --title %s %s\n" "$TAG" "$TAG" "$NOTES_ARG"
else
  # shellcheck disable=SC2086
  gh release create "$TAG" --title "$TAG" $NOTES_ARG
fi

ok "Release $TAG published."
echo ""
echo "The dashboard checks for updates every ~24h; users can force a check"
echo "by clicking the update pill (appears automatically when a newer tag is"
echo "published) or via:"
echo ""
echo "    curl -fsS 'http://127.0.0.1:3333/api/update/check?force=1'"
