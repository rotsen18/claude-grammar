import re
from dataclasses import dataclass

SEPARATOR = ",,"

SHELL_COMMAND_PREFIXES = (
    "cd ",
    "ls ",
    "npm ",
    "pnpm ",
    "yarn ",
    "pip ",
    "poetry ",
    "git ",
    "docker ",
    "curl ",
    "wget ",
    "make ",
    "cargo ",
    "go ",
    "python ",
    "node ",
    "uv ",
    "ruff ",
    "pytest ",
    "psql ",
    "redis-cli ",
    "kubectl ",
    "brew ",
    "ssh ",
    "scp ",
    "rm ",
    "mv ",
    "cp ",
    "mkdir ",
    "touch ",
    "chmod ",
    "chown ",
    "export ",
    "source ",
    "sudo ",
    "grep ",
    "find ",
    "awk ",
    "sed ",
    "cat ",
    "tail ",
    "head ",
    "tar ",
    "zip ",
    "unzip ",
)

TRACEBACK_PATTERNS = [
    re.compile(r"^\s*Traceback \(most recent call last\):"),
    re.compile(r'^\s*File\s+"[^"]+",\s+line\s+\d+'),
    re.compile(r"^\s*at\s+\S+\s*\([^)]+:\d+:\d+\)"),
    re.compile(r"^\s*at\s+[\w.<>]+\s*\([^)]*\)?\s*$"),
    re.compile(r"^\s*[A-Z][a-zA-Z]*Error:\s*"),
    re.compile(r"^\s*[A-Z][a-zA-Z]*Exception:\s*"),
]

LOG_LINE_PATTERNS = [
    re.compile(r"^\s*\[\d{4}-\d{2}-\d{2}[\sT]\d{2}:\d{2}:\d{2}"),
    re.compile(r"^\s*(INFO|DEBUG|WARNING|WARN|ERROR|TRACE|FATAL|CRITICAL):\s"),
    re.compile(r"^\s*\d{4}-\d{2}-\d{2}[\sT]\d{2}:\d{2}:\d{2}[,.]\d+"),
]

FILE_PATH_PATTERN = re.compile(r"^\s*(\.{0,2}/|~/|[A-Z]:\\|/)[^\s]+\s*$")
URL_PATTERN = re.compile(r"^\s*https?://\S+\s*$")

IMPORT_PATTERNS = [
    re.compile(r"^\s*import\s+\S+"),
    re.compile(r"^\s*from\s+\S+\s+import\s+"),
    re.compile(r"^\s*(const|let|var)\s+\w+\s*=\s*require\("),
    re.compile(r"^\s*(const|let|var)\s+\{[^}]+\}\s*=\s*require\("),
]

JSON_LIKE_PATTERNS = [
    re.compile(r"^\s*[\{\[]"),
    re.compile(r"^\s*[\]\}],?\s*$"),
    re.compile(r'^\s*"[^"]+"\s*:\s*'),
]

SHELL_PREFIX_PATTERN = re.compile(r"^\s*[\$>#]\s+\S")
SHEBANG_PATTERN = re.compile(r"^\s*#!")
BASE64_PATTERN = re.compile(r"^\s*[A-Za-z0-9+/]{40,}={0,2}\s*$")
HEX_DUMP_PATTERN = re.compile(r"^\s*[0-9a-fA-F]{8,}\s+[0-9a-fA-F]{2}")

CODE_FENCE_PATTERN = re.compile(r"^\s*```")


@dataclass
class ParseResult:
    natural_text: str
    skipped_lines: list[str]
    had_separator: bool
    original_prompt: str


def _starts_with_shell_command(line: str) -> bool:
    stripped = line.lstrip()
    return any(stripped.startswith(prefix) for prefix in SHELL_COMMAND_PREFIXES)


def _is_code_heavy(line: str) -> bool:
    stripped = line.strip()
    if len(stripped) < 8:
        return False
    special_chars = sum(1 for ch in stripped if ch in "{}[]();=<>|&")
    alpha_chars = sum(1 for ch in stripped if ch.isalpha())
    if alpha_chars == 0:
        return True
    ratio = special_chars / max(alpha_chars, 1)
    return ratio > 0.4


def _is_technical_line(line: str) -> bool:
    if not line.strip():
        return False

    for pattern in TRACEBACK_PATTERNS:
        if pattern.match(line):
            return True
    for pattern in LOG_LINE_PATTERNS:
        if pattern.match(line):
            return True
    for pattern in IMPORT_PATTERNS:
        if pattern.match(line):
            return True
    for pattern in JSON_LIKE_PATTERNS:
        if pattern.match(line):
            return True

    if FILE_PATH_PATTERN.match(line):
        return True
    if URL_PATTERN.match(line):
        return True
    if SHELL_PREFIX_PATTERN.match(line):
        return True
    if SHEBANG_PATTERN.match(line):
        return True
    if BASE64_PATTERN.match(line):
        return True
    if HEX_DUMP_PATTERN.match(line):
        return True
    if _starts_with_shell_command(line):
        return True
    if _is_code_heavy(line):
        return True

    return False


def _strip_inline_code(text: str) -> str:
    return re.sub(r"`[^`]*`", "", text)


def _split_on_separator(prompt: str) -> tuple[str, bool]:
    lines = prompt.splitlines()
    for index, line in enumerate(lines):
        if line.strip() == SEPARATOR:
            return "\n".join(lines[:index]), True
    return prompt, False


def _remove_code_fences(lines: list[str]) -> tuple[list[str], list[str]]:
    kept: list[str] = []
    skipped: list[str] = []
    inside_fence = False
    for line in lines:
        if CODE_FENCE_PATTERN.match(line):
            skipped.append(line)
            inside_fence = not inside_fence
            continue
        if inside_fence:
            skipped.append(line)
            continue
        kept.append(line)
    return kept, skipped


def parse_prompt(prompt: str) -> ParseResult:
    original_prompt = prompt
    working_text, had_separator = _split_on_separator(prompt)

    lines = working_text.splitlines()
    lines, fence_skipped = _remove_code_fences(lines)

    natural_lines: list[str] = []
    skipped_lines: list[str] = list(fence_skipped)

    for line in lines:
        if _is_technical_line(line):
            skipped_lines.append(line)
            continue
        cleaned = _strip_inline_code(line)
        natural_lines.append(cleaned)

    natural_text = "\n".join(natural_lines).strip()

    return ParseResult(
        natural_text=natural_text,
        skipped_lines=skipped_lines,
        had_separator=had_separator,
        original_prompt=original_prompt,
    )
