"""Update checker.

Fetches a small `manifest.json` from a user-configured URL and compares the
advertised version with the installed one. Results are cached in a local file
so repeated checks within `check_interval_hours` don't hit the network.

Manifest schema (all fields optional except `version`):
    {
      "version":     "0.3.0",
      "download_url": "https://…/grammar-hook-0.3.0.zip",
      "changelog_url": "https://…/CHANGELOG.md",
      "release_notes": "- Added translation …",
      "minimum_python": "3.11",
      "sha256":        "<hex digest of the zip>",
      "released_at":   "2026-04-22"
    }

The config knob `update.manifest_url` is empty by default; nothing is fetched
unless the user opts in.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

import requests

from config import DATA_DIR
from hook_log import get_logger
from version import get_version, is_newer

log = get_logger()

_CACHE_FILE = DATA_DIR / "update_check.json"
_FETCH_TIMEOUT_SECONDS = 8


@dataclass
class UpdateStatus:
    current: str
    latest: str = ""
    update_available: bool = False
    checked_at: str = ""
    manifest: dict = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "current": self.current,
            "latest": self.latest,
            "update_available": self.update_available,
            "checked_at": self.checked_at,
            "manifest": self.manifest,
            "error": self.error,
        }


def _load_cache() -> dict:
    if not _CACHE_FILE.exists():
        return {}
    try:
        return json.loads(_CACHE_FILE.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _save_cache(data: dict) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _CACHE_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(_CACHE_FILE)
    except OSError as exc:
        log.debug("updater cache write failed: %s", exc)


def _is_cache_fresh(cache: dict, interval_hours: int) -> bool:
    timestamp = cache.get("checked_at")
    if not timestamp:
        return False
    try:
        when = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return False
    age_hours = (datetime.now(timezone.utc) - when).total_seconds() / 3600
    return age_hours < max(0.01, interval_hours)


def check_for_update(manifest_url: str, interval_hours: int = 24, force: bool = False) -> UpdateStatus:
    current = get_version()
    cache = _load_cache()

    if not manifest_url:
        return UpdateStatus(current=current, error="manifest_url is empty")

    if not force and _is_cache_fresh(cache, interval_hours):
        cached_status = UpdateStatus(
            current=current,
            latest=cache.get("latest", ""),
            update_available=bool(cache.get("update_available")),
            checked_at=cache.get("checked_at", ""),
            manifest=cache.get("manifest") or {},
        )
        return cached_status

    try:
        response = requests.get(manifest_url, timeout=_FETCH_TIMEOUT_SECONDS)
    except Exception as exc:
        log.warning("Update check failed: %s", exc)
        return UpdateStatus(current=current, error=f"request failed: {exc}")

    if response.status_code != 200:
        message = f"HTTP {response.status_code}"
        log.warning("Update check returned %s: %s", message, response.text[:200])
        return UpdateStatus(current=current, error=message)

    try:
        manifest = response.json() or {}
    except ValueError as exc:
        log.warning("Update manifest parse failed: %s", exc)
        return UpdateStatus(current=current, error="invalid manifest JSON")

    latest = (manifest.get("version") or "").strip()
    if not latest:
        return UpdateStatus(current=current, error="manifest missing `version`")

    status = UpdateStatus(
        current=current,
        latest=latest,
        update_available=is_newer(latest, current),
        checked_at=datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        manifest=manifest,
    )

    _save_cache({
        "latest": status.latest,
        "update_available": status.update_available,
        "checked_at": status.checked_at,
        "manifest": status.manifest,
    })

    return status
