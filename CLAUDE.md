# claude-grammar — CLAUDE.md

Developer guide for the grammar correction hook. The user-facing overview
(install, markers, daily use, troubleshooting) lives in @README.md.

This file captures the architecture, the invariants you must not regress,
and the non-obvious gotchas that aren't visible from reading the code.

## Development guide

The source of truth is this repo. The **installed** copy at
`~/.claude/hooks/grammar/` is whatever `install.sh` last produced —
runtime state (`data/`, `.env`, `.venv/`) lives only there. Two ways to
iterate, depending on what you need to test.

### Recommended: run the dashboard from the repo against a DB snapshot

Point a repo-local dev process at an isolated data directory with
`CLAUDE_GRAMMAR_DATA_DIR` and work against a snapshot of real data.
Nothing the dev process does touches `~/.claude/hooks/grammar/data/`, so
you can break things freely and roll back by deleting `./dev-data/`.

```bash
# stop the installed dashboard to free port 3333
curl -X POST http://127.0.0.1:3333/api/server/shutdown

# one-time: snapshot the prod DB
mkdir -p ./dev-data
cp ~/.claude/hooks/grammar/data/corrections.db ./dev-data/

# run the dashboard from the repo against that copy
CLAUDE_GRAMMAR_DATA_DIR=./dev-data uv run --project . python -m dashboard.app
```

Refresh the snapshot with another `cp` whenever you want newer data. To
return to normal, stop the dev process and re-launch Claude Code — the
SessionStart hook brings the installed dashboard back up.

This covers anything observable through the dashboard — UI, settings,
reports, translations, correctors reached via `/api/…` endpoints. The one
thing it does NOT cover is the hook entry flow from Claude Code itself,
because `~/.claude/settings.json` still points at the installed copy.

### When you actually need to exercise the hook path

For changes to `grammar_fix.py`, `server_check.py`, parser behavior, or
the `UserPromptSubmit` dedupe path, you need to reinstall so Claude Code
invokes your updated code:

```bash
bash install.sh                                         # copy → ~/.claude/hooks/grammar/
curl -X POST http://127.0.0.1:3333/api/server/restart   # reload dashboard
```

Flask caches Jinja templates, so edits to `dashboard/templates/*.html`
always need that restart — browser reload alone won't pick them up.

### Running commands

Everything runs under `uv`. Stay in the repo root — the entry scripts
(`grammar_fix.py`, `server_check.py`) and subpackages (`grammar/`,
`correctors/`, `dashboard/`) assume the project root is on `sys.path`.

```bash
uv sync --project .                                         # install deps
uv run --project . python -m dashboard.app                  # run dashboard manually
uv run --project . python -c "from grammar import storage"  # ad-hoc smoke tests
```

No formal test suite. Verify changes with inline `uv run python -c`
invocations or by hitting the running dashboard's HTTP endpoints.

### Dev-tool noise to ignore

Pyright reports unresolved imports for `config`, `storage`, `settings`,
`flask`, `requests`, etc. — it can't see the uv-managed venv. These are
noise, not bugs; don't try to "fix" them by adding sys.path shims or pip
installs.

## How it works (runtime flow)

```
┌────────────────────────────────────────────────────────────────┐
│  User types prompt in Claude Code                              │
│          │                                                     │
│          ▼                                                     │
│  UserPromptSubmit hook fires (async — does NOT block session)  │
│          │                                                     │
│          ▼                                                     │
│  grammar_fix.py                                                │
│    ├─ kill switch: env + settings.hook_enabled                 │
│    ├─ parser.py:  strip code/logs after `,,` separator         │
│    ├─ dedupe:     skip if same prompt within 60s (atomic)      │
│    ├─ corrector:  groq | claude_cli | languagetool             │
│    └─ storage:    INSERT into SQLite (prompts + corrections)   │
│          │                                                     │
│          ▼                                                     │
│  Dashboard (localhost:3333)                                    │
│    ├─ Bootstrap:  GET  /api/corrections                        │
│    ├─ Livestream: GET  /api/corrections/stream (SSE)           │
│    └─ Stats:      GET  /api/stats                              │
└────────────────────────────────────────────────────────────────┘
```

The hook is registered with `async: true` so Claude Code never waits for
it — corrections appear on the dashboard seconds later, the user never
blocks.

## Layout

```
claude-grammar/
├── grammar_fix.py             # UserPromptSubmit hook entry
├── server_check.py            # SessionStart hook entry; launches dashboard
├── grammar/                   # library package
│   ├── parser.py              # prompt splitting (separator, bypass marker)
│   ├── storage.py             # SQLite layer (single source of truth for settings)
│   ├── config.py              # INITIAL_DEFAULTS + .env loading
│   ├── settings.py            # module-level constants read from DB at import
│   ├── version.py             # installed version reader (pairs with top-level VERSION)
│   ├── updater.py             # GitHub-Releases-backed update check
│   ├── translator.py          # EN↔configurable-language translator
│   └── reports.py             # LLM-written summary reports
├── correctors/
│   ├── base.py
│   ├── groq.py                # default — fast, fallback chain, JSON schema
│   ├── claude_cli.py          # uses local `claude -p` subscription
│   └── languagetool.py        # free, rule-based, no LLM
├── dashboard/
│   ├── app.py                 # Flask
│   └── templates/*.html       # UI (index.html is ≈ 4k lines)
├── scripts/                   # one-off dev tools (corrector comparison, etc.)
├── .github/workflows/         # CI (release.yml — workflow_dispatch)
├── install.sh                 # idempotent installer → ~/.claude/hooks/grammar/
├── get.sh                     # curl-able bootstrap (fetches latest release, runs install.sh)
└── VERSION                    # plain-text version, source of truth for releases
```

## Hard invariants — do not regress

**Prompt-injection hardening.** User text is OPAQUE DATA. Every path that
sends user text to an LLM must:

1. Wrap it in `<text_to_edit>…</text_to_edit>` (correctors) or
   `<text_to_translate>…</text_to_translate>` (translator).
2. Append the hard-coded safety rail to the system prompt *in code*
   (e.g. `SAFETY_APPENDIX` in `correctors/groq.py`). This runs regardless
   of the user's customized `system_prompt` setting — don't move it into
   configurable text where the user could weaken it.
3. Defensively strip the boundary tags from model output
   (`_strip_boundary_tags`) in case the model echoes them.

A "corrected" message that answers the user's question is a bug.

**Settings seeding.** `storage.ensure_settings_seed(INITIAL_DEFAULTS)` only
backfills *missing* keys. Changing an existing default in `config.py` will
NOT update already-installed databases. When a config change must propagate,
write a one-shot migration rather than editing the default in place.

**Settings read timing.** `settings.py` pulls values from the DB at
**import time** into module-level constants. A live settings edit is picked
up only after a process restart — *except* that the hook entry points
(`grammar_fix.py`, `server_check.py`) are spawned fresh for every invocation,
so they always see fresh settings. The long-running dashboard needs the
restart. Exceptions that read fresh on every call even in the dashboard:
`translator._resolve_target_language()`.

If you add a knob users should be able to flip at runtime without a restart,
read it fresh inside the dashboard — don't cache at import.

**Kill switch precedence.** `grammar_fix.py` is the gatekeeper. It must
check in this order: (1) `CLAUDE_GRAMMAR_DISABLED` env var truthy → skip,
(2) `settings.hook_enabled` is False → skip, (3) `BYPASS_MARKER` at end of
prompt → skip for this prompt only. Env always wins.

**Dedupe atomicity.** `storage.insert_prompt_if_not_duplicate` uses
`BEGIN IMMEDIATE` so the duplicate check + insert happen under one exclusive
write lock. Claude Code occasionally fires `UserPromptSubmit` twice for the
same prompt (session replays, retries) — the atomic guard is what prevents
double rows. Don't refactor into check-then-insert; that race is real.

**Paths.** Installed hook always lives at `~/.claude/hooks/grammar/`; data
at `~/.claude/hooks/grammar/data/`. Claude Code invokes the hook entry
points with absolute paths from `~/.claude/settings.json`. Don't refactor
toward relative paths or a pip-installable layout.

## Database schema

```sql
CREATE TABLE prompts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp         TEXT NOT NULL,       -- ISO 8601 UTC
    claude_session_id TEXT NOT NULL DEFAULT '',
    cwd               TEXT NOT NULL DEFAULT '',
    corrector         TEXT NOT NULL,       -- groq | claude_cli | languagetool
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

CREATE TABLE settings (
    key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL
);

CREATE TABLE reports (...);        -- weekly summary reports
CREATE TABLE translations (...);   -- cached EN↔target-lang lookups
```

PRAGMAs: `journal_mode=WAL`, `synchronous=NORMAL`, `foreign_keys=ON`.

### Useful queries for debugging

```sql
-- Top categories of errors (what's the user actually struggling with)
SELECT category, COUNT(*) AS c FROM corrections
WHERE category != '' GROUP BY category ORDER BY c DESC;

-- Repeated misspellings (for a future "N times" badge)
SELECT lower(original), COUNT(*) AS c FROM corrections
WHERE category = 'spelling' GROUP BY lower(original)
HAVING c > 1 ORDER BY c DESC;

-- Daily volume, last 30d
SELECT substr(timestamp, 1, 10) AS day, COUNT(*) FROM prompts
WHERE timestamp > date('now', '-30 days') GROUP BY day ORDER BY day;
```

## Configuration reference

All settings live in the `settings` table, seeded from `INITIAL_DEFAULTS`
in `config.py`. Users edit via the dashboard settings modal or by
POSTing to `/api/settings`. Top-level keys:

| Key | Type | Notes |
|---|---|---|
| `corrector` | enum | `claude_cli` \| `groq` \| `languagetool` |
| `hook_enabled` | bool | Global kill switch, see invariants |
| `min_natural_text_length` | int | Short prompts (e.g. "ok") skipped |
| `separator` | str | Default `,,` — split natural text from data |
| `bypass_marker` | str | Default `^^^` — per-prompt skip |
| `dashboard` | `{host, port}` | Default 127.0.0.1:3333 |
| `update` | `{github_repo, check_interval_hours, auto_check}` | GitHub Releases polling |
| `ui` | `{theme, chat_format, dogs_enabled}` | Dashboard appearance |
| `translation` | `{target_language}` | ISO code; English is always the other side |
| `languagetool` | `{language}` | e.g. `en-US` |
| `claude_cli` | `{model, timeout_seconds, system_prompt}` | `claude -p` |
| `groq` | `{base_url, model, fallback_models[], …, system_prompt}` | Groq API |
| `reports` | `{claude_model, claude_timeout_seconds, keep_latest}` | Weekly reports |

`SETTINGS_WRITABLE_KEYS` in `dashboard/app.py` controls which top-level
keys can be patched over the API. Secrets (`groq.api_key`) are masked in GET
responses and only writable through the separate `/.env` flow.

## Hooks registration (~/.claude/settings.json)

`install.sh` appends these (idempotently — won't duplicate):

```json
{
  "hooks": {
    "SessionStart": [{
      "matcher": "startup|resume",
      "hooks": [{ "type": "command",
        "command": "uv run --project ~/.claude/hooks/grammar ~/.claude/hooks/grammar/server_check.py"
      }]
    }],
    "UserPromptSubmit": [{
      "matcher": "",
      "hooks": [{ "type": "command", "async": true,
        "command": "uv run --project ~/.claude/hooks/grammar ~/.claude/hooks/grammar/grammar_fix.py"
      }]
    }]
  }
}
```

The existing file is backed up once to `settings.json.pre-grammar-hook.bak`
on first install.

## Dashboard internals

- **SSE stream** (`/api/corrections/stream`): polls SQLite every 500 ms for
  new prompts/reports + fans out pending-hook pings. Heartbeats every 15 s
  so the client can detect silent network stalls. Client reconnects on
  `visibilitychange` (laptop wake).
- **Window persistence**: `server_check.py` launches Chrome with
  `--app=<dashboard-url>`. Chrome remembers size/position per-URL, so
  restarts don't snap the window around. First launch uses 1056×321.
- **Pulse light**: green = healthy, orange = reconnecting/stale,
  red = dashboard down. Animation speed encodes recent work, not health.
- **Dog pen**: a playful activity indicator fixed to the top-right.
  Responds to correction volume (zoomies on bursts), session state
  (sleeping when the SSE is down), and pending-hook pings (bark volleys).
- **`pack.ensure(sessionId, cwd)` in `index.html` currently collapses all
  sessions to a single dog (`SOLO_KEY`).** Multi-dog infra exists
  (`MAX_DOGS=4`, `PACK_COLORS`, eviction, proximity greetings) but is gated
  off. Enabling is ~5 lines — ask before flipping.

## Extending

**Add a theme** — three places:

1. `body[data-theme="<name>"] { … }` CSS block in `index.html` — palette.
2. `THEME_DOT_COLORS` map in the JS — two-dot swatch colors.
3. `THEME_OPTIONS` list in `dashboard/app.py`.

**Add a translation language** — append one entry to
`translator.SUPPORTED_TARGET_LANGUAGES`. Settings endpoint and dropdown
pick it up automatically. Direction detection is the ASCII heuristic:
perfect for non-Latin scripts, imperfect for Latin ones where some words
are pure ASCII (e.g. Turkish "bir") — documented in `translator.py`.

**Add a corrector** — subclass `correctors.base.BaseCorrector`, implement
`.correct(text)` → `CorrectionResult`, register in `CORRECTOR_OPTIONS` in
`dashboard/app.py`, and wire dispatch in `grammar_fix.py`. Follow the
injection-hardening pattern from `correctors/groq.py`.

## Releasing

The dashboard update flow reads `/repos/<owner>/<repo>/releases/latest`
from GitHub, so publishing a release is just: bump version + tag.

1. Add changelog entries under `## [Unreleased]` in `CHANGELOG.md` as you
   work. Commit normally.
2. Go to **Actions → Release → Run workflow**, pick `patch`/`minor`/`major`,
   click **Run workflow**.

Step 2 must be done in the browser. The `gh` CLI on this dev machine is
logged in as `taras-radity`, which has read-only access to
`rotsen18/claude-grammar` (`pull: true`, everything else false) — so
`gh workflow run Release` → 403 "admin rights required" and
`gh release edit` → 404. Browser auth uses the `rotsen18` account, which
owns the repo. If you want to polish the auto-generated release notes
after the workflow runs, edit them on the **Releases → Edit release**
page, not via `gh release edit`.

The workflow reads `VERSION`, computes the next version, bumps
`VERSION` + `pyproject.toml`, promotes `## [Unreleased]` to a dated
version header, commits, tags, and creates the GitHub Release — all
running as `github-actions[bot]`, so no local `gh` auth needed.

Installed dashboards pick up the new release on their next poll
(`update.check_interval_hours`, default 1h) — users can force a check
right away via the update pill or `curl /api/update/check?force=1`.

Guardrails: the workflow fails fast if `## [Unreleased]` is empty, or if
`VERSION` isn't strict `X.Y.Z`.

**After every release, pull + rebase before your next commit.** The
workflow pushes a `Release vX.Y.Z` commit and a `vX.Y.Z` tag to `main`
as `github-actions[bot]`, so your local `main` is instantly behind. Any
work-in-progress you had queued must be rebased on top:

```bash
git pull --rebase origin main
```

If you skip this and try to push, git rejects the push as non-fast-forward.
Expect one CHANGELOG merge hazard: the workflow inserts a new
`## [X.Y.Z] — <date>` header directly below `## [Unreleased]`, so any
bullets you had already added under `[Unreleased]` will look like they
belong to the just-cut release after the rebase. Move them back under
`[Unreleased]` before amending, otherwise the next release will skip
them.

## Known rough edges

- The legacy Groq-quota endpoint (`/api/groq/quota`) and its JSON file on
  disk are still populated on every request but no UI consumes them.
  Intentionally kept for ad-hoc queries.
