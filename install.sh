#!/usr/bin/env bash
# Grammar hook installer for Claude Code on macOS.
# Usage:
#   bash install.sh            # clone-side install from the repo dir
#   curl -fsSL <url>/install.sh | bash   # remote install (if hosted)

set -euo pipefail

HOOK_DIR="$HOME/.claude/hooks/grammar"
SETTINGS_FILE="$HOME/.claude/settings.json"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
DIM='\033[2m'
NC='\033[0m'

ok()   { printf "${GREEN}✓${NC} %s\n" "$1"; }
warn() { printf "${YELLOW}!${NC} %s\n" "$1"; }
err()  { printf "${RED}✗${NC} %s\n" "$1"; }
info() { printf "${DIM}»${NC} %s\n" "$1"; }

# 1. Platform check
if [[ "$(uname)" != "Darwin" ]]; then
  err "This installer is macOS-only."
  exit 1
fi
ok "macOS detected"

# 2. uv check
if ! command -v uv &>/dev/null; then
  err "uv is not installed."
  echo ""
  echo "  Install with:  brew install uv"
  echo "  Or:            curl -LsSf https://astral.sh/uv/install.sh | sh"
  echo ""
  exit 1
fi
ok "uv found: $(uv --version 2>&1 | head -1)"

# 3. Claude Code check
if ! command -v claude &>/dev/null; then
  warn "claude CLI not found — Claude Code itself must be installed for hooks to run."
  warn "  Install from https://claude.com/claude-code"
else
  ok "claude CLI found"
fi

# 4. Copy files
mkdir -p "$HOOK_DIR"
if [[ "$SCRIPT_DIR" != "$HOOK_DIR" ]]; then
  info "Copying hook files to $HOOK_DIR"
  rsync -a \
    --exclude='data' \
    --exclude='.env' --exclude='.env.*' \
    --exclude='__pycache__' --exclude='*.pyc' \
    --exclude='.venv' \
    --exclude='.git' \
    "$SCRIPT_DIR/" "$HOOK_DIR/"
  ok "Files copied"
else
  ok "Running from $HOOK_DIR (no copy needed)"
fi
chmod +x "$HOOK_DIR/server_check.py" "$HOOK_DIR/grammar_fix.py" 2>/dev/null || true

# 5. Install Python deps
info "Installing Python dependencies via uv…"
(cd "$HOOK_DIR" && uv sync --quiet)
ok "Dependencies installed"

# 6. Register hooks in ~/.claude/settings.json
mkdir -p "$(dirname "$SETTINGS_FILE")"
if [[ ! -f "$SETTINGS_FILE" ]]; then
  echo '{}' > "$SETTINGS_FILE"
fi

python3 <<PYEOF
import json
from pathlib import Path

settings_path = Path("$SETTINGS_FILE")
data = json.loads(settings_path.read_text() or "{}")
data.setdefault("hooks", {})

session_start_cmd = "uv run --project ~/.claude/hooks/grammar ~/.claude/hooks/grammar/server_check.py"
user_prompt_cmd   = "uv run --project ~/.claude/hooks/grammar ~/.claude/hooks/grammar/grammar_fix.py"

def has_hook(section, command):
    for entry in data["hooks"].get(section, []):
        for hook in entry.get("hooks", []):
            if hook.get("command") == command:
                return True
    return False

changed = False

if not has_hook("SessionStart", session_start_cmd):
    data["hooks"].setdefault("SessionStart", []).append({
        "matcher": "startup|resume",
        "hooks": [{"type": "command", "command": session_start_cmd}],
    })
    changed = True

if not has_hook("UserPromptSubmit", user_prompt_cmd):
    data["hooks"].setdefault("UserPromptSubmit", []).append({
        "matcher": "",
        "hooks": [{"type": "command", "command": user_prompt_cmd, "async": True}],
    })
    changed = True

if changed:
    backup = settings_path.with_suffix(".json.pre-grammar-hook.bak")
    if not backup.exists():
        backup.write_text(settings_path.read_text())
    settings_path.write_text(json.dumps(data, indent=2) + "\n")
    print("REGISTERED")
else:
    print("ALREADY_REGISTERED")
PYEOF

# 7. .env scaffold
ENV_FILE="$HOOK_DIR/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  cat > "$ENV_FILE" <<'ENVEOF'
# Grammar hook environment variables
#
# Default corrector is `claude_cli` — uses your existing Claude Code subscription,
# no API key needed. Just leave this file as-is.
#
# Optional: switch to `groq` in the dashboard settings for ~5× faster corrections.
# Get a key at https://console.groq.com/keys and paste it below.
GROQ_API_KEY=
ENVEOF
  ok ".env scaffold created at $ENV_FILE"
  warn "Add your GROQ_API_KEY to $ENV_FILE (or switch corrector to claude_cli in the dashboard)."
else
  ok ".env already exists (kept as-is)"
fi

# 8. Smoke test
if (cd "$HOOK_DIR" && uv run --quiet python -c "import storage, hook_log; hook_log.get_logger(); storage.init_db()" 2>&1); then
  ok "Smoke test passed"
else
  err "Smoke test failed — check output above."
  exit 1
fi

echo ""
ok "Grammar hook installed."
echo ""
echo "  ${DIM}Next steps:${NC}"
echo "    1. (optional) Add GROQ_API_KEY to $ENV_FILE"
echo "    2. Start (or restart) Claude Code — dashboard auto-launches at http://127.0.0.1:3333"
echo "    3. See README.md for markers (,, and ^^^) and dashboard features"
echo ""
