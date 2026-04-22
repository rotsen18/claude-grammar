import time

import requests

from grammar import filters as filters_module
from grammar.settings import LANGUAGETOOL_API_URL, LANGUAGETOOL_LANGUAGE
from correctors.base import BaseCorrector, Correction, CorrectionResult
from grammar.hook_log import get_logger

log = get_logger()

TIMEOUT_SECONDS = 5
MIN_REQUEST_INTERVAL = 3.1

# Map our canonical category names (as used in excluded_categories settings)
# onto LanguageTool's native category IDs. When a user excludes a category we
# know how to express server-side, we hand it to LT as `disabledCategories` so
# the API skips those rules entirely. Everything else falls through to the
# post-filter below.
_CANONICAL_TO_LT_CATEGORY = {
    "capitalization": "CASING",
    "spelling": "TYPOS",
    "punctuation": "PUNCTUATION",
    "style": "STYLE",
    "word_choice": "COLLOQUIALISMS,REDUNDANCY,STYLE",
}

# Reverse direction — LT rule category ID → our canonical name — so stored
# corrections carry categories that match what the user selects in the UI.
_LT_CATEGORY_TO_CANONICAL = {
    "CASING": "capitalization",
    "TYPOS": "spelling",
    "PUNCTUATION": "punctuation",
    "STYLE": "style",
    "COLLOQUIALISMS": "word_choice",
    "REDUNDANCY": "word_choice",
    "GRAMMAR": "grammar",
}

_last_request_time: float = 0.0


class LanguageToolCorrector(BaseCorrector):
    name = "languagetool"

    def correct(self, text: str) -> CorrectionResult:
        global _last_request_time

        empty_result = CorrectionResult(
            original_text=text,
            corrected_text=text,
            corrections=[],
            corrector_name=self.name,
        )

        excluded, preserved = filters_module.load()

        elapsed = time.time() - _last_request_time
        if elapsed < MIN_REQUEST_INTERVAL:
            time.sleep(MIN_REQUEST_INTERVAL - elapsed)

        payload_data = {
            "text": text,
            "language": LANGUAGETOOL_LANGUAGE,
        }
        disabled = _disabled_categories_for(excluded)
        if disabled:
            payload_data["disabledCategories"] = disabled

        try:
            response = requests.post(
                LANGUAGETOOL_API_URL,
                data=payload_data,
                timeout=TIMEOUT_SECONDS,
            )
            _last_request_time = time.time()
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            log.error("LanguageTool request failed: %s", exc, exc_info=True)
            return empty_result

        matches = payload.get("matches", [])
        if not matches:
            return empty_result

        corrections: list[Correction] = []
        corrected_text = text
        matches_sorted = sorted(matches, key=lambda m: m.get("offset", 0), reverse=True)

        for match in matches_sorted:
            replacements = match.get("replacements", [])
            if not replacements:
                continue
            offset = match.get("offset", 0)
            length = match.get("length", 0)
            replacement = replacements[0].get("value", "")
            original_fragment = text[offset : offset + length]
            rule = match.get("rule", {}) or {}
            lt_category_id = (rule.get("category") or {}).get("id", "")

            corrected_text = corrected_text[:offset] + replacement + corrected_text[offset + length :]

            corrections.append(
                Correction(
                    original=original_fragment,
                    replacement=replacement,
                    rule=rule.get("id", ""),
                    offset=offset,
                    length=length,
                    message=match.get("message", ""),
                    category=_LT_CATEGORY_TO_CANONICAL.get(lt_category_id, ""),
                )
            )

        corrections.reverse()

        result = CorrectionResult(
            original_text=text,
            corrected_text=corrected_text,
            corrections=corrections,
            corrector_name=self.name,
        )
        return filters_module.apply(text, result, excluded, preserved)


def _disabled_categories_for(excluded: set[str]) -> str:
    """Translate user-configured excluded categories into LT's native
    `disabledCategories` param (comma-separated LT category IDs). Categories
    we can't express server-side are left to the post-filter."""
    mapped: list[str] = []
    for canonical in excluded:
        lt_id = _CANONICAL_TO_LT_CATEGORY.get(canonical)
        if lt_id:
            mapped.append(lt_id)
    seen: set[str] = set()
    deduped: list[str] = []
    for entry in ",".join(mapped).split(","):
        entry = entry.strip()
        if entry and entry not in seen:
            seen.add(entry)
            deduped.append(entry)
    return ",".join(deduped)
