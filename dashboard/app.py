import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests
from flask import Flask, Response, jsonify, render_template, request

import reports as reports_module
import storage
import translator
import updater
from config import DATA_DIR, ENV_FILE
from hook_log import LOG_FILE, get_logger, tail_log
from settings import DASHBOARD_HOST, DASHBOARD_PORT, GROQ_FALLBACK_MODELS, GROQ_MODEL
from version import get_version

log = get_logger()

LOG_CURSOR_FILE = DATA_DIR / "logs_last_seen_offset"
GROQ_QUOTA_FILE = DATA_DIR / "groq_quota.json"

POLL_INTERVAL_SECONDS = 0.5
HEARTBEAT_INTERVAL_SECONDS = 15

SETTINGS_WRITABLE_KEYS = {
    "corrector",
    "hook_enabled",
    "bypass_marker",
    "separator",
    "min_natural_text_length",
    "languagetool",
    "claude_cli",
    "groq",
    "ui",
    "update",
    "translation",
}

import queue as _queue

# Broadcast channel for hook-pending notifications. Each active SSE stream
# holds its own queue; a POST to /api/hook/pending fans out to all of them
# so every connected dashboard sees the signal (queue.Queue would give it
# to only one subscriber).
_pending_subscribers: "list[_queue.Queue[dict]]" = []
_pending_subscribers_lock = threading.Lock()


def _register_pending_subscriber() -> "_queue.Queue[dict]":
    sub: "_queue.Queue[dict]" = _queue.Queue()
    with _pending_subscribers_lock:
        _pending_subscribers.append(sub)
    return sub


def _unregister_pending_subscriber(sub: "_queue.Queue[dict]") -> None:
    with _pending_subscribers_lock:
        try:
            _pending_subscribers.remove(sub)
        except ValueError:
            pass


def _broadcast_pending(payload: dict) -> None:
    with _pending_subscribers_lock:
        subs = list(_pending_subscribers)
    for sub in subs:
        try:
            sub.put_nowait(payload)
        except Exception:
            pass


# ── Update worker state ─────────────────────────────────────────────
# A single in-flight update at a time. `events` is a replay log so a late
# SSE subscriber still gets the earlier phases. `subscribers` are live
# queues that receive new events as they happen.
_update_state: dict = {
    "lock": threading.Lock(),
    "active": False,
    "task_id": None,
    "events": [],
    "subscribers": [],
    "terminal": False,
}


def _emit_update_event(phase: str, **extra) -> None:
    event = {"phase": phase, "ts": time.time(), **extra}
    with _update_state["lock"]:
        _update_state["events"].append(event)
        subs = list(_update_state["subscribers"])
        if phase in ("done", "error"):
            _update_state["terminal"] = True
    for sub in subs:
        try:
            sub.put_nowait(event)
        except Exception:
            pass


def _run_update(task_id: str) -> None:
    """Background worker. Never raises — always emits a terminal event."""
    try:
        settings = _load_effective_settings()
        update_cfg = settings.get("update") or {}
        github_repo = (update_cfg.get("github_repo") or "").strip()
        status = updater.check_for_update(
            github_repo,
            interval_hours=int(update_cfg.get("check_interval_hours") or 24),
            force=True,
        )
        if status.error:
            _emit_update_event("error", message=status.error)
            return
        if not status.update_available:
            _emit_update_event("done", already_latest=True, version=status.current)
            return

        _emit_update_event("downloading", version=status.latest, url=status.download_url)
        zip_path = _download_release_zip(status.download_url)

        _emit_update_event("extracting")
        install_root = _extract_zip(zip_path)

        _emit_update_event("installing", install_root=str(install_root))
        _run_installer(install_root)

        _emit_update_event("restarting")
        # Last event before we orchestrate our own suicide. Give the SSE
        # stream a brief head start so clients receive "restarting" before
        # the socket drops.
        _spawn_server_check(startup_delay_seconds=2.0)
        threading.Timer(0.5, lambda: _emit_update_event("done", version=status.latest)).start()
        _schedule_exit(delay=1.5)
    except _UpdateError as exc:
        _emit_update_event("error", message=str(exc))
    except Exception as exc:  # noqa: BLE001 — top-level worker must not propagate
        log.exception("Update worker crashed")
        _emit_update_event("error", message=f"{type(exc).__name__}: {exc}")
    finally:
        with _update_state["lock"]:
            _update_state["active"] = False


class _UpdateError(Exception):
    """Raised inside the update worker to surface a clean message to the UI."""


def _download_release_zip(url: str) -> Path:
    tmp = tempfile.NamedTemporaryFile(prefix="claude-grammar-", suffix=".zip", delete=False)
    path = Path(tmp.name)
    tmp.close()
    try:
        with requests.get(url, stream=True, timeout=60, allow_redirects=True) as response:
            response.raise_for_status()
            total = int(response.headers.get("content-length") or 0)
            downloaded = 0
            last_emit = 0.0
            with path.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=65536):
                    if not chunk:
                        continue
                    handle.write(chunk)
                    downloaded += len(chunk)
                    now = time.time()
                    # Throttle progress emissions to ~4 per second.
                    if now - last_emit > 0.25:
                        _emit_update_event(
                            "downloading",
                            bytes_downloaded=downloaded,
                            bytes_total=total,
                            fraction=(downloaded / total) if total else None,
                        )
                        last_emit = now
    except requests.HTTPError as exc:
        raise _UpdateError(f"download failed: HTTP {exc.response.status_code}") from exc
    except requests.RequestException as exc:
        raise _UpdateError(f"download failed: {exc}") from exc
    return path


def _extract_zip(zip_path: Path) -> Path:
    import zipfile

    dest = Path(tempfile.mkdtemp(prefix="claude-grammar-unpack-"))
    try:
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(dest)
    except zipfile.BadZipFile as exc:
        raise _UpdateError(f"downloaded file is not a valid zip: {exc}") from exc

    # GitHub zipballs contain exactly one top-level directory.
    entries = [p for p in dest.iterdir() if p.is_dir()]
    if len(entries) != 1:
        raise _UpdateError(f"expected 1 top-level dir in zip, got {len(entries)}")
    return entries[0]


def _run_installer(install_root: Path) -> None:
    script = install_root / "install.sh"
    if not script.exists():
        raise _UpdateError("install.sh missing from downloaded release")
    try:
        result = subprocess.run(
            ["bash", str(script)],
            cwd=str(install_root),
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        raise _UpdateError("installer timed out after 5 minutes") from None
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip()[-400:]
        raise _UpdateError(f"installer exited {result.returncode}: {tail}")


CORRECTOR_OPTIONS = ["groq", "claude_cli", "languagetool"]
THEME_OPTIONS = [
    "phosphor",
    "amber",
    "vapor",
    "mono",
    "matrix",
    "solarized",
    "dracula",
    "nord",
    "gruvbox",
    "synthwave",
    "tokyo-night",
    "catppuccin",
    "github-dark",
    "monokai",
    "pink",
]
CHAT_FORMAT_OPTIONS = ["corrected", "diff", "diff-annotated", "full", "inline", "annotated"]

app = Flask(__name__)


@app.route("/health")
def health() -> Response:
    return jsonify({"status": "ok"})


@app.route("/")
def index() -> str:
    return render_template("index.html")


@app.route("/api/corrections")
def corrections() -> Response:
    limit = int(request.args.get("limit", 200))
    return jsonify(storage.get_latest_entries(limit))


@app.route("/api/corrections/stream")
def stream() -> Response:
    def generate():
        pending_sub = _register_pending_subscriber()
        try:
            last_id = storage.latest_id()
            try:
                existing_reports = storage.get_reports()
                last_report_id = max((r["id"] for r in existing_reports), default=0)
            except Exception:
                last_report_id = 0
            last_heartbeat = time.time()
            while True:
                try:
                    new_entries = storage.get_entries_newer_than(last_id)
                except Exception as exc:
                    print(f"SSE poll failed: {exc}", file=sys.stderr)
                    new_entries = []

                new_reports: list[dict] = []
                try:
                    all_reports = storage.get_reports()
                    new_reports = [r for r in all_reports if r["id"] > last_report_id]
                except Exception as exc:
                    print(f"SSE report poll failed: {exc}", file=sys.stderr)

                pending_items: list[dict] = []
                while True:
                    try:
                        pending_items.append(pending_sub.get_nowait())
                    except _queue.Empty:
                        break

                if new_entries or new_reports or pending_items:
                    for entry in new_entries:
                        yield f"data: {json.dumps(entry)}\n\n"
                        last_id = max(last_id, entry["id"])
                    for report in new_reports:
                        payload = {"id": report["id"], "title": report.get("title", "")}
                        yield f"event: report\ndata: {json.dumps(payload)}\n\n"
                        last_report_id = max(last_report_id, report["id"])
                    for pending in pending_items:
                        yield f"event: pending\ndata: {json.dumps(pending)}\n\n"
                    last_heartbeat = time.time()
                    continue

                now = time.time()
                if now - last_heartbeat > HEARTBEAT_INTERVAL_SECONDS:
                    # Named event (not an SSE comment) so the client's JS can
                    # observe it and detect silent network stalls.
                    yield f"event: heartbeat\ndata: {int(now * 1000)}\n\n"
                    last_heartbeat = now

                time.sleep(POLL_INTERVAL_SECONDS)
        finally:
            _unregister_pending_subscriber(pending_sub)

    return Response(generate(), mimetype="text/event-stream")


@app.route("/api/stats")
def stats() -> Response:
    return jsonify(storage.get_stats())


@app.route("/api/hook/pending", methods=["POST"])
def api_hook_pending() -> Response:
    try:
        body = request.get_json(force=True) or {}
    except Exception:
        return jsonify({"ok": False}), 400
    _broadcast_pending({
        "session_id": body.get("session_id", "") or "",
        "cwd": body.get("cwd", "") or "",
    })
    return jsonify({"ok": True})


@app.route("/api/settings", methods=["GET"])
def get_settings_endpoint() -> Response:
    current = _load_effective_settings()
    masked = _mask_secrets(current)
    env_state = _env_key_state(current)
    return jsonify({
        "settings": masked,
        "corrector_options": CORRECTOR_OPTIONS,
        "theme_options": THEME_OPTIONS,
        "chat_format_options": CHAT_FORMAT_OPTIONS,
        "translation_languages": translator.SUPPORTED_TARGET_LANGUAGES,
        "env": env_state,
    })


@app.route("/api/settings", methods=["POST"])
def update_settings_endpoint() -> Response:
    try:
        incoming = request.get_json(force=True) or {}
    except Exception:
        return jsonify({"ok": False, "error": "invalid JSON body"}), 400

    current = _load_effective_settings()
    updated = deepcopy(current)

    patch = incoming.get("settings") or {}
    for key, value in patch.items():
        if key not in SETTINGS_WRITABLE_KEYS:
            continue
        if isinstance(value, dict) and isinstance(updated.get(key), dict):
            merged = dict(updated[key])
            for sub_key, sub_value in value.items():
                if sub_key == "api_key":
                    continue
                merged[sub_key] = sub_value
            updated[key] = merged
        else:
            updated[key] = value

    if updated.get("corrector") not in CORRECTOR_OPTIONS:
        updated["corrector"] = current.get("corrector", "groq")

    updated["hook_enabled"] = bool(updated.get("hook_enabled", True))

    ui = updated.get("ui", {}) or {}
    if ui.get("theme") == "cyan":
        ui["theme"] = "vapor"
    if ui.get("theme") not in THEME_OPTIONS:
        ui["theme"] = "phosphor"
    if ui.get("chat_format") not in CHAT_FORMAT_OPTIONS:
        ui["chat_format"] = "full"
    ui["dogs_enabled"] = bool(ui.get("dogs_enabled", True))
    updated["ui"] = ui

    translation_cfg = updated.get("translation") or {}
    valid_codes = {entry["code"] for entry in translator.SUPPORTED_TARGET_LANGUAGES}
    if translation_cfg.get("target_language") not in valid_codes:
        translation_cfg["target_language"] = translator.DEFAULT_TARGET
    updated["translation"] = translation_cfg

    storage.set_settings(updated)

    new_groq_key = (incoming.get("groq_api_key") or "").strip()
    if new_groq_key:
        _write_env_key("GROQ_API_KEY", new_groq_key)

    refreshed = _load_effective_settings()
    return jsonify({
        "ok": True,
        "settings": _mask_secrets(refreshed),
        "env": _env_key_state(refreshed),
    })


@app.route("/reports")
def reports_list_page() -> str:
    return render_template("reports_list.html")


@app.route("/reports/<int:report_id>")
def report_detail_page(report_id: int) -> Response | str:
    report = storage.get_report(report_id)
    if report is None:
        return Response("Report not found", status=404)
    storage.mark_report_viewed(report_id)
    return render_template("reports_detail.html", report_id=report_id)


@app.route("/api/reports", methods=["GET"])
def api_reports_list() -> Response:
    return jsonify(storage.get_reports())


@app.route("/api/reports/<int:report_id>", methods=["GET"])
def api_reports_detail(report_id: int) -> Response:
    report = storage.get_report(report_id)
    if report is None:
        return jsonify({"ok": False, "error": "not found"}), 404
    return jsonify(report)


@app.route("/api/reports/<int:report_id>", methods=["DELETE"])
def api_reports_delete(report_id: int) -> Response:
    storage.delete_report(report_id)
    return jsonify({"ok": True})


@app.route("/api/reports/generate", methods=["POST"])
def api_reports_generate() -> Response:
    try:
        body = request.get_json(force=True) or {}
    except Exception:
        return jsonify({"ok": False, "error": "invalid JSON body"}), 400

    time_range = (body.get("time_range") or "").strip()
    if time_range not in reports_module.TIME_RANGES:
        return jsonify({"ok": False, "error": f"invalid time_range: {time_range}"}), 400

    try:
        record = reports_module.generate_report(time_range)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500

    return jsonify({
        "ok": True,
        "id": record["id"],
        "title": record["title"],
        "url": f"/reports/{record['id']}",
    })


@app.route("/api/db/stats", methods=["GET"])
def api_db_stats() -> Response:
    return jsonify(storage.db_stats())


@app.route("/api/db/cleanup", methods=["POST"])
def api_db_cleanup() -> Response:
    try:
        body = request.get_json(force=True) or {}
    except Exception:
        return jsonify({"ok": False, "error": "invalid JSON body"}), 400

    days = body.get("before_days")
    try:
        days_int = int(days)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "before_days must be an integer"}), 400

    if days_int < 1 or days_int > 3650:
        return jsonify({"ok": False, "error": "before_days must be between 1 and 3650"}), 400

    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    cutoff = (_dt.now(_tz.utc) - _td(days=days_int)).isoformat()
    result = storage.delete_prompts_older_than(cutoff)
    return jsonify({"ok": True, "cutoff": cutoff, **result})


@app.route("/logs")
def logs_page() -> str:
    return render_template("logs.html")


@app.route("/api/logs", methods=["GET"])
def api_logs() -> Response:
    try:
        lines = int(request.args.get("lines", "200"))
    except ValueError:
        lines = 200
    lines = max(10, min(lines, 5000))
    return jsonify({
        "path": str(LOG_FILE),
        "exists": LOG_FILE.exists(),
        "lines": lines,
        "content": tail_log(lines),
    })


@app.route("/api/logs", methods=["DELETE"])
def api_logs_clear() -> Response:
    try:
        if LOG_FILE.exists():
            LOG_FILE.write_text("")
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/logs/errors", methods=["GET"])
def api_logs_error_count() -> Response:
    count = _count_unread_log_errors()
    return jsonify({"unread_errors": count})


@app.route("/api/version", methods=["GET"])
def api_version() -> Response:
    return jsonify({"version": get_version()})


@app.route("/api/update/check", methods=["GET", "POST"])
def api_update_check() -> Response:
    settings = _load_effective_settings()
    update_cfg = settings.get("update") or {}
    github_repo = (update_cfg.get("github_repo") or "").strip()
    interval_hours = int(update_cfg.get("check_interval_hours") or 24)
    force = request.args.get("force", "").lower() in {"1", "true", "yes"} or request.method == "POST"
    status = updater.check_for_update(github_repo, interval_hours=interval_hours, force=force)
    return jsonify(status.to_dict())


@app.route("/api/update/apply", methods=["POST"])
def api_update_apply() -> Response:
    """Kick off a background update run. Returns a task_id; the caller
    subscribes to /api/update/progress/<id> for live phase events.

    Only one update at a time. 409 if one is already in-flight.
    """
    with _update_state["lock"]:
        if _update_state["active"]:
            return jsonify({"ok": False, "error": "update already in progress"}), 409

        task_id = f"u{int(time.time())}"
        _update_state["active"] = True
        _update_state["task_id"] = task_id
        _update_state["events"] = []
        _update_state["subscribers"] = []
        _update_state["terminal"] = False

    thread = threading.Thread(target=_run_update, args=(task_id,), daemon=True)
    thread.start()
    return jsonify({"ok": True, "task_id": task_id}), 202


@app.route("/api/update/progress/<task_id>")
def api_update_progress(task_id: str) -> Response:
    """SSE stream: replays past events for this task, then streams new
    ones until the task reaches a terminal phase (done|error).
    """
    if _update_state["task_id"] != task_id:
        return Response("unknown task_id\n", status=404)

    def generate():
        sub: "_queue.Queue[dict]" = _queue.Queue()
        with _update_state["lock"]:
            past = list(_update_state["events"])
            terminal_already = _update_state["terminal"]
            if not terminal_already:
                _update_state["subscribers"].append(sub)

        for event in past:
            yield f"data: {json.dumps(event)}\n\n"

        if terminal_already:
            return

        try:
            while True:
                try:
                    event = sub.get(timeout=15)
                except _queue.Empty:
                    yield ": heartbeat\n\n"
                    continue
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("phase") in ("done", "error"):
                    break
        finally:
            with _update_state["lock"]:
                try:
                    _update_state["subscribers"].remove(sub)
                except ValueError:
                    pass

    return Response(generate(), mimetype="text/event-stream")


@app.route("/api/translate", methods=["POST"])
def api_translate() -> Response:
    try:
        body = request.get_json(force=True) or {}
    except Exception:
        return jsonify({"ok": False, "error": "invalid JSON body"}), 400

    text = (body.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "empty input"}), 400

    result = translator.translate(text)
    if result.get("error"):
        return jsonify({"ok": False, "error": result["error"]}), 502

    return jsonify({"ok": True, "result": result})


@app.route("/api/translations/recent", methods=["GET"])
def api_translations_recent() -> Response:
    try:
        limit = int(request.args.get("limit", "15"))
    except ValueError:
        limit = 15
    limit = max(1, min(limit, 50))
    return jsonify({"translations": storage.get_recent_translations(limit)})


@app.route("/api/groq/quota")
def api_groq_quota() -> Response:
    if not GROQ_QUOTA_FILE.exists():
        return jsonify({"chain": [], "summary": None})
    try:
        data = json.loads(GROQ_QUOTA_FILE.read_text())
    except Exception:
        return jsonify({"chain": [], "summary": None})
    data.pop("_last_model", None)

    chain_order = [GROQ_MODEL, *GROQ_FALLBACK_MODELS]
    chain = []
    worst_percent = None
    for model in chain_order:
        raw = data.get(model)
        if not raw:
            continue
        summary = _summarize_groq_quota(raw)
        if summary is None:
            continue
        summary["model"] = model
        summary["raw"] = raw
        chain.append(summary)
        if worst_percent is None or summary["percent"] < worst_percent:
            worst_percent = summary["percent"]

    overall = None
    if worst_percent is not None:
        overall = {
            "percent": worst_percent,
            "state": "red" if worst_percent < 5 else "amber" if worst_percent < 20 else "green",
        }

    return jsonify({"chain": chain, "summary": overall})


def _summarize_groq_quota(raw: dict) -> dict | None:
    """Reduce one model's raw headers to {percent, bucket, window, reset}.

    `bucket` = "T" (tokens) or "R" (requests) — whichever is tighter.
    `window` = "M" (reset < 2min, i.e. per-minute) or "D" (reset ≥ 2min, daily).
    """
    r_pct = _percent(raw.get("remaining_requests"), raw.get("limit_requests"))
    t_pct = _percent(raw.get("remaining_tokens"), raw.get("limit_tokens"))
    candidates = []
    if r_pct is not None:
        candidates.append(("R", r_pct, raw.get("reset_requests")))
    if t_pct is not None:
        candidates.append(("T", t_pct, raw.get("reset_tokens")))
    if not candidates:
        return None
    bucket, percent, reset = min(candidates, key=lambda item: item[1])
    reset_seconds = _parse_reset_seconds(reset) if reset else None
    window = "M" if reset_seconds is not None and reset_seconds < 120 else "D"
    return {
        "percent": round(percent, 1),
        "bucket": bucket,
        "window": window,
        "label": f"{bucket}P{window}",
        "reset": reset,
        "reset_seconds": reset_seconds,
    }


def _percent(remaining: str | None, limit: str | None) -> float | None:
    try:
        remaining_value = float(remaining) if remaining is not None else None
        limit_value = float(limit) if limit is not None else None
    except (TypeError, ValueError):
        return None
    if remaining_value is None or limit_value is None or limit_value <= 0:
        return None
    return max(0.0, min(100.0, remaining_value / limit_value * 100))


def _parse_reset_seconds(value: str) -> float | None:
    """Parse Groq reset strings like '3.54s', '12m57.599s', '1h30m'."""
    if not value:
        return None
    total = 0.0
    found = False
    for amount, unit in re.findall(r"(\d+(?:\.\d+)?)([hms])", value):
        found = True
        scale = {"h": 3600, "m": 60, "s": 1}[unit]
        total += float(amount) * scale
    return total if found else None


@app.route("/api/logs/errors/ack", methods=["POST"])
def api_logs_ack_errors() -> Response:
    try:
        offset = LOG_FILE.stat().st_size if LOG_FILE.exists() else 0
        LOG_CURSOR_FILE.write_text(str(offset))
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


def _count_unread_log_errors() -> int:
    if not LOG_FILE.exists():
        return 0
    try:
        last_seen = int(LOG_CURSOR_FILE.read_text().strip()) if LOG_CURSOR_FILE.exists() else 0
    except Exception:
        last_seen = 0
    try:
        size = LOG_FILE.stat().st_size
    except OSError:
        return 0
    # If the log was truncated/rotated, the stored offset becomes larger than
    # the file — reset and count from the start.
    if last_seen > size:
        last_seen = 0
    if last_seen == size:
        return 0
    try:
        with LOG_FILE.open("rb") as fh:
            fh.seek(last_seen)
            chunk = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return 0
    return sum(1 for line in chunk.splitlines() if "[ERROR]" in line)


@app.route("/api/server/shutdown", methods=["POST"])
def api_server_shutdown() -> Response:
    _schedule_exit(delay=0.25)
    return jsonify({"ok": True})


@app.route("/api/server/restart", methods=["POST"])
def api_server_restart() -> Response:
    _spawn_server_check(startup_delay_seconds=2.5)
    _schedule_exit(delay=0.5)
    return jsonify({"ok": True})


def _schedule_exit(delay: float) -> None:
    def _die() -> None:
        try:
            os.kill(os.getpid(), signal.SIGTERM)
        except Exception:
            os._exit(0)

    timer = threading.Timer(delay, _die)
    timer.daemon = True
    timer.start()


def _spawn_server_check(startup_delay_seconds: float = 0.0) -> None:
    project_dir = Path(__file__).resolve().parent.parent
    log_handle = open(project_dir / "data" / "dashboard.log", "a")
    cmd = f"sleep {startup_delay_seconds} && exec uv run --project {project_dir} {project_dir / 'server_check.py'}"
    subprocess.Popen(
        ["sh", "-c", cmd],
        cwd=str(project_dir),
        stdout=log_handle,
        stderr=log_handle,
        start_new_session=True,
    )


def _load_effective_settings() -> dict:
    settings = storage.get_all_settings()
    ui = settings.get("ui") or {}
    if ui.get("theme") == "cyan":
        ui["theme"] = "vapor"
        settings["ui"] = ui
    return settings


def _mask_secrets(settings: dict) -> dict:
    safe = json.loads(json.dumps(settings))
    groq = safe.get("groq") or {}
    key = groq.get("api_key") or ""
    groq["api_key"] = _mask(key)
    safe["groq"] = groq
    return safe


def _mask(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:4]}···{key[-4:]}"


def _env_key_state(settings: dict) -> dict:
    env_name = (settings.get("groq") or {}).get("api_key_env", "GROQ_API_KEY")
    env_value = os.environ.get(env_name, "")
    file_value = _read_env_value(env_name)
    override_value = (settings.get("groq") or {}).get("api_key") or ""

    source = None
    masked = ""
    if env_value and not file_value:
        source = "env"
        masked = _mask(env_value)
    elif file_value:
        source = ".env file"
        masked = _mask(file_value)
    elif override_value:
        source = "settings.json"
        masked = _mask(override_value)

    return {
        "groq_key_present": bool(source),
        "groq_key_source": source,
        "groq_key_masked": masked,
        "groq_env_var_name": env_name,
    }


def _read_env_value(key: str) -> str:
    if not ENV_FILE.exists():
        return ""
    try:
        for line in ENV_FILE.read_text().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            name, _, raw = stripped.partition("=")
            if name.strip() == key:
                return raw.strip().strip('"').strip("'")
    except Exception:
        pass
    return ""


def _write_env_key(key: str, value: str) -> None:
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing: list[str] = []
    if ENV_FILE.exists():
        existing = ENV_FILE.read_text().splitlines()

    updated_lines: list[str] = []
    found = False
    for line in existing:
        stripped = line.strip()
        if stripped.startswith(f"{key}=") or stripped.startswith(f"{key} ="):
            updated_lines.append(f"{key}={value}")
            found = True
        else:
            updated_lines.append(line)
    if not found:
        updated_lines.append(f"{key}={value}")

    fd, tmp_path = tempfile.mkstemp(dir=ENV_FILE.parent, prefix=".env-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write("\n".join(updated_lines).rstrip("\n") + "\n")
        os.replace(tmp_path, ENV_FILE)
        os.chmod(ENV_FILE, 0o600)
    except Exception:
        if Path(tmp_path).exists():
            Path(tmp_path).unlink()
        raise


def main() -> None:
    storage.init_db()
    app.run(host=DASHBOARD_HOST, port=DASHBOARD_PORT, debug=False, threaded=True)


if __name__ == "__main__":
    main()
