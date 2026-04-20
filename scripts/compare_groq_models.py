#!/usr/bin/env python3
"""Run the same test text through multiple Groq models and write a JSON
comparison report for subagent analysis.

Usage:
    uv run --project ~/.claude/hooks/grammar ~/.claude/hooks/grammar/scripts/compare_groq_models.py

Output:
    ~/.claude/hooks/grammar/data/groq_model_comparison.json

Only chat-capable models from the Groq free tier are included. Audio,
prompt-guard, and router (compound) models are skipped — they can't run
our grammar-correction schema.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from grammar.config import DATA_DIR
from correctors import groq as groq_module
from correctors.groq import GroqCorrector

TEST_TEXT = (
    # 1. Mechanical grammar
    "Yesterday i goes to the shop and buyed two apple. "
    "I was realy happy becuase the apples was freshh. "
    # 2. Collocation traps
    "I want to make a photo of the sunset and do a decision about dinner. "
    "The strong rain yesterday was very bad for my plans. "
    # 3. Phraseology
    "I live in this city since long time and I have 30 years already. "
    "In this moment I am very busy but I will answer you in 5 minutes. "
    # 4. Tense coherence
    "When I was a child I am going to the beach every summer with my family. "
    "If I would have known that earlier, I would came prepared. "
    # 5. Rephrase-worthy
    "It exists many reasons for why the project is currently in the state of delay. "
    "The thing that I want to say you is that we must to take a decision soon. "
    # 6. Brand / technical terms (MUST be preserved)
    "I deploy the Django app to Vercel using pnpm and the build fails on TypeScript errors. "
    # 7. Structure + casual register
    "Walking to the kitchen the apple was eaten by the dog it made me laugh alot. "
    "i dont think thats correct english but lets see what each tool does with it."
)

MODELS = [
    "llama-3.1-8b-instant",
    "llama-3.3-70b-versatile",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "openai/gpt-oss-20b",
    "openai/gpt-oss-120b",
    "qwen/qwen3-32b",
]


def run_model(model: str) -> dict:
    original_model = groq_module.GROQ_MODEL
    groq_module.GROQ_MODEL = model
    corrector = GroqCorrector()

    started = time.time()
    try:
        result = corrector.correct(TEST_TEXT)
        error = None
    except Exception as exc:
        result = None
        error = str(exc)
    elapsed = round(time.time() - started, 3)

    groq_module.GROQ_MODEL = original_model

    if result is None or error:
        return {
            "model": model,
            "elapsed_seconds": elapsed,
            "error": error or "unknown",
            "corrected_text": None,
            "change_count": 0,
            "corrections": [],
        }

    return {
        "model": model,
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
    report = {
        "test_text": TEST_TEXT,
        "test_text_chars": len(TEST_TEXT),
        "results": [run_model(model) for model in MODELS],
    }

    output_path = DATA_DIR / "groq_model_comparison.json"
    output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"Wrote {output_path}")

    for entry in report["results"]:
        if entry["error"]:
            print(f"  {entry['model']}: ERROR ({entry['error']}) in {entry['elapsed_seconds']}s")
        else:
            print(f"  {entry['model']}: {entry['change_count']} changes in {entry['elapsed_seconds']}s")


if __name__ == "__main__":
    main()
