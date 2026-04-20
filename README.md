# Grammar Correction Hook for Claude Code

An async `UserPromptSubmit` hook that grammar-checks every prompt you submit,
writes the results to SQLite, and displays them live on a compact
terminal-log-style dashboard at `http://127.0.0.1:3333`.

Designed for non-native English speakers who write code-related prompts
daily — it silently fixes grammar/spelling/tense/word-order errors in the
background without blocking the main Claude Code session.

## Prerequisites (macOS)

- **macOS** — installer is Mac-only (AppleScript + Chrome app-window)
- **Claude Code** — https://claude.com/claude-code
- **uv** — `brew install uv` (or `curl -LsSf https://astral.sh/uv/install.sh | sh`)
- **Chrome** (optional) — gives a chromeless sized app-window; falls back to default browser

## Quick start

From a clone of this repo (or its directory on disk):

```bash
bash install.sh
```

The installer:
1. Verifies macOS + uv
2. Copies files to `~/.claude/hooks/grammar/`
3. `uv sync` — installs Python deps
4. Registers `SessionStart` + `UserPromptSubmit` hooks in `~/.claude/settings.json` (backs up the existing file once)
5. Drops a `.env` scaffold — default corrector is `claude_cli` (uses your Claude subscription, no key needed). Paste a `GROQ_API_KEY` only if you want to switch to the faster Groq corrector later.

Then restart Claude Code — the dashboard auto-launches at http://127.0.0.1:3333.

## How it works

```
┌────────────────────────────────────────────────────────────────┐
│  User types prompt in Claude Code                              │
│          │                                                     │
│          ▼                                                     │
│  UserPromptSubmit hook fires (async — does NOT block session)  │
│          │                                                     │
│          ▼                                                     │
│  grammar_fix.py                                                │
│    ├─ parser.py: strip JSON/tracebacks/code/URLs/shell         │
│    ├─ dedupe: skip if same prompt within 60s (atomic)          │
│    ├─ corrector: groq | claude_cli | languagetool              │
│    └─ storage: INSERT into SQLite (prompts + corrections)      │
│          │                                                     │
│          ▼                                                     │
│  Dashboard (localhost:3333)                                    │
│    ├─ Bootstrap: GET /api/corrections                          │
│    ├─ Stream:    GET /api/corrections/stream (SSE polling)     │
│    └─ Stats:     GET /api/stats                                │
└────────────────────────────────────────────────────────────────┘
```

## Correctors

Switch between them by editing `"corrector"` in `settings.json`. No restart.

| Corrector | Latency | Cost | Quality | Notes |
|---|---|---|---|---|
| `claude_cli` *(default)* | ~9s | uses your Claude subscription | highest | Haiku via `claude -p`, runs from `/tmp` to isolate from project context |
| `groq` | ~2s | free tier | high | Primary `llama-3.3-70b-versatile`; auto-falls-back to `llama-4-scout-17b` → `qwen3-32b` on 429. Structured JSON schema, brand-name aware |
| `languagetool` | ~500ms | free public API | mechanical | Rule-based, no LLM; fine for typos, weak on tense/structure |

All three return the same `{corrected_text, changes[]}` shape. Each change has:
`{category, original, replacement, message}` where `category` is one of
`spelling · capitalization · punctuation · tense · agreement · article ·
word_choice · word_order · preposition · contraction · clarity`.

## Special markers in your prompts

| Marker | Effect |
|---|---|
| `,,` on its own line | Everything above is natural language; everything below is data (JSON/logs). Only the top is grammar-checked. |
| `^^^` at the end | Skip grammar checking for this prompt entirely. |

## File layout

```
~/.claude/hooks/grammar/
├── README.md                          # this file
├── pyproject.toml                     # uv project, deps: requests, flask
├── settings.json                      # user-editable config (corrector, keys, prompts)
├── .env                               # GROQ_API_KEY=... (you create this)
│
├── grammar_fix.py                     # UserPromptSubmit entry point
├── server_check.py                    # SessionStart entry point (boots dashboard)
├── config.py                          # Settings loader + env file loader
├── storage.py                         # SQLite layer (prompts, corrections tables)
├── parser.py                          # Prompt parser: separator + technical-line filter
├── migrate_jsonl_to_sqlite.py         # One-shot migration (already run)
│
├── correctors/
│   ├── base.py                        # BaseCorrector, Correction, CorrectionResult
│   ├── groq.py                        # Groq OpenAI-compatible API (default)
│   ├── claude_cli.py                  # `claude -p` subprocess
│   └── languagetool.py                # https://api.languagetool.org/v2/check
│
├── dashboard/
│   ├── app.py                         # Flask, /api/corrections, SSE stream, /api/stats, /api/logs/errors
│   └── templates/
│       ├── index.html                 # Terminal-log-style UI (IBM Plex Mono)
│       ├── logs.html                  # /logs — hook stderr/stdout with copy/download
│       ├── reports_list.html          # /reports — weekly summary index
│       └── reports_detail.html        # /reports/<id> — per-week breakdown
│
├── scripts/
│   └── compare_groq_models.py         # Side-by-side model quality test
│
└── data/                              # Runtime files (don't check in)
    ├── corrections.db                 # SQLite (WAL mode)
    ├── corrections.jsonl.backup.*     # Pre-migration backup
    ├── dashboard.pid                  # Live dashboard process ID
    ├── dashboard.lock                 # fcntl.flock guard against concurrent startup
    ├── dashboard.log                  # Flask stdout/stderr
    └── logs_last_seen_offset          # Byte offset cursor for the error badge
```

## Database schema

```sql
CREATE TABLE prompts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp         TEXT NOT NULL,          -- ISO 8601 UTC
    claude_session_id TEXT NOT NULL DEFAULT '',
    cwd               TEXT NOT NULL DEFAULT '',
    corrector         TEXT NOT NULL,          -- groq | claude_cli | languagetool
    had_separator     INTEGER NOT NULL DEFAULT 0,
    original_prompt   TEXT NOT NULL,
    natural_text      TEXT NOT NULL,
    corrected_text    TEXT NOT NULL
);

CREATE TABLE corrections (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt_id   INTEGER NOT NULL REFERENCES prompts(id) ON DELETE CASCADE,
    seq         INTEGER NOT NULL DEFAULT 0,
    category    TEXT NOT NULL DEFAULT '',
    rule        TEXT NOT NULL DEFAULT '',
    original    TEXT NOT NULL DEFAULT '',
    replacement TEXT NOT NULL DEFAULT '',
    message     TEXT NOT NULL DEFAULT '',
    offset_val  INTEGER,
    length_val  INTEGER
);

CREATE INDEX idx_prompts_timestamp  ON prompts(timestamp);
CREATE INDEX idx_prompts_original   ON prompts(original_prompt, timestamp);
CREATE INDEX idx_corrections_prompt ON corrections(prompt_id);
CREATE INDEX idx_corrections_category ON corrections(category);
```

### Useful queries

```sql
-- Top categories you struggle with
SELECT category, COUNT(*) AS count
FROM corrections
WHERE category != ''
GROUP BY category
ORDER BY count DESC;

-- Words you consistently misspell
SELECT lower(original) AS word, COUNT(*) AS count
FROM corrections
WHERE category = 'spelling'
GROUP BY lower(original)
HAVING COUNT(*) > 1
ORDER BY count DESC;

-- Daily correction counts (last 30 days)
SELECT substr(timestamp, 1, 10) AS day, COUNT(*) AS prompts
FROM prompts
WHERE timestamp > date('now', '-30 days')
GROUP BY day
ORDER BY day;

-- All tense errors with their explanations (for study)
SELECT p.timestamp, c.original, c.replacement, c.message
FROM corrections c
JOIN prompts p ON p.id = c.prompt_id
WHERE c.category = 'tense'
ORDER BY p.timestamp DESC;
```

## Configuration (settings.json)

```json
{
  "corrector": "groq",
  "min_natural_text_length": 10,
  "separator": ",,",
  "bypass_marker": "^^^",

  "dashboard": { "host": "127.0.0.1", "port": 3333 },

  "languagetool": { "language": "en-US" },

  "claude_cli": {
    "model": "haiku",
    "timeout_seconds": 60,
    "system_prompt": "..."
  },

  "groq": {
    "base_url": "https://api.groq.com/openai/v1",
    "model": "llama-3.3-70b-versatile",
    "fallback_models": [
      "meta-llama/llama-4-scout-17b-16e-instruct",
      "qwen/qwen3-32b"
    ],
    "timeout_seconds": 15,
    "api_key_env": "GROQ_API_KEY",
    "api_key": "",
    "temperature": 0.1,
    "use_json_schema": true,
    "system_prompt": "..."
  }
}
```

Settings are loaded at every hook invocation (hooks spawn fresh processes),
so changes take effect on your next prompt — no restart needed.

## Hooks registration (~/.claude/settings.json)

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup",
        "hooks": [{
          "type": "command",
          "command": "uv run --project ~/.claude/hooks/grammar ~/.claude/hooks/grammar/server_check.py"
        }]
      }
    ],
    "UserPromptSubmit": [
      {
        "matcher": "",
        "hooks": [{
          "type": "command",
          "command": "uv run --project ~/.claude/hooks/grammar ~/.claude/hooks/grammar/grammar_fix.py",
          "async": true
        }]
      }
    ]
  }
}
```

`async: true` means the hook never blocks Claude Code — corrections appear
on the dashboard seconds later, you never wait for them.

## Dashboard

Loads at `http://127.0.0.1:3333`. Features:

- **Compact terminal-log layout** — 32px header, dense cards, IBM Plex Mono
- **Chat-style diff** — red `−` gutter (original), green `+` gutter (corrected),
  subtle color bleed behind each line like `git diff`
- **Categorized changes** — 3-letter codes (`CAP`, `TNS`, `SPL`, `AGR`, `ART`,
  `WCH`, `WRD`, `PRP`, `CTR`, `PNC`, `CLR`, `GRM`) each with a distinct accent
  color, hover any chip for the original → replacement diff + rule explanation
- **Header bar** — active corrector + model pill, `total/today` counter (hover
  for the top category), logs icon with a red dot when new `[ERROR]` entries
  arrive, restart/shutdown controls
- **Live streaming** — EventSource polls SQLite every 500ms, new prompts
  append to the bottom with a slide-in animation, viewport only auto-scrolls
  if you're already near the bottom. Auto-reconnects on laptop wake
  (`visibilitychange`)
- **Live indicator** — small green dot in the header pulses while SSE is
  connected, goes red on disconnect
- **Window persistence** — first launch opens a 1056×321 Chrome app-window;
  Chrome remembers the position per-URL after that, so restarts do not
  snap it back

## Dedupe

Claude Code occasionally fires `UserPromptSubmit` twice for the same prompt
(retries, session events). Dedupe is **atomic inside SQLite**:

```sql
BEGIN IMMEDIATE;
SELECT id FROM prompts WHERE original_prompt = ? AND timestamp > ? LIMIT 1;
-- if duplicate, COMMIT with no insert
-- else, INSERT and COMMIT
```

Tested with 5 concurrent identical hooks → exactly 1 row inserted.

## Troubleshooting

**Dashboard not running**
```bash
curl http://127.0.0.1:3333/health
# If nothing, start it manually:
uv run --project ~/.claude/hooks/grammar ~/.claude/hooks/grammar/server_check.py
```

**Groq says "API key missing"**
```bash
# Check the .env file
cat ~/.claude/hooks/grammar/.env
# Should contain GROQ_API_KEY=gsk_...
# Or export it in your shell (requires Claude Code restart)
```

**Corrections not appearing on dashboard**
```bash
# Check the DB directly
sqlite3 ~/.claude/hooks/grammar/data/corrections.db "SELECT id, corrector, natural_text FROM prompts ORDER BY id DESC LIMIT 5"

# Check the hook is registered
grep -A2 UserPromptSubmit ~/.claude/settings.json

# Check the dashboard log for errors
tail -50 ~/.claude/hooks/grammar/data/dashboard.log
```

**Flask serving stale template after HTML edit**
```bash
# Flask caches Jinja templates; restart the dashboard:
kill $(cat ~/.claude/hooks/grammar/data/dashboard.pid)
uv run --project ~/.claude/hooks/grammar ~/.claude/hooks/grammar/server_check.py
```

## Future ideas

- **Daily lesson generator** — query top categories + most-repeated mistakes,
  ask an LLM to generate a personalized study sheet with rules and examples
- **Category filters in the dashboard** — click a category chip to show only
  those corrections
- **"You've repeated this mistake N times"** badges when the same
  `(category, lower(original))` appears in recent history
- **Export to Anki** — SELECT mistakes with explanations → Anki flashcard CSV
- **Multi-language support** — settings field for LanguageTool language,
  similar prompt swap for the LLM correctors

## Translation (EN ↔ UA)

The top bar has a small input that translates a word or 2–4 words between
English and Ukrainian. Direction is auto-detected by scanning for non-ASCII
characters (all Cyrillic letters live above codepoint 127). Results come back
with a best translation, 2–4 synonyms, and 2–3 example sentences, and are
cached in the `translations` table so repeat lookups are instant.

Uses the same Groq fallback chain as the grammar corrector — no separate key
required.

## Publishing & auto-update

The hook carries its own version (`VERSION` file, `pyproject.toml`) and the
dashboard advertises it at `GET /api/version`. When `update.manifest_url` is
configured, the dashboard polls it once per `update.check_interval_hours`
(default 24h) and shows an "update" pill in the header if a newer version is
available.

Release flow:

```bash
# 1. Bump VERSION + pyproject.toml, edit CHANGELOG.md.
# 2. Build a zip + manifest.json.
bash publish.sh --download-url https://example.com/grammar-hook/

# 3. Upload grammar-hook-<version>.zip + manifest.json to that URL.
# 4. In the dashboard settings, set:
#      update.manifest_url = https://example.com/grammar-hook/manifest.json
```

The manifest is a tiny JSON document ([schema in `updater.py`](./updater.py))
with `version`, `download_url`, optional `changelog_url`, `release_notes`,
and `sha256`. Any static host works — S3, GitHub Releases, Gist, plain
Apache — nothing Claude- or hook-specific.

Teammates upgrade with:

```bash
bash upgrade.sh    # reads manifest_url from the dashboard, downloads + installs
```

See `CHANGELOG.md` for the version history.

## Dependencies

- Python ≥ 3.11
- `uv` for package management (auto-creates venv on first run)
- `requests`, `flask` (declared in `pyproject.toml`)
- For `claude_cli` corrector: the `claude` CLI installed and logged in
- For `groq` corrector: `GROQ_API_KEY` in `.env` or shell env

No global installs needed.
