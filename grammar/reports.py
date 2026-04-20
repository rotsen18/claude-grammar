import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

from . import storage
from .settings import REPORTS_CLAUDE_MODEL, REPORTS_CLAUDE_TIMEOUT_SECONDS

TIME_RANGES = ("daily", "weekly", "monthly", "since_last", "all_time")

_SYSTEM_PROMPT = (
    "You are a grammar coach analyzing a non-native English speaker's recent writing "
    "mistakes. You will receive aggregated statistics and a sample of real corrections. "
    "Your job is to produce a focused, practical study report.\n\n"
    "Output rules:\n"
    "1. Return plain text only. Do NOT wrap output in code fences.\n"
    "2. The first line MUST be exactly: TITLE: <3-7 word theme title>\n"
    "   Then one blank line, then markdown content.\n"
    "3. Markdown sections, in this order:\n"
    "   ## Overview — period covered, prompt/correction counts, one-paragraph summary of patterns\n"
    "   ## Top categories — bulleted list of the dominant error categories\n"
    "   ## Priority rules to learn — 3 to 5 specific rules. Each with a short heading, "
    "1-2 sentence rule explanation, and 1-2 real examples drawn from THEIR corrections "
    "(show the wrong phrase then the fix, using blockquote or inline quotes).\n"
    "   ## Wins — categories with few or zero errors (only include if data supports it)\n"
    "   ## Next steps — 2 to 3 concrete actionable suggestions\n"
    "4. Write in second person (\"you\"). Warm, direct tone. No flattery. No filler.\n"
    "5. Keep the whole report under 400 lines of markdown.\n"
    "6. Do NOT echo the raw JSON stats. Synthesize — pull specific examples from the "
    "samples to illustrate rules.\n"
    "7. IGNORE capitalization errors entirely. The user types in informal lowercase "
    "during daily Claude Code usage — they understand capitalization rules but "
    "skip them on purpose. Do NOT mention capitalization in categories, priority "
    "rules, examples, or next steps. Exclude the 'capitalization' category from all "
    "counts and lists. If a correction's only issue is capitalization, skip it.\n"
)


def _resolve_window(time_range: str) -> tuple[str | None, str]:
    now = datetime.now(timezone.utc)
    to_timestamp = now.isoformat()
    if time_range == "daily":
        return (now - timedelta(days=1)).isoformat(), to_timestamp
    if time_range == "weekly":
        return (now - timedelta(days=7)).isoformat(), to_timestamp
    if time_range == "monthly":
        return (now - timedelta(days=30)).isoformat(), to_timestamp
    if time_range == "since_last":
        last = storage.get_last_report_timestamp()
        return last, to_timestamp
    if time_range == "all_time":
        return None, to_timestamp
    raise ValueError(f"Unknown time_range: {time_range}")


def _aggregate(prompts: list[dict]) -> dict:
    prompt_count = len(prompts)
    all_corrections: list[dict] = []
    category_counter: Counter = Counter()
    mistake_counter: Counter = Counter()

    for prompt in prompts:
        for correction in prompt.get("corrections", []):
            all_corrections.append({
                "timestamp": prompt["timestamp"],
                "natural_text": prompt.get("natural_text", ""),
                "corrected_text": prompt.get("corrected_text", ""),
                "category": correction.get("category", ""),
                "original": correction.get("original", ""),
                "replacement": correction.get("replacement", ""),
                "message": correction.get("message", ""),
            })
            category = correction.get("category") or "uncategorized"
            category_counter[category] += 1
            original = (correction.get("original") or "").strip().lower()
            if original:
                mistake_counter[(original, category)] += 1

    top_mistakes = [
        {"phrase": phrase, "category": category, "count": count}
        for (phrase, category), count in mistake_counter.most_common(20)
    ]
    sample = sorted(all_corrections, key=lambda c: c["timestamp"], reverse=True)[:40]

    return {
        "prompt_count": prompt_count,
        "correction_count": len(all_corrections),
        "category_counts": dict(category_counter),
        "top_mistakes": top_mistakes,
        "sample_corrections": sample,
    }


def _build_user_message(time_range: str, from_timestamp: str | None, to_timestamp: str, stats: dict) -> str:
    window_label = {
        "daily": "the last 24 hours",
        "weekly": "the last 7 days",
        "monthly": "the last 30 days",
        "since_last": "the period since the previous report",
        "all_time": "all recorded history",
    }.get(time_range, time_range)

    payload = {
        "time_range": time_range,
        "window_label": window_label,
        "from_timestamp": from_timestamp,
        "to_timestamp": to_timestamp,
        **stats,
    }
    return (
        f"Generate a grammar study report covering {window_label}.\n\n"
        f"Statistics and correction samples (JSON):\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def _invoke_claude(user_message: str) -> str:
    command = ["claude", "-p", "--system-prompt", _SYSTEM_PROMPT]
    if REPORTS_CLAUDE_MODEL:
        command.extend(["--model", REPORTS_CLAUDE_MODEL])
    command.append(user_message)

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=REPORTS_CLAUDE_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude CLI returned {result.returncode}: {result.stderr.strip()[:500]}")
    output = result.stdout.strip()
    if not output:
        raise RuntimeError("claude CLI returned empty output")
    return output


def _parse_output(raw: str) -> tuple[str, str]:
    lines = raw.splitlines()
    title = "Untitled report"
    body_start = 0
    if lines and lines[0].strip().upper().startswith("TITLE:"):
        title = lines[0].split(":", 1)[1].strip() or title
        body_start = 1
        while body_start < len(lines) and not lines[body_start].strip():
            body_start += 1
    content = "\n".join(lines[body_start:]).strip()
    return title, content


def generate_report(time_range: str) -> dict:
    if time_range not in TIME_RANGES:
        raise ValueError(f"Unsupported time_range: {time_range}")

    from_timestamp, to_timestamp = _resolve_window(time_range)
    prompts = storage.get_prompts_in_window(from_timestamp, to_timestamp)
    stats = _aggregate(prompts)

    if stats["prompt_count"] == 0:
        raise RuntimeError("No prompts recorded in the selected time range — nothing to report on.")

    user_message = _build_user_message(time_range, from_timestamp, to_timestamp, stats)

    try:
        raw = _invoke_claude(user_message)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"claude CLI timed out after {REPORTS_CLAUDE_TIMEOUT_SECONDS}s") from exc

    title, content_markdown = _parse_output(raw)
    created_at = datetime.now(timezone.utc).isoformat()

    record = {
        "title": title,
        "time_range": time_range,
        "from_timestamp": from_timestamp,
        "to_timestamp": to_timestamp,
        "created_at": created_at,
        "viewed_at": None,
        "corrector": "claude_cli",
        "model": REPORTS_CLAUDE_MODEL,
        "prompt_count": stats["prompt_count"],
        "correction_count": stats["correction_count"],
        "content_markdown": content_markdown,
    }
    report_id = storage.insert_report(record)
    record["id"] = report_id
    return record


if __name__ == "__main__":
    try:
        result = generate_report(sys.argv[1] if len(sys.argv) > 1 else "all_time")
        print(json.dumps({"id": result["id"], "title": result["title"]}))
    except Exception as error:
        print(f"Failed: {error}", file=sys.stderr)
        sys.exit(1)
