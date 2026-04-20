# Changelog

All notable changes to the grammar hook are documented here. This project
follows [semantic versioning](https://semver.org/): `MAJOR.MINOR.PATCH`.

- **MAJOR** — breaking changes (config format, schema, hook entry points).
- **MINOR** — new features, backwards compatible.
- **PATCH** — bug fixes and small polish.

## [Unreleased]

### Added
- Installed version now shown in the dashboard header next to the brand
  (e.g. `▚ GRAMMAR v0.3.5`). Previously only available as a hover tooltip.

## [0.3.5] — 2026-04-20

### Changed
- Project layout: all library modules (`config`, `storage`, `settings`,
  `hook_log`, `parser`, `reports`, `translator`, `updater`, `version`)
  moved under a `grammar/` package. Entry scripts (`grammar_fix.py`,
  `server_check.py`) stay at the root so `~/.claude/settings.json`
  hook commands don't change. Imports updated accordingly; no runtime
  behavior change.
- `release.sh` removed — superseded by the `Release` GitHub Actions
  workflow (Actions → Release → Run workflow).

## [0.3.4] — 2026-04-20

### Changed
- Internal: releases now cut via a `workflow_dispatch` GitHub Actions job
  (Actions → Release → pick patch/minor/major). The workflow bumps
  `VERSION` + `pyproject.toml`, promotes `## [Unreleased]` to a dated
  version header, commits, tags, and publishes the release — no local
  `gh auth` required.

## [0.3.3] — 2026-04-20

### Changed
- **Installer now honors `$CLAUDE_CONFIG_DIR`.** Previously the installer
  hardcoded `~/.claude` for both the hook directory and the settings file,
  so users with a relocated Claude config couldn't install. Resolution order
  is now `$CLAUDE_CONFIG_DIR` → `$HOME/.claude`. If neither is writable and
  stdin is a terminal, the installer prompts for an alternate path; if
  stdin is piped, it bails with guidance to re-run with the env var set.
- Hook commands written into `settings.json` now use absolute paths instead
  of `~/.claude/...`, so they resolve correctly regardless of where the
  config dir lives.
- In-dashboard updates propagate the active config dir to the installer
  subprocess, so updates land in the same location as the original install
  even when the user's shell doesn't export `CLAUDE_CONFIG_DIR`.

## [0.3.2] — 2026-04-20

### Fixed
- **Dashboard now actually restarts after installing an update.** The exit
  signal was being held up by the still-open SSE progress stream — werkzeug
  waited on active request threads before releasing the port, so the next
  `server_check` couldn't bind and bailed. Switched to a hard `os._exit(0)`
  once the "restarting" event has been emitted. Graceful shutdown isn't
  useful here; we're mid-restart by design.
- **No more "two close buttons" in the update modal.** The footer cancel
  button used to sit next to the header ✕ icon during install, reading as
  two dismiss affordances. Rebuilt the modal footer as a proper state
  machine: cancel is hidden during active install/restart, only ever the
  primary button label changes (install → installing… → retry → reload
  page), and cancel is the single "close" label whenever it's visible.
- Timeout handling in the update modal now shows a clear "reload page"
  action instead of a dead-end error message.

## [0.3.1] — 2026-04-20

### Changed
- **Update latency cut to minutes.** Default `update.check_interval_hours`
  dropped 24h → 1h. Frontend now polls every 15 min instead of every 6h,
  and force-checks on tab refocus (`visibilitychange`) so "refocus the
  dashboard" is enough to see a fresh release immediately. New releases
  reach users' dashboards in minutes, not hours.

## [0.3.0] — 2026-04-20

### Added
- **One-click in-dashboard updates** — the update pill now opens a modal
  with release notes and an Install button. Clicking Install downloads the
  release zip from GitHub, extracts it, runs `install.sh`, and restarts the
  dashboard. Live progress (downloading → extracting → installing →
  restarting) is streamed over SSE; the page auto-reloads when the new
  version is up.
- **Kill switch** — new `hook_enabled` setting toggles grammar correction
  without uninstalling. Header shows an amber `⏸ paused` pill when off;
  click to resume. `CLAUDE_GRAMMAR_DISABLED=1` env var overrides (env wins).
- `GET /api/update/apply` + `GET /api/update/progress/<task_id>` — backend
  side of the new update flow.
- `release.sh` — tags + pushes + creates a GitHub Release with notes pulled
  from the matching `## [X.Y.Z]` section of CHANGELOG.md.

### Changed
- **Update source swap** — `updater.py` now reads
  `/repos/<owner>/<repo>/releases/latest` from GitHub instead of a
  self-hosted `manifest.json`. Settings key renamed from
  `update.manifest_url` to `update.github_repo`.
- Docs split — README slimmed to user-facing essentials, technical
  content (architecture, DB schema, settings internals, invariants) moved
  to `CLAUDE.md` for developers.

### Removed
- `publish.sh`, `upgrade.sh`, `package.sh` — obsolete now that updates
  flow through GitHub Releases.

## [0.2.0] — 2026-04-20

### Added
- **EN ↔ UA translation** — header input that auto-detects direction by
  checking for non-ASCII characters. Reuses the Groq fallback chain with a
  dictionary-style prompt and returns translation, synonyms, and 2–3 example
  sentences. Results are cached in a new `translations` table and served
  instantly on repeat lookups.
- **Pink theme** — new `pink` palette alongside monokai, catppuccin, etc.
- **Auto-update check** — dashboard fetches a configurable `manifest.json`
  once per 24h (configurable via `update.check_interval_hours`) and shows an
  "update" pill in the header when a newer version is advertised.
- `GET /api/version` and `GET /api/update/check` endpoints.
- `VERSION` file and `version.py` module as the single source of truth for
  the installed version.
- `publish.sh` — builds the distributable zip plus a `manifest.json` ready
  to upload to your chosen host (GitHub release, S3, plain HTTP, etc.).
- `upgrade.sh` — thin wrapper that downloads the latest zip from the
  manifest and re-runs `install.sh`.
- `data/translations` table + `storage.get_cached_translation`,
  `storage.save_translation`, `storage.get_recent_translations` helpers.

### Removed
- Groq token/quota pill from the top bar. The pill added clutter without
  actionable information — the fallback chain already handles 429s. The
  backend quota persistence and `/api/groq/quota` endpoint remain for anyone
  who wants to build on top of them.

## [0.1.0] — earlier work

Initial release — corrections pipeline, dashboard, reports, dog pen.
