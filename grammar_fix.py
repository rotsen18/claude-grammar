#!/usr/bin/env python3
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import storage
from settings import BYPASS_MARKER, CORRECTOR, MIN_NATURAL_TEXT_LENGTH
from correctors.base import BaseCorrector
from correctors.claude_cli import ClaudeCLICorrector
from correctors.groq import GroqCorrector
from correctors.languagetool import LanguageToolCorrector
from hook_log import get_logger
from parser import parse_prompt

DEDUPE_WINDOW_SECONDS = 60

log = get_logger()


def _ping_dashboard_pending(session_id: str, cwd: str) -> None:
    try:
        import requests  # noqa: PLC0415

        from settings import DASHBOARD_HOST, DASHBOARD_PORT  # noqa: PLC0415

        requests.post(
            f"http://{DASHBOARD_HOST}:{DASHBOARD_PORT}/api/hook/pending",
            json={"session_id": session_id, "cwd": cwd},
            timeout=2.0,
        )
    except Exception as exc:
        log.debug("Pending ping failed (dashboard likely down): %s", exc)


def get_corrector(name: str) -> BaseCorrector:
    if name == "languagetool":
        return LanguageToolCorrector()
    if name == "claude_cli":
        return ClaudeCLICorrector()
    if name == "groq":
        return GroqCorrector()
    raise ValueError(f"Unknown corrector: {name}")


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except Exception as exc:
        log.error("Failed to read hook input: %s", exc, exc_info=True)
        sys.exit(0)

    prompt = input_data.get("prompt", "")
    if not prompt.strip():
        sys.exit(0)

    if prompt.rstrip().endswith(BYPASS_MARKER):
        sys.exit(0)

    try:
        storage.init_db()
    except Exception as exc:
        log.error("Failed to init DB: %s", exc, exc_info=True)
        sys.exit(0)

    if storage.is_recent_duplicate(prompt, DEDUPE_WINDOW_SECONDS):
        sys.exit(0)

    try:
        parsed = parse_prompt(prompt)
    except Exception as exc:
        log.error("Parser failed: %s", exc, exc_info=True)
        sys.exit(0)

    if len(parsed.natural_text.strip()) < MIN_NATURAL_TEXT_LENGTH:
        sys.exit(0)

    _ping_dashboard_pending(
        input_data.get("session_id", "") or "",
        input_data.get("cwd", "") or "",
    )

    log.info("Running corrector=%s text_len=%d", CORRECTOR, len(parsed.natural_text))

    try:
        corrector = get_corrector(CORRECTOR)
        result = corrector.correct(parsed.natural_text)
    except Exception as exc:
        log.error("Corrector %s failed: %s", CORRECTOR, exc, exc_info=True)
        sys.exit(0)

    log.info("Corrector=%s returned %d corrections", CORRECTOR, len(result.corrections))

    if not result.corrections:
        sys.exit(0)

    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_id": input_data.get("session_id", ""),
        "cwd": input_data.get("cwd", ""),
        "original_prompt": parsed.original_prompt,
        "natural_text": parsed.natural_text,
        "corrected_text": result.corrected_text,
        "corrections": [
            {
                "original": c.original,
                "replacement": c.replacement,
                "rule": c.rule,
                "offset": c.offset,
                "length": c.length,
                "message": c.message,
                "category": c.category,
            }
            for c in result.corrections
        ],
        "corrector": result.corrector_name,
        "had_separator": parsed.had_separator,
    }

    try:
        storage.insert_prompt_if_not_duplicate(record, DEDUPE_WINDOW_SECONDS)
    except Exception as exc:
        log.error("Failed to persist correction record: %s", exc, exc_info=True)

    sys.exit(0)


if __name__ == "__main__":
    main()
