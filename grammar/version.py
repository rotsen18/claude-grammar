"""Single source of truth for the installed hook version.

Read order:
  1. VERSION file next to this module (plain text, e.g. `0.2.0`).
  2. `pyproject.toml` `[project].version` (installer-agnostic fallback).
  3. `"0.0.0"` if nothing is found.

Both files are shipped in every release so either lookup succeeds offline.
"""
from __future__ import annotations

import re
from pathlib import Path

# VERSION / pyproject.toml live at the project root — one level up from the package.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_VERSION_FILE = _PROJECT_ROOT / "VERSION"
_PYPROJECT = _PROJECT_ROOT / "pyproject.toml"


def _read_version_file() -> str | None:
    if not _VERSION_FILE.exists():
        return None
    try:
        value = _VERSION_FILE.read_text(encoding="utf-8").strip()
        return value or None
    except OSError:
        return None


def _read_pyproject_version() -> str | None:
    if not _PYPROJECT.exists():
        return None
    try:
        contents = _PYPROJECT.read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', contents)
    return match.group(1) if match else None


def get_version() -> str:
    return _read_version_file() or _read_pyproject_version() or "0.0.0"


def parse_version(raw: str) -> tuple[int, int, int, str]:
    """Parse a semver-ish version into a sortable tuple.

    Tolerates pre-release suffixes (`0.3.0-beta.1`) but sorts them ahead of
    the clean version (so `0.3.0-beta.1 < 0.3.0`), matching semver intent.
    """
    core, _, pre = raw.strip().partition("-")
    parts = core.split(".")
    major, minor, patch = 0, 0, 0
    try:
        major = int(parts[0]) if len(parts) > 0 else 0
        minor = int(parts[1]) if len(parts) > 1 else 0
        patch = int(parts[2]) if len(parts) > 2 else 0
    except ValueError:
        pass
    # Empty `pre` sorts AFTER any non-empty pre-release, so use \uffff as sentinel.
    pre_key = pre or "\uffff"
    return (major, minor, patch, pre_key)


def is_newer(candidate: str, baseline: str) -> bool:
    """True if `candidate` represents a strictly newer version than `baseline`."""
    return parse_version(candidate) > parse_version(baseline)


__version__ = get_version()
