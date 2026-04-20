# claude-grammar — CLAUDE.md

Development repo for the grammar correction hook for Claude Code.

See @README.md for the user-facing overview: what this does, install flow,
markers (`,,` and `^^^`), correctors, dashboard, and troubleshooting.

This file is the **developer** companion — it covers the invariants and
gotchas that aren't obvious from reading the code.

## Dev vs. installed

This repo at `~/Repos/personal/claude-grammar/` is the source of truth.
The *installed* location is `~/.claude/hooks/grammar/` — a subset of this
tree copied there by `install.sh`, with runtime data (`data/`), `.env`,
and `.venv/` living on that side only.

Typical dev loop:

```bash
# make changes here (this repo)
bash install.sh                      # copy to ~/.claude/hooks/grammar/, install deps
curl -X POST http://127.0.0.1:3333/api/server/restart   # reload dashboard
```

Flask caches Jinja templates, so edits to `dashboard/templates/index.html`
need that restart — browser reload alone won't pick them up.

## Layout

```
claude-grammar/
├── grammar_fix.py       # UserPromptSubmit hook entry
├── server_check.py      # SessionStart hook entry; launches dashboard
├── parser.py            # prompt splitting (separator, bypass marker)
├── storage.py           # SQLite layer (single source of truth for settings)
├── config.py            # INITIAL_DEFAULTS + .env loading
├── settings.py          # module-level constants read from DB at import
├── version.py + VERSION # installed version (single source of truth)
├── updater.py           # remote manifest-based update check
├── translator.py        # EN↔configurable-language translator
├── reports.py           # LLM-written summary reports
├── correctors/
│   ├── base.py
│   ├── groq.py          # default — fast, fallback chain, JSON schema
│   ├── claude_cli.py    # uses local `claude -p` subscription
│   └── languagetool.py  # free, rule-based, no LLM
├── dashboard/
│   ├── app.py           # Flask
│   └── templates/*.html # UI (index.html is the big one ≈ 4k lines)
├── scripts/             # one-off dev tools (corrector comparison, etc.)
├── install.sh           # idempotent installer → ~/.claude/hooks/grammar/
├── publish.sh           # builds zip + manifest.json for distribution
└── upgrade.sh           # client-side upgrade runner (reads manifest_url)
```

## Commands

Everything runs under `uv`. Stay in the repo root — `cd`-ing into subdirs
breaks `from config import ...` style imports.

```bash
uv sync --project .                                    # install deps
uv run --project . python -m dashboard.app             # run dashboard manually
uv run --project . python -c "import storage; ..."     # ad-hoc smoke tests
curl -X POST http://127.0.0.1:3333/api/server/restart  # reload after code edits
```

No formal test suite. Verify changes with inline `uv run python -c`
invocations or by hitting the running dashboard's HTTP endpoints.

## Hard invariants — do not regress

**Prompt-injection hardening.** User text is OPAQUE DATA. Every path that
sends user text to an LLM must:

1. Wrap it in `<text_to_edit>…</text_to_edit>` (correctors) or
   `<text_to_translate>…</text_to_translate>` (translator).
2. Append the hard-coded safety rail to the system prompt in code
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
up only after a process restart. Exceptions that read fresh on every call:
`translator._resolve_target_language()`. If you add a knob users should be
able to flip at runtime, read it fresh — don't cache at import.

**Paths.** Installed hook always lives at `~/.claude/hooks/grammar/`; data
at `~/.claude/hooks/grammar/data/`. Claude Code invokes the hook entry
points with absolute paths from `~/.claude/settings.json`. Don't refactor
toward relative paths or a pip-installable layout.

## Extending

**Add a theme** — three places, all in `dashboard/templates/index.html`
+ one in `dashboard/app.py`:

1. CSS block `body[data-theme="<name>"] { … }` — variable palette.
2. `THEME_DOT_COLORS` map in the JS — two-dot swatch colors.
3. `THEME_OPTIONS` list in `dashboard/app.py`.

**Add a translation language** — append one entry to
`translator.SUPPORTED_TARGET_LANGUAGES`. Settings endpoint and dropdown
pick it up automatically. Direction detection is the ASCII heuristic:
perfect for non-Latin scripts, imperfect for Latin ones (documented in
`translator.py`).

**Add a corrector** — subclass `correctors.base.BaseCorrector`, implement
`.correct(text)` → `CorrectionResult`, register in `CORRECTOR_OPTIONS` in
`dashboard/app.py`, and wire dispatch in `grammar_fix.py`. Follow the
injection-hardening pattern from `correctors/groq.py`.

## Releasing

1. Bump `VERSION` (and `pyproject.toml version` to match).
2. Add an entry to `CHANGELOG.md`.
3. `bash publish.sh --download-url <where-youll-host-it>` — produces the
   zip and `manifest.json`.
4. Upload both to the host; point `update.manifest_url` at the manifest URL
   (via the dashboard settings or by editing `data/corrections.db`).
5. Teammates run `bash upgrade.sh`.

## Git identity for this repo

Local config only — global git identity is untouched:
- `user.email` → personal noreply (`102423887+rotsen18@users.noreply.github.com`)
- `user.name` → `Taras Nester`
- `core.sshcommand` → pins pushes to `~/.ssh/github_personal`

If you clone this repo fresh on another machine, re-apply these three
`git config --local` settings. Don't commit machine-specific paths.

## Known rough edges

- Pyright reports unresolved imports for `config`, `storage`, `settings`,
  `flask`, etc. — pyright can't see the uv-managed venv. Noise, not bugs.
- `pack.ensure(sessionId, cwd)` in `index.html` currently collapses all
  sessions to a single dog (`SOLO_KEY`). Multi-dog infra exists
  (`MAX_DOGS=4`, `PACK_COLORS`, eviction, proximity greetings) but is
  gated off. Enabling it is ~5 lines — ask before flipping.
- The legacy Groq-quota endpoint (`/api/groq/quota`) and its JSON file on
  disk are still populated on every request but no UI consumes them.
  Intentionally kept for ad-hoc queries.
