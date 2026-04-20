"""Runtime settings module.

Imports `config` (paths + defaults) and `storage` (DB layer), initializes
the database with the built-in defaults, then exposes the effective settings
as module-level constants for import throughout the rest of the codebase.

This module is the single place where `storage.init_db(INITIAL_DEFAULTS)` is
called at import time — splitting it out of `config.py` avoids a circular
import (storage → config → storage).
"""
import storage
from config import INITIAL_DEFAULTS

storage.init_db(INITIAL_DEFAULTS)
SETTINGS = storage.get_all_settings()


CORRECTOR = SETTINGS["corrector"]
HOOK_ENABLED = SETTINGS.get("hook_enabled", True)
MIN_NATURAL_TEXT_LENGTH = SETTINGS["min_natural_text_length"]
SEPARATOR = SETTINGS["separator"]
BYPASS_MARKER = SETTINGS["bypass_marker"]

DASHBOARD_HOST = SETTINGS["dashboard"]["host"]
DASHBOARD_PORT = SETTINGS["dashboard"]["port"]

UI_THEME = SETTINGS["ui"]["theme"]

LANGUAGETOOL_API_URL = "https://api.languagetool.org/v2/check"
LANGUAGETOOL_LANGUAGE = SETTINGS["languagetool"]["language"]

CLAUDE_CLI_MODEL = SETTINGS["claude_cli"]["model"]
CLAUDE_CLI_TIMEOUT_SECONDS = SETTINGS["claude_cli"]["timeout_seconds"]
CLAUDE_CLI_SYSTEM_PROMPT = SETTINGS["claude_cli"]["system_prompt"]

REPORTS_CLAUDE_MODEL = SETTINGS["reports"]["claude_model"]
REPORTS_CLAUDE_TIMEOUT_SECONDS = SETTINGS["reports"]["claude_timeout_seconds"]
REPORTS_KEEP_LATEST = SETTINGS["reports"]["keep_latest"]

GROQ_BASE_URL = SETTINGS["groq"]["base_url"]
GROQ_MODEL = SETTINGS["groq"]["model"]
GROQ_FALLBACK_MODELS = SETTINGS["groq"].get("fallback_models", [])
GROQ_TIMEOUT_SECONDS = SETTINGS["groq"]["timeout_seconds"]
GROQ_API_KEY_ENV = SETTINGS["groq"]["api_key_env"]
GROQ_API_KEY_OVERRIDE = SETTINGS["groq"]["api_key"]
GROQ_TEMPERATURE = SETTINGS["groq"]["temperature"]
GROQ_USE_JSON_SCHEMA = SETTINGS["groq"]["use_json_schema"]
GROQ_SYSTEM_PROMPT = SETTINGS["groq"]["system_prompt"]
