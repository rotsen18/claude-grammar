# claude-grammar

A Claude Code hook that silently rewrites your prompts to native-English
grammar before Claude sees them. Built for non-native speakers writing code
prompts daily — typos, wrong tenses, odd word choice get cleaned up in the
background, and a local dashboard shows you what changed.

- Async (never blocks your Claude Code session)
- Runs locally, your prompts never leave the machine except to the corrector
  you choose
- Three correctors: `claude_cli` (uses your Claude Code subscription — no
  extra keys), `groq` (free tier, ~2s), or `languagetool` (rule-based, offline-ish)
- Local dashboard at `http://127.0.0.1:3333` with a live feed of corrections,
  categorized stats, weekly reports, and an EN ↔ UA translation helper

## Install

macOS + [Claude Code](https://claude.com/claude-code) + [uv](https://docs.astral.sh/uv/) required.

```bash
git clone https://github.com/rotsen18/claude-grammar.git
cd claude-grammar
bash install.sh
```

The installer copies the hook into `~/.claude/hooks/grammar/`, installs
Python deps, and registers the `UserPromptSubmit` + `SessionStart` hooks in
`~/.claude/settings.json` (backs up your existing file once).

Restart Claude Code. The dashboard opens in a small Chrome window. That's
the whole setup.

## Daily use

There are only two things you ever need to know:

| Marker | What it does |
|---|---|
| `,,` on its own line | Anything **above** is natural text (grammar-checked); anything **below** is data (code, logs, JSON) and left alone |
| `^^^` at the end of a prompt | Skip grammar checking for this prompt entirely |

Everything else is just the dashboard:

- **Live feed** — every corrected prompt shows up within seconds with a
  red `−` / green `+` diff and a 3-letter category tag (`CAP`, `TNS`, `SPL`, …)
- **Pause button** — click the header when you want to temporarily stop
  rewriting (history and translation stay on). Full toggle in settings.
- **Translation** — top-bar input that translates a word or short phrase
  between English and a configurable second language (Ukrainian, Turkish,
  German, French, Spanish, Italian, Portuguese, Polish, Russian). Auto-detects
  direction by script, caches results. Press `Enter` to look up.
- **Reports** — `/reports` generates weekly summaries of your top categories
  and repeated mistakes. Useful for studying your own patterns.
- **Themes** — 15 accent palettes, from `phosphor` (default) to `pink`,
  `tokyo-night`, `matrix`, `dracula`. Settings modal → Appearance.

## Pausing the hook

Two ways, same effect — Claude sees your prompts unmodified:

- **Dashboard toggle** (persistent): Settings → General → uncheck
  "grammar correction enabled". Or click the `⏸ paused` pill in the header
  to resume.
- **Env variable** (one-shot): `export CLAUDE_GRAMMAR_DISABLED=1` in your
  shell, then launch Claude Code from that shell. Env wins over the toggle.

## Updating

The dashboard checks GitHub once a day for a new release. When one's
available an `update` pill appears in the header — clicking it opens the
release notes and an Install button that downloads, applies, and restarts.
No manual steps needed.

## Troubleshooting

**Nothing showing on the dashboard**

```bash
# Is it running?
curl http://127.0.0.1:3333/health

# Kick it manually:
uv run --project ~/.claude/hooks/grammar ~/.claude/hooks/grammar/server_check.py
```

**Corrections not happening**

Open settings — if "grammar correction enabled" is off, that's why. If it's
on, check the logs page (link in the top right) for errors. Groq-based
corrections need `GROQ_API_KEY` in `~/.claude/hooks/grammar/.env`.

**Still stuck**

Open an issue at https://github.com/rotsen18/claude-grammar/issues with
a screenshot of the logs page and what you typed.

## For developers

Architecture, database schema, settings internals, release flow, and the
"do not regress" invariants live in [CLAUDE.md](./CLAUDE.md). Start there
if you want to contribute or fork.

Version history: [CHANGELOG.md](./CHANGELOG.md).

## License

MIT — do what you want.
