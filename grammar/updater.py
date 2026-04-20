"""Update checker backed by GitHub Releases.

Calls `GET /repos/{owner}/{repo}/releases/latest` and compares the
`tag_name` (leading `v` stripped) with the installed version. Results are
cached for `update.check_interval_hours` to avoid hammering the API.

Config keys under `update` in settings:
    github_repo           — "owner/repo" to check. Empty disables checks.
    check_interval_hours  — cache TTL, default 24.

The apply flow in `dashboard/app.py` consumes:
    .latest        — tag minus leading "v"
    .download_url  — archive URL for the tag (source zip)
    .release_notes — markdown body of the release
    .release_url   — human-readable release page
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

import requests

from .config import DATA_DIR
from .hook_log import get_logger
from .version import get_version, is_newer

log = get_logger()

_CACHE_FILE = DATA_DIR / "update_check.json"
_FETCH_TIMEOUT_SECONDS = 8
_GITHUB_API = "https://api.github.com"


@dataclass
class UpdateStatus:
    current: str
    latest: str = ""
    update_available: bool = False
    checked_at: str = ""
    download_url: str = ""
    release_notes: str = ""
    release_url: str = ""
    published_at: str = ""
    error: str = ""
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "current": self.current,
            "latest": self.latest,
            "update_available": self.update_available,
            "checked_at": self.checked_at,
            "download_url": self.download_url,
            "release_notes": self.release_notes,
            "release_url": self.release_url,
            "published_at": self.published_at,
            "error": self.error,
        }


def _strip_v(tag: str) -> str:
    return tag.lstrip("vV").strip()


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


def _is_cache_fresh(cache: dict, interval_hours: float) -> bool:
    timestamp = cache.get("checked_at")
    if not timestamp:
        return False
    try:
        when = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return False
    age_hours = (datetime.now(timezone.utc) - when).total_seconds() / 3600
    return age_hours < max(0.01, interval_hours)


def _status_from_cache(cache: dict, current: str) -> UpdateStatus:
    latest = cache.get("latest", "")
    return UpdateStatus(
        current=current,
        latest=latest,
        # Re-evaluate on every read so a version bump locally (e.g. just-
        # installed a newer release) correctly clears update_available
        # without waiting for the cache to expire.
        update_available=bool(latest) and is_newer(latest, current),
        checked_at=cache.get("checked_at", ""),
        download_url=cache.get("download_url", ""),
        release_notes=cache.get("release_notes", ""),
        release_url=cache.get("release_url", ""),
        published_at=cache.get("published_at", ""),
    )


def check_for_update(github_repo: str, interval_hours: float = 24, force: bool = False) -> UpdateStatus:
    current = get_version()
    cache = _load_cache()

    if not github_repo:
        return UpdateStatus(current=current, error="github_repo is empty")

    if "/" not in github_repo:
        return UpdateStatus(current=current, error="github_repo must be 'owner/repo'")

    if not force and _is_cache_fresh(cache, interval_hours) and cache.get("repo") == github_repo:
        return _status_from_cache(cache, current)

    url = f"{_GITHUB_API}/repos/{github_repo}/releases/latest"
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        response = requests.get(url, headers=headers, timeout=_FETCH_TIMEOUT_SECONDS)
    except Exception as exc:
        log.warning("Update check failed: %s", exc)
        return UpdateStatus(current=current, error=f"request failed: {exc}")

    if response.status_code == 404:
        return UpdateStatus(current=current, error="no releases published yet")

    if response.status_code != 200:
        message = f"HTTP {response.status_code}"
        log.warning("Update check returned %s: %s", message, response.text[:200])
        return UpdateStatus(current=current, error=message)

    try:
        release = response.json() or {}
    except ValueError as exc:
        return UpdateStatus(current=current, error=f"invalid JSON: {exc}")

    tag_name = (release.get("tag_name") or "").strip()
    latest = _strip_v(tag_name)
    if not latest:
        return UpdateStatus(current=current, error="release missing tag_name")

    # Tag archive URL — stable, public, auth-free for public repos.
    download_url = f"https://github.com/{github_repo}/archive/refs/tags/{tag_name}.zip"

    status = UpdateStatus(
        current=current,
        latest=latest,
        update_available=is_newer(latest, current),
        checked_at=datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        download_url=download_url,
        release_notes=(release.get("body") or "").strip(),
        release_url=release.get("html_url", ""),
        published_at=release.get("published_at", ""),
    )

    _save_cache({
        "repo": github_repo,
        "latest": status.latest,
        "update_available": status.update_available,
        "checked_at": status.checked_at,
        "download_url": status.download_url,
        "release_notes": status.release_notes,
        "release_url": status.release_url,
        "published_at": status.published_at,
    })

    return status
