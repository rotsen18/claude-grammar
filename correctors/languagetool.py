import time

import requests

from settings import LANGUAGETOOL_API_URL, LANGUAGETOOL_LANGUAGE
from correctors.base import BaseCorrector, Correction, CorrectionResult
from hook_log import get_logger

log = get_logger()

TIMEOUT_SECONDS = 5
MIN_REQUEST_INTERVAL = 3.1

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

        elapsed = time.time() - _last_request_time
        if elapsed < MIN_REQUEST_INTERVAL:
            time.sleep(MIN_REQUEST_INTERVAL - elapsed)

        try:
            response = requests.post(
                LANGUAGETOOL_API_URL,
                data={
                    "text": text,
                    "language": LANGUAGETOOL_LANGUAGE,
                },
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

            corrected_text = corrected_text[:offset] + replacement + corrected_text[offset + length :]

            corrections.append(
                Correction(
                    original=original_fragment,
                    replacement=replacement,
                    rule=match.get("rule", {}).get("id", ""),
                    offset=offset,
                    length=length,
                    message=match.get("message", ""),
                )
            )

        corrections.reverse()

        return CorrectionResult(
            original_text=text,
            corrected_text=corrected_text,
            corrections=corrections,
            corrector_name=self.name,
        )
