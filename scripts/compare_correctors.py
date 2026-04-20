#!/usr/bin/env python3
"""Run the same test text through all three correctors and write a JSON
comparison report for subagent analysis.

Usage:
    uv run --project ~/.claude/hooks/grammar ~/.claude/hooks/grammar/scripts/compare_correctors.py

Output:
    ~/.claude/hooks/grammar/data/corrector_comparison.json

The test text deliberately mixes grammar, spelling, meaning-blur, and
structure errors so each corrector is exercised across its weak spots.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import DATA_DIR
from correctors.claude_cli import ClaudeCLICorrector
from correctors.groq import GroqCorrector
from correctors.languagetool import LanguageToolCorrector

TEST_TEXT = (
    # 1. Mechanical grammar (subject-verb, tense, spelling, articles)
    "Yesterday i goes to the shop and buyed two apple. "
    "I was realy happy becuase the apples was freshh. "
    # 2. Collocation traps (grammatically legal, non-native choice)
    "I want to make a photo of the sunset and do a decision about dinner. "
    "The strong rain yesterday was very bad for my plans. "
    # 3. Phraseology (translated-from-another-language feel)
    "I live in this city since long time and I have 30 years already. "
    "In this moment I am very busy but I will answer you in 5 minutes. "
    # 4. Tense coherence (legal tense, wrong choice for the situation)
    "When I was a child I am going to the beach every summer with my family. "
    "If I would have known that earlier, I would came prepared. "
    # 5. Rephrase-worthy (unnatural word order / translated phrasing)
    "It exists many reasons for why the project is currently in the state of delay. "
    "The thing that I want to say you is that we must to take a decision soon. "
    # 6. Brand / technical terms (MUST be preserved)
    "I deploy the Django app to Vercel using pnpm and the build fails on TypeScript errors. "
    # 7. Structure (comma splice + run-on + misplaced modifier)
    "Walking to the kitchen the apple was eaten by the dog it made me laugh alot "
    "then i decided to wrote this sentence for test the corrector which is really good. "
    # 8. Capitalization + contraction + casual register (don't formalize)
    "i dont think thats correct english but lets see what each tool does with it."
)


def run_corrector(name: str, corrector):
    started = time.time()
    try:
        result = corrector.correct(TEST_TEXT)
        error = None
    except Exception as exc:
        result = None
        error = str(exc)
    elapsed = round(time.time() - started, 3)

    if result is None or error:
        return {
            "name": name,
            "elapsed_seconds": elapsed,
            "error": error or "unknown",
            "corrected_text": None,
            "change_count": 0,
            "corrections": [],
        }

    return {
        "name": name,
        "elapsed_seconds": elapsed,
        "error": None,
        "corrected_text": result.corrected_text,
        "change_count": len(result.corrections),
        "corrections": [
            {
                "category": c.category or "",
                "rule": c.rule or "",
                "original": c.original,
                "replacement": c.replacement,
                "message": c.message or "",
            }
            for c in result.corrections
        ],
    }


def main() -> None:
    correctors = [
        ("claude_cli", ClaudeCLICorrector()),
        ("groq", GroqCorrector()),
        ("languagetool", LanguageToolCorrector()),
    ]

    report = {
        "test_text": TEST_TEXT,
        "test_text_chars": len(TEST_TEXT),
        "results": [run_corrector(name, inst) for name, inst in correctors],
    }

    output_path = DATA_DIR / "corrector_comparison.json"
    output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"Wrote {output_path}")

    for entry in report["results"]:
        if entry["error"]:
            print(f"  {entry['name']}: ERROR ({entry['error']}) in {entry['elapsed_seconds']}s")
        else:
            print(f"  {entry['name']}: {entry['change_count']} changes in {entry['elapsed_seconds']}s")


if __name__ == "__main__":
    main()
