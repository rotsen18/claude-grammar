import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator

from .config import DATA_DIR, DATABASE_FILE

SCHEMA = """
CREATE TABLE IF NOT EXISTS prompts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp         TEXT NOT NULL,
    claude_session_id TEXT NOT NULL DEFAULT '',
    cwd               TEXT NOT NULL DEFAULT '',
    corrector         TEXT NOT NULL,
    had_separator     INTEGER NOT NULL DEFAULT 0,
    original_prompt   TEXT NOT NULL,
    natural_text      TEXT NOT NULL,
    corrected_text    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_prompts_timestamp ON prompts(timestamp);
CREATE INDEX IF NOT EXISTS idx_prompts_original ON prompts(original_prompt, timestamp);

CREATE TABLE IF NOT EXISTS corrections (
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

CREATE INDEX IF NOT EXISTS idx_corrections_prompt ON corrections(prompt_id);
CREATE INDEX IF NOT EXISTS idx_corrections_category ON corrections(category);

CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reports (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    title             TEXT NOT NULL DEFAULT 'Untitled report',
    time_range        TEXT NOT NULL,
    from_timestamp    TEXT,
    to_timestamp      TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    viewed_at         TEXT,
    corrector         TEXT NOT NULL,
    model             TEXT,
    prompt_count      INTEGER NOT NULL DEFAULT 0,
    correction_count  INTEGER NOT NULL DEFAULT 0,
    content_markdown  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reports_created_at ON reports(created_at DESC);

CREATE TABLE IF NOT EXISTS translations (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source_key   TEXT NOT NULL,
    source_text  TEXT NOT NULL,
    source_lang  TEXT NOT NULL,
    target_lang  TEXT NOT NULL,
    translation  TEXT NOT NULL DEFAULT '',
    synonyms     TEXT NOT NULL DEFAULT '[]',
    examples     TEXT NOT NULL DEFAULT '[]',
    notes        TEXT NOT NULL DEFAULT '',
    model        TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    last_used_at TEXT NOT NULL,
    hit_count    INTEGER NOT NULL DEFAULT 1
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_translations_lookup
    ON translations(source_key, source_lang, target_lang);

CREATE INDEX IF NOT EXISTS idx_translations_recent
    ON translations(last_used_at DESC);
"""


def init_db(seed_settings: dict | None = None) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript(SCHEMA)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
    if seed_settings:
        ensure_settings_seed(seed_settings)


def ensure_settings_seed(initial: dict) -> None:
    """Backfill missing keys from `initial` into the DB without overwriting
    existing user-set values. Applies recursively for nested dicts so that
    adding a new sub-key (e.g. ui.chat_format) gets filled in without
    clobbering siblings like ui.theme.
    """
    existing = get_all_settings()
    updates: dict = {}
    for key, default_value in initial.items():
        if key not in existing:
            updates[key] = default_value
            continue
        if isinstance(default_value, dict) and isinstance(existing[key], dict):
            merged = dict(existing[key])
            changed = False
            for sub_key, sub_default in default_value.items():
                if sub_key not in merged:
                    merged[sub_key] = sub_default
                    changed = True
            if changed:
                updates[key] = merged
    if updates:
        set_settings(updates)


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(DATABASE_FILE, timeout=30.0, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        yield connection
    finally:
        connection.close()


def is_recent_duplicate(original_prompt: str, window_seconds: int = 60) -> bool:
    cutoff_iso = (datetime.now(timezone.utc) - timedelta(seconds=window_seconds)).isoformat()
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM prompts WHERE original_prompt = ? AND timestamp > ? LIMIT 1",
            (original_prompt, cutoff_iso),
        ).fetchone()
    return row is not None


def insert_prompt_if_not_duplicate(record: dict, dedupe_window_seconds: int = 60) -> int | None:
    """
    Atomically check for recent duplicate + insert the prompt + its corrections.
    Returns the new prompt id, or None if deduped.
    """
    cutoff_iso = (datetime.now(timezone.utc) - timedelta(seconds=dedupe_window_seconds)).isoformat()
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            dupe = conn.execute(
                "SELECT id FROM prompts WHERE original_prompt = ? AND timestamp > ? LIMIT 1",
                (record["original_prompt"], cutoff_iso),
            ).fetchone()
            if dupe:
                conn.execute("COMMIT")
                return None

            cursor = conn.execute(
                """
                INSERT INTO prompts
                    (timestamp, claude_session_id, cwd, corrector, had_separator,
                     original_prompt, natural_text, corrected_text)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["timestamp"],
                    record.get("session_id", "") or "",
                    record.get("cwd", "") or "",
                    record.get("corrector", "") or "",
                    1 if record.get("had_separator") else 0,
                    record["original_prompt"],
                    record.get("natural_text", "") or "",
                    record.get("corrected_text", "") or "",
                ),
            )
            prompt_id = cursor.lastrowid

            for seq, correction in enumerate(record.get("corrections", [])):
                conn.execute(
                    """
                    INSERT INTO corrections
                        (prompt_id, seq, category, rule, original, replacement, message,
                         offset_val, length_val)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        prompt_id,
                        seq,
                        correction.get("category", "") or "",
                        correction.get("rule", "") or "",
                        correction.get("original", "") or "",
                        correction.get("replacement", "") or "",
                        correction.get("message", "") or "",
                        correction.get("offset"),
                        correction.get("length"),
                    ),
                )

            conn.execute("COMMIT")
            return prompt_id
        except Exception:
            conn.execute("ROLLBACK")
            raise


def latest_id() -> int:
    with _connect() as conn:
        row = conn.execute("SELECT MAX(id) AS id FROM prompts").fetchone()
    return row["id"] or 0


def get_entries_newer_than(last_id: int, limit: int = 200) -> list[dict]:
    with _connect() as conn:
        prompt_rows = conn.execute(
            "SELECT * FROM prompts WHERE id > ? ORDER BY id ASC LIMIT ?",
            (last_id, limit),
        ).fetchall()
        return _hydrate_prompts(conn, prompt_rows)


def get_latest_entries(limit: int = 200) -> list[dict]:
    with _connect() as conn:
        prompt_rows = conn.execute(
            "SELECT * FROM prompts ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return _hydrate_prompts(conn, prompt_rows)


def _hydrate_prompts(conn: sqlite3.Connection, prompt_rows: list[sqlite3.Row]) -> list[dict]:
    if not prompt_rows:
        return []
    prompt_ids = [row["id"] for row in prompt_rows]
    placeholders = ",".join("?" * len(prompt_ids))
    correction_rows = conn.execute(
        f"SELECT * FROM corrections WHERE prompt_id IN ({placeholders}) ORDER BY prompt_id, seq",
        prompt_ids,
    ).fetchall()

    corrections_by_prompt: dict[int, list[dict]] = {pid: [] for pid in prompt_ids}
    for row in correction_rows:
        corrections_by_prompt[row["prompt_id"]].append({
            "original": row["original"],
            "replacement": row["replacement"],
            "rule": row["rule"],
            "message": row["message"],
            "category": row["category"],
            "offset": row["offset_val"],
            "length": row["length_val"],
        })

    entries = []
    for row in prompt_rows:
        entries.append({
            "id": row["id"],
            "timestamp": row["timestamp"],
            "session_id": row["claude_session_id"],
            "cwd": row["cwd"],
            "corrector": row["corrector"],
            "had_separator": bool(row["had_separator"]),
            "original_prompt": row["original_prompt"],
            "natural_text": row["natural_text"],
            "corrected_text": row["corrected_text"],
            "corrections": corrections_by_prompt.get(row["id"], []),
        })
    return entries


def get_stats() -> dict:
    with _connect() as conn:
        total = conn.execute("SELECT COUNT(*) AS c FROM prompts").fetchone()["c"]
        top_categories = conn.execute(
            """
            SELECT category, COUNT(*) AS count
            FROM corrections
            WHERE category != ''
            GROUP BY category
            ORDER BY count DESC
            LIMIT 10
            """
        ).fetchall()
        per_day = conn.execute(
            """
            SELECT substr(timestamp, 1, 10) AS day, COUNT(*) AS count
            FROM prompts
            GROUP BY day
            ORDER BY day
            """
        ).fetchall()
        top_mistakes = conn.execute(
            """
            SELECT lower(original) AS original, category, COUNT(*) AS count
            FROM corrections
            WHERE original != ''
            GROUP BY lower(original), category
            ORDER BY count DESC
            LIMIT 20
            """
        ).fetchall()

    return {
        "total_prompts": total,
        "top_categories": [dict(row) for row in top_categories],
        "per_day": [dict(row) for row in per_day],
        "top_mistakes": [dict(row) for row in top_mistakes],
    }


def count_prompts() -> int:
    with _connect() as conn:
        return conn.execute("SELECT COUNT(*) AS c FROM prompts").fetchone()["c"]


def db_stats() -> dict:
    with _connect() as conn:
        prompt_count = conn.execute("SELECT COUNT(*) AS c FROM prompts").fetchone()["c"]
        correction_count = conn.execute("SELECT COUNT(*) AS c FROM corrections").fetchone()["c"]
        report_count = conn.execute("SELECT COUNT(*) AS c FROM reports").fetchone()["c"]
        bounds = conn.execute(
            "SELECT MIN(timestamp) AS oldest, MAX(timestamp) AS newest FROM prompts"
        ).fetchone()
    try:
        size_bytes = DATABASE_FILE.stat().st_size
    except Exception:
        size_bytes = 0
    return {
        "db_size_bytes": size_bytes,
        "prompt_count": prompt_count,
        "correction_count": correction_count,
        "report_count": report_count,
        "oldest_prompt": bounds["oldest"],
        "newest_prompt": bounds["newest"],
    }


def delete_prompts_older_than(cutoff_iso: str) -> dict:
    with _connect() as conn:
        before = conn.execute("SELECT COUNT(*) AS c FROM prompts").fetchone()["c"]
        conn.execute("DELETE FROM prompts WHERE timestamp < ?", (cutoff_iso,))
        after = conn.execute("SELECT COUNT(*) AS c FROM prompts").fetchone()["c"]
        conn.execute("VACUUM")
    return {"deleted": before - after, "remaining": after}


def get_all_settings() -> dict:
    with _connect() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {row["key"]: json.loads(row["value"]) for row in rows}


def settings_is_empty() -> bool:
    with _connect() as conn:
        row = conn.execute("SELECT 1 FROM settings LIMIT 1").fetchone()
    return row is None


def insert_report(record: dict) -> int:
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO reports
                (title, time_range, from_timestamp, to_timestamp, created_at, viewed_at,
                 corrector, model, prompt_count, correction_count, content_markdown)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["title"],
                record["time_range"],
                record.get("from_timestamp"),
                record["to_timestamp"],
                record["created_at"],
                record.get("viewed_at"),
                record["corrector"],
                record.get("model"),
                record.get("prompt_count", 0),
                record.get("correction_count", 0),
                record["content_markdown"],
            ),
        )
        return cursor.lastrowid


def _report_row_to_dict(row: sqlite3.Row, include_content: bool = True) -> dict:
    result = {
        "id": row["id"],
        "title": row["title"],
        "time_range": row["time_range"],
        "from_timestamp": row["from_timestamp"],
        "to_timestamp": row["to_timestamp"],
        "created_at": row["created_at"],
        "viewed_at": row["viewed_at"],
        "corrector": row["corrector"],
        "model": row["model"],
        "prompt_count": row["prompt_count"],
        "correction_count": row["correction_count"],
    }
    if include_content:
        result["content_markdown"] = row["content_markdown"]
    return result


def get_reports() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM reports ORDER BY created_at DESC"
        ).fetchall()
    return [_report_row_to_dict(row, include_content=False) for row in rows]


def get_report(report_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM reports WHERE id = ?", (report_id,)
        ).fetchone()
    if row is None:
        return None
    return _report_row_to_dict(row, include_content=True)


def delete_report(report_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM reports WHERE id = ?", (report_id,))


def mark_report_viewed(report_id: int) -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute(
            "UPDATE reports SET viewed_at = ? WHERE id = ? AND viewed_at IS NULL",
            (now_iso, report_id),
        )


def get_last_report_timestamp() -> str | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT created_at FROM reports ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    return row["created_at"] if row else None


def get_prompts_in_window(from_timestamp: str | None, to_timestamp: str) -> list[dict]:
    with _connect() as conn:
        if from_timestamp is None:
            prompt_rows = conn.execute(
                "SELECT * FROM prompts WHERE timestamp <= ? ORDER BY id ASC",
                (to_timestamp,),
            ).fetchall()
        else:
            prompt_rows = conn.execute(
                "SELECT * FROM prompts WHERE timestamp > ? AND timestamp <= ? ORDER BY id ASC",
                (from_timestamp, to_timestamp),
            ).fetchall()
        return _hydrate_prompts(conn, prompt_rows)


def _translation_key(text: str) -> str:
    return " ".join(text.strip().lower().split())


def get_cached_translation(source_text: str, source_lang: str, target_lang: str) -> dict | None:
    key = _translation_key(source_text)
    if not key:
        return None
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM translations
            WHERE source_key = ? AND source_lang = ? AND target_lang = ?
            """,
            (key, source_lang, target_lang),
        ).fetchone()
        if row is None:
            return None
        now_iso = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE translations SET hit_count = hit_count + 1, last_used_at = ? WHERE id = ?",
            (now_iso, row["id"]),
        )
    return _translation_row_to_dict(row)


def save_translation(record: dict) -> int:
    key = _translation_key(record["source_text"])
    now_iso = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO translations
                (source_key, source_text, source_lang, target_lang, translation,
                 synonyms, examples, notes, model, created_at, last_used_at, hit_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(source_key, source_lang, target_lang) DO UPDATE SET
                translation  = excluded.translation,
                synonyms     = excluded.synonyms,
                examples     = excluded.examples,
                notes        = excluded.notes,
                model        = excluded.model,
                last_used_at = excluded.last_used_at,
                hit_count    = translations.hit_count + 1
            """,
            (
                key,
                record["source_text"],
                record["source_lang"],
                record["target_lang"],
                record.get("translation", ""),
                json.dumps(record.get("synonyms", []), ensure_ascii=False),
                json.dumps(record.get("examples", []), ensure_ascii=False),
                record.get("notes", ""),
                record.get("model", ""),
                now_iso,
                now_iso,
            ),
        )
        return cursor.lastrowid or 0


def get_recent_translations(limit: int = 20) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM translations ORDER BY last_used_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_translation_row_to_dict(row) for row in rows]


def _translation_row_to_dict(row: sqlite3.Row) -> dict:
    def _parse_json(raw: str, fallback):
        try:
            return json.loads(raw) if raw else fallback
        except Exception:
            return fallback

    return {
        "id": row["id"],
        "source_text": row["source_text"],
        "source_lang": row["source_lang"],
        "target_lang": row["target_lang"],
        "translation": row["translation"],
        "synonyms": _parse_json(row["synonyms"], []),
        "examples": _parse_json(row["examples"], []),
        "notes": row["notes"],
        "model": row["model"],
        "created_at": row["created_at"],
        "last_used_at": row["last_used_at"],
        "hit_count": row["hit_count"],
    }


def set_settings(values: dict) -> None:
    timestamp = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute("BEGIN")
        try:
            for key, value in values.items():
                conn.execute(
                    """
                    INSERT INTO settings (key, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        updated_at = excluded.updated_at
                    """,
                    (key, json.dumps(value, ensure_ascii=False), timestamp),
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
