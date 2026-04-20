# Changelog

All notable changes to the grammar hook are documented here. This project
follows [semantic versioning](https://semver.org/): `MAJOR.MINOR.PATCH`.

- **MAJOR** — breaking changes (config format, schema, hook entry points).
- **MINOR** — new features, backwards compatible.
- **PATCH** — bug fixes and small polish.

## [Unreleased]

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
