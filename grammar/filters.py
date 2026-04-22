"""Post-correction filters — drop excluded categories and preserved tokens.

Two-stage enforcement pattern used by every corrector:
1. The prompt snippet (see `prompt_snippet`) is appended to the system prompt
   so the model has a chance to self-suppress unwanted corrections.
2. `apply` runs after the corrector returns — it is authoritative. Even if
   the model ignores the prompt, the resulting correction never reaches
   storage or the dashboard.
"""
from __future__ import annotations

from correctors.base import Correction, CorrectionResult

_STRIP_CHARS = ",.!?;:\"'()[]{}"


def load() -> tuple[set[str], set[str]]:
    """Read filters fresh from the DB. Called on every correction so that
    live edits from the dashboard take effect without a process restart."""
    from . import storage

    settings = storage.get_all_settings()
    cfg = settings.get("filters") or {}
    excluded = {
        str(item).strip().lower()
        for item in (cfg.get("excluded_categories") or [])
        if str(item).strip()
    }
    preserved = {
        token
        for token in (_normalize_token(str(item)) for item in (cfg.get("preserved_tokens") or []))
        if token
    }
    return excluded, preserved


def prompt_snippet(excluded: set[str], preserved: set[str]) -> str:
    """Short directive to append to the system prompt. Empty string when both
    filters are empty so we don't bloat the prompt with no-ops."""
    if not excluded and not preserved:
        return ""
    lines = ["\n\nUSER FILTERS (MANDATORY — override any earlier instruction):"]
    if excluded:
        cats = ", ".join(sorted(excluded))
        lines.append(
            f"- Do NOT emit corrections in these categories: {cats}. "
            f"If the only issue with a span is in one of those categories, leave it alone."
        )
    if preserved:
        tokens = ", ".join(f'"{token}"' for token in sorted(preserved))
        lines.append(
            f"- Preserve these tokens verbatim — they are intentional shorthand, "
            f"not typos: {tokens}. Never correct, expand, or capitalize them."
        )
    return "\n".join(lines)


def apply(
    natural_text: str,
    result: CorrectionResult,
    excluded: set[str],
    preserved: set[str],
) -> CorrectionResult:
    """Drop filtered corrections and rebuild `corrected_text` from survivors.

    When no filter would apply, returns `result` unchanged (cheap fast path).
    When corrections are dropped, rebuilds `corrected_text` by applying the
    surviving corrections to `natural_text` — so the displayed text matches
    exactly the corrections the user sees."""
    if not excluded and not preserved:
        return result
    if not result.corrections:
        return result

    kept: list[Correction] = []
    dropped = False
    for correction in result.corrections:
        if _should_drop(correction, excluded, preserved):
            dropped = True
            continue
        kept.append(correction)

    if not dropped:
        return result

    rebuilt = _rebuild(natural_text, kept) if kept else natural_text
    return CorrectionResult(
        original_text=result.original_text,
        corrected_text=rebuilt,
        corrections=kept,
        corrector_name=result.corrector_name,
    )


def _should_drop(correction: Correction, excluded: set[str], preserved: set[str]) -> bool:
    if excluded:
        category = (correction.category or "").strip().lower()
        if category and category in excluded:
            return True
    if preserved:
        if _normalize_token(correction.original) in preserved:
            return True
    return False


def _normalize_token(token: str) -> str:
    return (token or "").strip().strip(_STRIP_CHARS).lower()


def _rebuild(natural_text: str, corrections: list[Correction]) -> str:
    """Apply each surviving correction to `natural_text` in reverse offset
    order so earlier edits don't shift later indices. Falls back to a single
    literal replace when the stored offset doesn't line up."""
    ordered = sorted(corrections, key=lambda c: (c.offset, -c.length), reverse=True)
    result = natural_text
    for correction in ordered:
        start = correction.offset
        end = start + correction.length
        if (
            0 <= start <= len(result)
            and end <= len(result)
            and result[start:end] == correction.original
        ):
            result = result[:start] + correction.replacement + result[end:]
        elif correction.original and correction.original in result:
            result = result.replace(correction.original, correction.replacement, 1)
    return result
